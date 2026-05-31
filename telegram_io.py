"""
Thin wrapper around the Telegram Bot HTTP API.

All functions are best-effort: on network failure they log and return None
rather than crash the workflow.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

log = logging.getLogger("telegram")

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

_API = f"https://api.telegram.org/bot{TOKEN}" if TOKEN else ""


def _post(method: str, **payload: Any) -> dict | None:
    if not TOKEN:
        log.error(f"No TELEGRAM_BOT_TOKEN; skipping {method}")
        return None
    # Drop None values so we don't send "field: null"
    payload = {k: v for k, v in payload.items() if v is not None}
    try:
        r = requests.post(f"{_API}/{method}", json=payload, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error(f"{method} failed: {e}")
        return None


# -----------------------------------------------------------------------------
# Outgoing
# -----------------------------------------------------------------------------

def send_message(
    text: str,
    chat_id: str | int | None = None,
    reply_markup: dict | None = None,
    reply_to: int | None = None,
    disable_preview: bool = False,
) -> dict | None:
    return _post(
        "sendMessage",
        chat_id=chat_id or CHAT_ID,
        text=text,
        parse_mode="HTML",
        disable_web_page_preview=disable_preview,
        reply_markup=reply_markup,
        reply_to_message_id=reply_to,
    )


def answer_callback(callback_query_id: str, text: str = "") -> None:
    _post("answerCallbackQuery", callback_query_id=callback_query_id, text=text)


def edit_message_text(
    chat_id: int | str,
    message_id: int,
    text: str,
    reply_markup: dict | None = None,
) -> dict | None:
    return _post(
        "editMessageText",
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        parse_mode="HTML",
        reply_markup=reply_markup,
    )


# -----------------------------------------------------------------------------
# Incoming
# -----------------------------------------------------------------------------

def get_updates(offset: int) -> list[dict]:
    """Fetch new updates since the given offset. Non-blocking (timeout=0)."""
    resp = _post(
        "getUpdates",
        offset=offset,
        timeout=0,
        allowed_updates=["message", "callback_query"],
    )
    if not resp or not resp.get("ok"):
        return []
    return resp.get("result", []) or []
