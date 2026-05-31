#!/usr/bin/env python3
"""
Afspraak Watcher — multi-site appointment availability checker.

Reads YAML config files from sites/ and checks each one.
Sends Telegram notification only when an earlier-than-before date appears.
State stored in state/<site>.json for deduplication.

Designed to run in GitHub Actions (cron) but works locally too.

Environment variables required:
  TELEGRAM_BOT_TOKEN — bot token from BotFather
  TELEGRAM_CHAT_ID   — your chat id
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests
import yaml
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

ROOT = Path(__file__).parent
SITES_DIR = ROOT / "sites"
STATE_DIR = ROOT / "state"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

DUTCH_MONTHS = {
    "januari": 1, "februari": 2, "maart": 3, "april": 4,
    "mei": 5, "juni": 6, "juli": 7, "augustus": 8,
    "september": 9, "oktober": 10, "november": 11, "december": 12,
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("watcher")


# -----------------------------------------------------------------------------
# Date parsing
# -----------------------------------------------------------------------------

_DUTCH_DATE_REGEX = re.compile(
    r"\b(?:[A-Za-z]{2,3},?\s+)?(\d{1,2})\s+("
    + "|".join(DUTCH_MONTHS.keys())
    + r")\s+(\d{4})\b",
    re.IGNORECASE,
)


def parse_dutch_date(text: str) -> date | None:
    """Parse strings like 'Wo, 13 mei 2026' or '13 mei 2026' to a date."""
    if not text:
        return None
    m = _DUTCH_DATE_REGEX.search(text)
    if not m:
        return None
    day, month_name, year = m.group(1), m.group(2), m.group(3)
    month = DUTCH_MONTHS.get(month_name.lower())
    if not month:
        return None
    try:
        return date(int(year), month, int(day))
    except ValueError:
        return None


def format_dutch_date(d: date) -> str:
    """Format a date as a user-friendly Dutch string."""
    inv = {v: k for k, v in DUTCH_MONTHS.items()}
    return f"{d.day} {inv[d.month]} {d.year}"


# -----------------------------------------------------------------------------
# Site config + state
# -----------------------------------------------------------------------------

@dataclass
class SiteConfig:
    name: str
    url: str
    steps: list[dict[str, Any]]
    extract: list[dict[str, Any]]
    notify_if_before: date
    message_template: str
    max_retries: int = 3
    quiet_when_unchanged: bool = True

    @classmethod
    def from_yaml(cls, path: Path) -> "SiteConfig":
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        deadline_raw = data["notify_if_before"]
        if isinstance(deadline_raw, str):
            deadline = date.fromisoformat(deadline_raw)
        elif isinstance(deadline_raw, date):
            deadline = deadline_raw
        else:
            raise ValueError(f"notify_if_before must be a date string, got {deadline_raw!r}")
        return cls(
            name=data["name"],
            url=data["url"],
            steps=data["steps"],
            extract=data["extract"],
            notify_if_before=deadline,
            message_template=data["message_template"],
            max_retries=int(data.get("max_retries", 3)),
            quiet_when_unchanged=bool(data.get("quiet_when_unchanged", True)),
        )


@dataclass
class SiteState:
    """Persisted across runs to avoid spamming notifications."""
    last_seen_date: date | None = None
    last_notified_date: date | None = None
    last_check_utc: str = ""
    consecutive_failures: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_seen_date": self.last_seen_date.isoformat() if self.last_seen_date else None,
            "last_notified_date": self.last_notified_date.isoformat() if self.last_notified_date else None,
            "last_check_utc": self.last_check_utc,
            "consecutive_failures": self.consecutive_failures,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SiteState":
        return cls(
            last_seen_date=date.fromisoformat(d["last_seen_date"]) if d.get("last_seen_date") else None,
            last_notified_date=date.fromisoformat(d["last_notified_date"]) if d.get("last_notified_date") else None,
            last_check_utc=d.get("last_check_utc", ""),
            consecutive_failures=int(d.get("consecutive_failures", 0)),
        )


def load_state(site_id: str) -> SiteState:
    path = STATE_DIR / f"{site_id}.json"
    if not path.exists():
        return SiteState()
    try:
        return SiteState.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except Exception as e:
        log.warning(f"Failed to load state for {site_id}: {e}; starting fresh")
        return SiteState()


def save_state(site_id: str, state: SiteState) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / f"{site_id}.json"
    path.write_text(json.dumps(state.to_dict(), indent=2) + "\n", encoding="utf-8")


# -----------------------------------------------------------------------------
# Telegram
# -----------------------------------------------------------------------------

def send_telegram(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set — skipping send")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=15,
        )
        r.raise_for_status()
        log.info("Telegram notification sent")
        return True
    except Exception as e:
        log.error(f"Telegram send failed: {e}")
        return False


# -----------------------------------------------------------------------------
# Browser actions (driven by YAML)
# -----------------------------------------------------------------------------

async def run_steps(page: Page, site: SiteConfig) -> None:
    for i, step in enumerate(site.steps):
        action = step.get("action")
        log.debug(f"  step[{i}] {action} {step}")
        if action == "goto":
            await page.goto(
                site.url,
                wait_until=step.get("wait_until", "domcontentloaded"),
                timeout=int(step.get("timeout", 60000)),
            )
        elif action == "click":
            selector = step["selector"]
            timeout = int(step.get("timeout", 15000))
            await page.wait_for_selector(selector, timeout=timeout)
            await page.click(selector)
        elif action == "wait":
            await page.wait_for_timeout(int(step["duration"]))
        elif action == "wait_load":
            await page.wait_for_load_state(step.get("state", "networkidle"))
        elif action == "fill":
            await page.fill(step["selector"], step["value"])
        else:
            raise ValueError(f"Unknown action: {action!r}")


async def extract_date(page: Page, site: SiteConfig) -> tuple[date, str] | None:
    """Try each configured extraction strategy in order. Returns (date, raw_text) or None."""
    for strat in site.extract:
        t = strat["type"]
        if t == "input_dutch_date":
            for inp in await page.locator("input").all():
                try:
                    val = (await inp.input_value()).strip()
                except Exception:
                    continue
                if any(m in val.lower() for m in DUTCH_MONTHS):
                    d = parse_dutch_date(val)
                    if d:
                        return d, val
        elif t == "text_regex_dutch_date":
            try:
                body = await page.inner_text("body")
            except Exception:
                continue
            d = parse_dutch_date(body)
            if d:
                m = _DUTCH_DATE_REGEX.search(body)
                return d, (m.group(0) if m else body[:60])
        elif t == "selector_text":
            try:
                txt = (await page.locator(strat["selector"]).first.inner_text()).strip()
            except Exception:
                continue
            d = parse_dutch_date(txt)
            if d:
                return d, txt
        else:
            log.warning(f"Unknown extract type: {t!r}")
    return None


# -----------------------------------------------------------------------------
# Main per-site check
# -----------------------------------------------------------------------------

async def check_site_once(pw: Playwright, site: SiteConfig) -> tuple[date, str]:
    """Run one full check. Raises on failure."""
    browser: Browser = await pw.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",  # critical in CI containers
            "--disable-gpu",
            "--disable-blink-features=AutomationControlled",
        ],
    )
    try:
        ctx: BrowserContext = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 800},
            locale="nl-NL",
        )
        page = await ctx.new_page()
        await run_steps(page, site)
        result = await extract_date(page, site)
        if result is None:
            raise RuntimeError("No date found on page")
        return result
    finally:
        await browser.close()


async def check_site(site_id: str, site: SiteConfig) -> None:
    log.info(f"=== Checking site: {site.name} (notify if before {site.notify_if_before}) ===")
    state = load_state(site_id)
    found: tuple[date, str] | None = None
    last_error = ""

    async with async_playwright() as pw:
        for attempt in range(1, site.max_retries + 1):
            try:
                found = await check_site_once(pw, site)
                break
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                log.warning(f"Attempt {attempt}/{site.max_retries} failed: {last_error}")
                if attempt < site.max_retries:
                    await asyncio.sleep(2 * attempt)

    state.last_check_utc = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    if found is None:
        state.consecutive_failures += 1
        log.error(f"All {site.max_retries} attempts failed. Last error: {last_error}")
        # Notify only once after many consecutive failures, to avoid spam
        if state.consecutive_failures in (10, 50, 200):
            send_telegram(
                f"⚠️ <b>{site.name}</b>: {state.consecutive_failures} checks op rij mislukt.\n"
                f"<code>{last_error[:200]}</code>"
            )
        save_state(site_id, state)
        return

    state.consecutive_failures = 0
    found_date, raw = found
    log.info(f"Earliest available: {found_date} (raw: {raw!r})")

    previous = state.last_seen_date
    state.last_seen_date = found_date

    should_notify = False
    reason = ""

    if found_date < site.notify_if_before:
        # Date qualifies. Only notify if it's NEW or EARLIER than last notification.
        if state.last_notified_date is None:
            should_notify = True
            reason = "first qualifying date"
        elif found_date < state.last_notified_date:
            should_notify = True
            reason = f"earlier than last notified ({state.last_notified_date})"
        elif found_date != state.last_notified_date:
            # Different date but not earlier — don't spam
            log.info(f"Date {found_date} qualifies but not earlier than last notified {state.last_notified_date}")
        else:
            log.info(f"Same date as last notified ({state.last_notified_date}) — quiet")

    if should_notify:
        msg = site.message_template.format(
            name=site.name,
            date=format_dutch_date(found_date),
            url=site.url,
            previous_date=format_dutch_date(previous) if previous else "—",
        )
        if send_telegram(msg):
            state.last_notified_date = found_date
            log.info(f"Notified ({reason})")
    else:
        if previous != found_date:
            log.info(f"Date changed: {previous} → {found_date} (no notification, not earlier than deadline)")
        elif not site.quiet_when_unchanged:
            log.info(f"Unchanged: {found_date}")

    save_state(site_id, state)


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------

async def main_async(only: list[str] | None) -> int:
    site_files = sorted(SITES_DIR.glob("*.yaml")) + sorted(SITES_DIR.glob("*.yml"))
    if not site_files:
        log.error(f"No site configs found in {SITES_DIR}")
        return 1

    had_unrecoverable_error = False
    for path in site_files:
        site_id = path.stem
        if only and site_id not in only:
            continue
        try:
            site = SiteConfig.from_yaml(path)
        except Exception as e:
            log.error(f"Failed to load {path.name}: {e}")
            had_unrecoverable_error = True
            continue
        try:
            await check_site(site_id, site)
        except Exception:
            log.error(f"Unhandled error checking {site_id}:\n{traceback.format_exc()}")
            had_unrecoverable_error = True

    return 1 if had_unrecoverable_error else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Appointment availability watcher")
    parser.add_argument(
        "--only", nargs="*", default=None,
        help="Run only these site IDs (filename without .yaml). Default: all.",
    )
    args = parser.parse_args()
    return asyncio.run(main_async(args.only))


if __name__ == "__main__":
    sys.exit(main())
