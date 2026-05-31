#!/usr/bin/env python3
"""
Entry point for the GitHub Actions cron job.

Order of operations:
  1. Pull pending Telegram commands & button presses → mutate state
  2. For every site whose state.active is True, run a check + send notif if needed

Both phases tolerate per-item failures so one broken site / one bad message
doesn't take down the whole run.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import traceback

from bot import process_pending_updates
from checker import check_and_notify
from sites import list_sites
from state import load_site_state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("main")


def run_bot_phase() -> None:
    log.info("=" * 60)
    log.info("PHASE 1: process Telegram updates")
    log.info("=" * 60)
    try:
        n = process_pending_updates()
        log.info(f"Processed {n} Telegram update(s)")
    except Exception:
        log.error("Bot phase crashed (non-fatal):\n" + traceback.format_exc())


async def run_check_phase(only: list[str] | None) -> bool:
    log.info("=" * 60)
    log.info("PHASE 2: check active sites")
    log.info("=" * 60)
    sites = list_sites()
    if not sites:
        log.warning("No site YAMLs found in sites/")
        return True

    had_error = False
    for site_id, cfg in sites.items():
        if only and site_id not in only:
            continue
        state = load_site_state(site_id)
        if not state.active:
            log.info(f"[{site_id}] skipped (paused)")
            continue
        try:
            await check_and_notify(cfg)
        except Exception:
            had_error = True
            log.error(f"[{site_id}] uncaught error:\n" + traceback.format_exc())
    return not had_error


def main() -> int:
    parser = argparse.ArgumentParser(description="Afspraak Watcher main loop")
    parser.add_argument(
        "--only", nargs="*", default=None,
        help="Limit phase 2 to specific site_ids (filename stems). Default: all active.",
    )
    parser.add_argument(
        "--skip-bot", action="store_true",
        help="Skip Telegram polling (debug).",
    )
    parser.add_argument(
        "--skip-checks", action="store_true",
        help="Skip site checks (debug).",
    )
    args = parser.parse_args()

    if not args.skip_bot:
        run_bot_phase()

    if not args.skip_checks:
        ok = asyncio.run(run_check_phase(args.only))
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
