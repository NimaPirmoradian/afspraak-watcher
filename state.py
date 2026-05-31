"""
Persisted state for sites and the Telegram update cursor.

Each site has a JSON file in state/<site_id>.json. The Telegram polling
cursor lives in state/_telegram.json.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent
STATE_DIR = ROOT / "state"


# -----------------------------------------------------------------------------
# Per-site state
# -----------------------------------------------------------------------------

@dataclass
class SiteState:
    """Runtime state for a single site. Persisted to state/<site_id>.json."""

    # User-controllable
    active: bool = False                     # /watch sets True, /pause and /booked set False
    deadline_override: date | None = None    # /deadline sets this; None = no cap

    # Internal
    last_seen_date: date | None = None       # most recent earliest-available date scraped
    last_notified_date: date | None = None   # date used in most recent Telegram notification
    last_check_utc: str = ""                 # ISO timestamp of most recent check attempt
    consecutive_failures: int = 0            # count of scrape failures in a row
    first_notification_sent: bool = False    # set True after the baseline notif
    force_check_pending: bool = False        # /check sets True → checker runs even if recently checked

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SiteState":
        def opt_date(v: Any) -> date | None:
            return date.fromisoformat(v) if v else None

        return cls(
            active=bool(d.get("active", False)),
            deadline_override=opt_date(d.get("deadline_override")),
            last_seen_date=opt_date(d.get("last_seen_date")),
            last_notified_date=opt_date(d.get("last_notified_date")),
            last_check_utc=str(d.get("last_check_utc", "")),
            consecutive_failures=int(d.get("consecutive_failures", 0)),
            first_notification_sent=bool(d.get("first_notification_sent", False)),
            force_check_pending=bool(d.get("force_check_pending", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "deadline_override": self.deadline_override.isoformat() if self.deadline_override else None,
            "last_seen_date": self.last_seen_date.isoformat() if self.last_seen_date else None,
            "last_notified_date": self.last_notified_date.isoformat() if self.last_notified_date else None,
            "last_check_utc": self.last_check_utc,
            "consecutive_failures": self.consecutive_failures,
            "first_notification_sent": self.first_notification_sent,
            "force_check_pending": self.force_check_pending,
        }


def _site_path(site_id: str) -> Path:
    return STATE_DIR / f"{site_id}.json"


def load_site_state(site_id: str) -> SiteState:
    p = _site_path(site_id)
    if not p.exists():
        return SiteState()
    try:
        return SiteState.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        # corrupted file → start clean, do not crash the workflow
        return SiteState()


def save_site_state(site_id: str, state: SiteState) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _site_path(site_id).write_text(
        json.dumps(state.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# -----------------------------------------------------------------------------
# Telegram update cursor
# -----------------------------------------------------------------------------

_TELEGRAM_FILE = STATE_DIR / "_telegram.json"


def load_telegram_offset() -> int:
    if not _TELEGRAM_FILE.exists():
        return 0
    try:
        return int(json.loads(_TELEGRAM_FILE.read_text(encoding="utf-8")).get("last_update_id", 0))
    except Exception:
        return 0


def save_telegram_offset(update_id: int) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _TELEGRAM_FILE.write_text(
        json.dumps({"last_update_id": int(update_id)}, indent=2) + "\n",
        encoding="utf-8",
    )
