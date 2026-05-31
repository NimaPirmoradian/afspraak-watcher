"""
Site scraping + notification logic.

Public API:
    DUTCH_MONTHS, parse_dutch_date, format_dutch_date   - date helpers
    check_and_notify(site)                              - one full check cycle

Skips inactive sites. Decides whether to notify based on state, sends
notifications with inline buttons.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from sites import SiteConfig
from state import SiteState, load_site_state, save_site_state
from telegram_io import send_message

log = logging.getLogger("checker")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# -----------------------------------------------------------------------------
# Dutch date parsing
# -----------------------------------------------------------------------------

DUTCH_MONTHS = {
    "januari": 1, "februari": 2, "maart": 3, "april": 4,
    "mei": 5, "juni": 6, "juli": 7, "augustus": 8,
    "september": 9, "oktober": 10, "november": 11, "december": 12,
}

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
    inv = {v: k for k, v in DUTCH_MONTHS.items()}
    return f"{d.day} {inv[d.month]} {d.year}"


# -----------------------------------------------------------------------------
# Browser actions driven by YAML
# -----------------------------------------------------------------------------

async def _run_steps(page: Page, site: SiteConfig) -> None:
    for i, step in enumerate(site.steps):
        action = step.get("action")
        log.debug(f"  step[{i}] {action}")
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


async def _extract_date(page: Page, site: SiteConfig) -> tuple[date, str] | None:
    for strat in site.extract:
        t = strat.get("type")
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


async def _scrape_once(pw: Playwright, site: SiteConfig) -> tuple[date, str]:
    browser: Browser = await pw.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
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
        await _run_steps(page, site)
        result = await _extract_date(page, site)
        if result is None:
            raise RuntimeError("No date found on page")
        return result
    finally:
        await browser.close()


async def _scrape_with_retries(site: SiteConfig) -> tuple[date, str] | str:
    """Returns (date, raw_text) on success, or error string on total failure."""
    last_error = ""
    async with async_playwright() as pw:
        for attempt in range(1, site.max_retries + 1):
            try:
                return await _scrape_once(pw, site)
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                log.warning(f"  attempt {attempt}/{site.max_retries} failed: {last_error}")
                if attempt < site.max_retries:
                    await asyncio.sleep(2 * attempt)
    return last_error


# -----------------------------------------------------------------------------
# Notification logic
# -----------------------------------------------------------------------------

def _build_notification_keyboard(site_id: str, url: str) -> dict:
    """Inline buttons shown under every slot-found notification."""
    return {
        "inline_keyboard": [
            [
                {"text": "🔗 صفحه بوکینگ", "url": url},
                {"text": "✅ قرار گرفتم", "callback_data": f"pause:{site_id}"},
            ],
            [
                {"text": "🔄 دوباره چک کن", "callback_data": f"check:{site_id}"},
            ],
        ]
    }


def _should_notify(state: SiteState, found: date) -> tuple[bool, str]:
    """Decide whether the found date warrants a Telegram message.

    Rules:
    - If we've never sent the baseline notif: always notify (it's the baseline).
    - Otherwise: notify only if `found` is earlier than min(deadline, last_notified).
      (deadline_override is the user's cap; last_notified prevents repeating.)
    """
    if not state.first_notification_sent:
        return True, "baseline"

    candidates: list[date] = []
    if state.deadline_override is not None:
        candidates.append(state.deadline_override)
    if state.last_notified_date is not None:
        candidates.append(state.last_notified_date)

    if not candidates:
        return True, "no threshold"

    if found < min(candidates):
        return True, "improvement"
    return False, "no improvement"


def _build_message(site: SiteConfig, found: date, state: SiteState, reason: str) -> str:
    if reason == "baseline":
        text = (
            f"👀 شروع کردم چک کردن <b>{site.name}</b>.\n\n"
            f"اولین تاریخی که الان آزاده: <b>{format_dutch_date(found)}</b>"
        )
        if state.deadline_override:
            text += f"\nفقط تا <b>{format_dutch_date(state.deadline_override)}</b> رو نگاه می‌کنم."
        text += "\n\nاز این به بعد فقط اگه تاریخ زودتری ببینم خبرت می‌کنم."
        return text

    # improvement
    prev = state.last_notified_date
    return (
        f"🎉 <b>یه تاریخ زودتر پیدا کردم!</b>\n\n"
        f"📍 <b>{site.name}</b>\n"
        f"📅 جدید: <b>{format_dutch_date(found)}</b>\n"
        f"قبلاً: {format_dutch_date(prev) if prev else '—'}\n\n"
        f"سریع برو بگیر 🏃"
    )


# -----------------------------------------------------------------------------
# Public entry: check one site
# -----------------------------------------------------------------------------

async def check_and_notify(site: SiteConfig) -> None:
    state = load_site_state(site.site_id)

    if not state.active:
        log.info(f"[{site.site_id}] skipping (not active)")
        return

    log.info(
        f"[{site.site_id}] === checking {site.name} ==="
        f" (deadline={state.deadline_override}, last_notified={state.last_notified_date})"
    )

    result = await _scrape_with_retries(site)
    state.last_check_utc = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    state.force_check_pending = False  # consumed

    if isinstance(result, str):
        # Total failure after retries
        state.consecutive_failures += 1
        log.error(f"[{site.site_id}] failed: {result}")
        # Notify rarely so the user knows something is broken — but not on every cron tick
        if state.consecutive_failures in (10, 50, 200):
            send_message(
                f"⚠️ <b>{site.name}</b>: {state.consecutive_failures} بار پشت سر هم نتونستم چک کنم.\n"
                f"<code>{result[:300]}</code>"
            )
        save_site_state(site.site_id, state)
        return

    # Success
    state.consecutive_failures = 0
    found_date, raw = result
    state.last_seen_date = found_date
    log.info(f"[{site.site_id}] earliest available: {found_date} (raw: {raw!r})")

    notify, reason = _should_notify(state, found_date)

    if notify:
        msg = _build_message(site, found_date, state, reason)
        kb = _build_notification_keyboard(site.site_id, site.url)
        resp = send_message(msg, reply_markup=kb)
        if resp and resp.get("ok"):
            state.last_notified_date = found_date
            state.first_notification_sent = True
            log.info(f"[{site.site_id}] notified ({reason})")
        else:
            log.error(f"[{site.site_id}] notificati