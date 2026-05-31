"""
Telegram command + callback processing.

Polls Telegram for new updates, parses commands and inline-button taps,
mutates site state files. Authorization: only the configured CHAT_ID can
issue commands; everything else is silently ignored.

Public API:
    process_pending_updates() — pull all queued updates and act on them
"""

from __future__ import annotations

import logging
import os
from datetime import date
from typing import Any, Callable

from sites import SiteConfig, list_sites, load_site
from state import (
    SiteState,
    load_site_state,
    load_telegram_offset,
    save_site_state,
    save_telegram_offset,
)
from telegram_io import (
    answer_callback,
    edit_message_text,
    get_updates,
    send_message,
)
from checker import format_dutch_date

log = logging.getLogger("bot")

ALLOWED_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


# -----------------------------------------------------------------------------
# Help text
# -----------------------------------------------------------------------------

HELP_TEXT = (
    "<b>دستورات</b>\n\n"
    "/list — لیست همه سایت‌ها و وضعیتشون\n"
    "/info <code>&lt;site&gt;</code> — جزئیات یک سایت\n\n"
    "/watch <code>&lt;site&gt;</code> — شروع چک کردن\n"
    "/pause <code>&lt;site&gt;</code> — توقف موقت (با /resume برمی‌گرده)\n"
    "/resume <code>&lt;site&gt;</code> — ادامه چک کردن\n"
    "/booked <code>&lt;site&gt;</code> — قرار رو گرفتم، خاموش کن\n\n"
    "/deadline <code>&lt;site&gt; &lt;YYYY-MM-DD&gt;</code> — تاریخ سقف برای notif\n"
    "/deadline <code>&lt;site&gt; clear</code> — حذف تاریخ سقف\n\n"
    "/check <code>&lt;site&gt;</code> — درخواست چک فوری در ران بعدی\n\n"
    "/help — همین راهنما\n\n"
    "💡 برای اضافه‌کردن سایت جدید، یک چت با AI باز کن و آدرس "
    "گیت‌هاب پروژه + URL سایت جدید رو بده."
)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _is_authorized(chat_id: int | str) -> bool:
    return ALLOWED_CHAT_ID != "" and str(chat_id) == str(ALLOWED_CHAT_ID)


def _site_or_reply(site_id: str, chat_id: int) -> SiteConfig | None:
    cfg = load_site(site_id)
    if cfg is None:
        all_ids = ", ".join(list_sites().keys()) or "(هیچی نیست)"
        send_message(
            f"❓ سایتی به اسم <code>{site_id}</code> پیدا نشد.\n"
            f"موجود: <code>{all_ids}</code>",
            chat_id=chat_id,
        )
        return None
    return cfg


def _status_emoji(state: SiteState) -> str:
    return "🟢" if state.active else "⚪"


def _format_state_line(site_id: str, cfg: SiteConfig, state: SiteState) -> str:
    parts = [f"{_status_emoji(state)} <b>{site_id}</b> — {cfg.name}"]
    if state.last_seen_date:
        parts.append(f"   آخرین تاریخ پیداشده: {format_dutch_date(state.last_seen_date)}")
    if state.deadline_override:
        parts.append(f"   تاریخ سقف: {format_dutch_date(state.deadline_override)}")
    if state.consecutive_failures > 0:
        parts.append(f"   ⚠️ {state.consecutive_failures} fail پشت سر هم")
    return "\n".join(parts)


# -----------------------------------------------------------------------------
# Command handlers
# -----------------------------------------------------------------------------

def cmd_start(chat_id: int, args: list[str]) -> None:
    send_message(
        "👋 سلام Nima!\n\n"
        "این بات وقت‌های آزاد رو برات چک میکنه. /help برای دیدن دستورات.",
        chat_id=chat_id,
    )


def cmd_help(chat_id: int, args: list[str]) -> None:
    send_message(HELP_TEXT, chat_id=chat_id, disable_preview=True)


def cmd_list(chat_id: int, args: list[str]) -> None:
    sites = list_sites()
    if not sites:
        send_message("هیچ سایتی تعریف نشده. اول یه YAML در پوشه sites/ اضافه کن.", chat_id=chat_id)
        return
    lines = ["<b>سایت‌ها:</b>\n"]
    for site_id, cfg in sites.items():
        state = load_site_state(site_id)
        lines.append(_format_state_line(site_id, cfg, state))
    lines.append("\n🟢 = active · ⚪ = paused")
    send_message("\n".join(lines), chat_id=chat_id)


def cmd_info(chat_id: int, args: list[str]) -> None:
    if not args:
        send_message("استفاده: <code>/info &lt;site&gt;</code>", chat_id=chat_id)
        return
    cfg = _site_or_reply(args[0], chat_id)
    if cfg is None:
        return
    state = load_site_state(cfg.site_id)
    lines = [
        f"<b>{cfg.name}</b>",
        f"site_id: <code>{cfg.site_id}</code>",
        f"وضعیت: {'🟢 active' if state.active else '⚪ paused'}",
        f"URL: {cfg.url}",
        f"آخرین چک: {state.last_check_utc or '—'} UTC",
        f"آخرین تاریخ پیداشده: {format_dutch_date(state.last_seen_date) if state.last_seen_date else '—'}",
        f"آخرین تاریخ نوتیف‌شده: {format_dutch_date(state.last_notified_date) if state.last_notified_date else '—'}",
        f"تاریخ سقف: {format_dutch_date(state.deadline_override) if state.deadline_override else '—'}",
        f"failure پشت سر هم: {state.consecutive_failures}",
    ]
    send_message("\n".join(lines), chat_id=chat_id, disable_preview=True)


def cmd_watch(chat_id: int, args: list[str]) -> None:
    if not args:
        send_message("استفاده: <code>/watch &lt;site&gt;</code>", chat_id=chat_id)
        return
    cfg = _site_or_reply(args[0], chat_id)
    if cfg is None:
        return
    state = load_site_state(cfg.site_id)
    was_active = state.active
    state.active = True
    # If re-activating after /booked, reset baseline so user gets a fresh first-date notif
    state.first_notification_sent = False
    state.last_notified_date = None
    save_site_state(cfg.site_id, state)
    msg = (
        f"{'✅ از سر گرفته شد' if was_active else '✅ شروع شد'} چک کردن <b>{cfg.name}</b>.\n"
        f"در ران بعدی (تا ~۱۰ دقیقه دیگه) اولین تاریخ آزاد رو می‌فرستم."
    )
    if state.deadline_override:
        msg += f"\nتاریخ سقف فعلی: <b>{format_dutch_date(state.deadline_override)}</b>"
    else:
        msg += "\n\nاگه می‌خوای تاریخ سقف تنظیم کنی: <code>/deadline " + cfg.site_id + " 2026-06-09</code>"
    send_message(msg, chat_id=chat_id)


def cmd_pause(chat_id: int, args: list[str]) -> None:
    if not args:
        send_message("استفاده: <code>/pause &lt;site&gt;</code>", chat_id=chat_id)
        return
    cfg = _site_or_reply(args[0], chat_id)
    if cfg is None:
        return
    state = load_site_state(cfg.site_id)
    state.active = False
    save_site_state(cfg.site_id, state)
    send_message(
        f"⏸ پاوز شد <b>{cfg.name}</b>.\n"
        f"برای ادامه: <code>/resume {cfg.site_id}</code>",
        chat_id=chat_id,
    )


def cmd_resume(chat_id: int, args: list[str]) -> None:
    # alias for watch
    cmd_watch(chat_id, args)


def cmd_booked(chat_id: int, args: list[str]) -> None:
    """Ask for confirmation before pausing."""
    if not args:
        send_message("استفاده: <code>/booked &lt;site&gt;</code>", chat_id=chat_id)
        return
    cfg = _site_or_reply(args[0], chat_id)
    if cfg is None:
        return
    keyboard = {
        "inline_keyboard": [[
            {"text": "✅ بله، خاموش کن", "callback_data": f"pause_yes:{cfg.site_id}"},
            {"text": "❌ خیر، ادامه بده", "callback_data": f"pause_no:{cfg.site_id}"},
        ]]
    }
    send_message(
        f"❓ مطمئنی قرار <b>{cfg.name}</b> رو گرفتی؟\n"
        f"این کار چک خودکار رو متوقف میکنه.",
        chat_id=chat_id,
        reply_markup=keyboard,
    )


def cmd_deadline(chat_id: int, args: list[str]) -> None:
    if len(args) < 2:
        send_message(
            "استفاده: <code>/deadline &lt;site&gt; &lt;YYYY-MM-DD&gt;</code>\n"
            "یا برای پاک‌کردن: <code>/deadline &lt;site&gt; clear</code>",
            chat_id=chat_id,
        )
        return
    cfg = _site_or_reply(args[0], chat_id)
    if cfg is None:
        return
    state = load_site_state(cfg.site_id)
    if args[1].lower() in ("clear", "none", "off", "remove"):
        state.deadline_override = None
        save_site_state(cfg.site_id, state)
        send_message(f"🗑 تاریخ سقف <b>{cfg.name}</b> پاک شد.", chat_id=chat_id)
        return
    try:
        new_deadline = date.fromisoformat(args[1])
    except ValueError:
        send_message(
            f"❌ تاریخ <code>{args[1]}</code> نامعتبره. فرمت: YYYY-MM-DD (مثلاً 2026-06-09)",
            chat_id=chat_id,
        )
        return
    state.deadline_override = new_deadline
    # Resetting first_notification_sent ensures the user gets a fresh
    # baseline-style notification with the new context.
    state.first_notification_sent = False
    save_site_state(cfg.site_id, state)
    send_message(
        f"✅ تاریخ سقف <b>{cfg.name}</b> روی <b>{format_dutch_date(new_deadline)}</b> تنظیم شد.\n"
        f"در ران بعدی وضعیت فعلی رو دوباره گزارش می‌دم.",
        chat_id=chat_id,
    )


def cmd_check(chat_id: int, args: list[str]) -> None:
    if not args:
        send_message("استفاده: <code>/check &lt;site&gt;</code>", chat_id=chat_id)
        return
    cfg = _site_or_reply(args[0], chat_id)
    if cfg is None:
        return
    state = load_site_state(cfg.site_id)
    if not state.active:
        send_message(
            f"⚠️ <b>{cfg.name}</b> الان paused هست. اول <code>/watch {cfg.site_id}</code>.",
            chat_id=chat_id,
        )
        return
    state.force_check_pending = True
    save_site_state(cfg.site_id, state)
    send_message(f"📋 در ران بعدی <b>{cfg.name}</b> چک می‌شه.", chat_id=chat_id)


COMMANDS: dict[str, Callable[[int, list[str]], None]] = {
    "/start": cmd_start,
    "/help": cmd_help,
    "/list": cmd_list,
    "/info": cmd_info,
    "/watch": cmd_watch,
    "/pause": cmd_pause,
    "/resume": cmd_resume,
    "/booked": cmd_booked,
    "/deadline": cmd_deadline,
    "/check": cmd_check,
}


# -----------------------------------------------------------------------------
# Callback (inline button) handlers
# -----------------------------------------------------------------------------

def _handle_callback(cb: dict[str, Any]) -> None:
    cb_id = cb["id"]
    data = cb.get("data", "")
    message = cb.get("message", {}) or {}
    chat_id = message.get("chat", {}).get("id")
    message_id = message.get("message_id")
    from_user_id = cb.get("from", {}).get("id")

    if not _is_authorized(from_user_id):
        answer_callback(cb_id, "Not authorized")
        return

    if ":" not in data:
        answer_callback(cb_id, "Invalid")
        return

    action, _, site_id = data.partition(":")
    cfg = load_site(site_id)
    if cfg is None:
        answer_callback(cb_id, "سایت ناشناخته")
        return

    state = load_site_state(site_id)

    if action == "pause":
        # First tap on the "✅ گرفتم" button under a notification → ask confirmation
        confirm_kb = {
            "inline_keyboard": [[
                {"text": "✅ بله، خاموش کن", "callback_data": f"pause_yes:{site_id}"},
                {"text": "❌ خیر، ادامه بده", "callback_data": f"pause_no:{site_id}"},
            ]]
        }
        # Preserve original message text, add a question
        orig_text = message.get("text") or message.get("caption") or cfg.name
        new_text = orig_text + f"\n\n❓ مطمئنی قرار <b>{cfg.name}</b> رو گرفتی؟"
        edit_message_text(chat_id, message_id, new_text, reply_markup=confirm_kb)
        answer_callback(cb_id)

    elif action == "pause_yes":
        state.active = False
        save_site_state(site_id, state)
        orig_text = message.get("text") or cfg.name
        edit_message_text(
            chat_id, message_id,
            orig_text + f"\n\n⏸ <b>{cfg.name}</b> خاموش شد. برای فعال‌سازی: <code>/watch {site_id}</code>",
            reply_markup=None,
        )
        answer_callback(cb_id, "✅ خاموش شد")

    elif action == "pause_no":
        orig_text = message.get("text") or cfg.name
        edit_message_text(
            chat_id, message_id,
            orig_text + "\n\n✅ ادامه می‌دم.",
            reply_markup=None,
        )
        answer_callback(cb_id, "ادامه می‌دم")

    elif action == "check":
        if not state.active:
            answer_callback(cb_id, "⚠️ این سایت paused هست")
            return
        state.force_check_pending = True
        save_site_state(site_id, state)
        answer_callback(cb_id, "📋 در ران بعدی چک می‌شه")

    else:
        answer_callback(cb_id, "ناشناخته")


# -----------------------------------------------------------------------------
# Top-level update processor
# -----------------------------------------------------------------------------

def _handle_message(msg: dict[str, Any]) -> None:
    chat_id = msg.get("chat", {}).get("id")
    text = (msg.get("text") or "").strip()
    from_user_id = msg.get("from", {}).get("id")

    if not _is_authorized(from_user_id):
        # Silently ignore strangers; do not echo anything that could reveal we exist
        log.info(f"Ignored message from unauthorized user {from_user_id}")
        return

    if not text.startswith("/"):
        send_message("دستوری نفهمیدم. /help", chat_id=chat_id)
        return

    # Strip @botname suffix if present (Telegram appends it in group chats)
    parts = text.split()
    cmd = parts[0].split("@")[0].lower()
    args = parts[1:]

    handler = COMMANDS.get(cmd)
    if handler is None:
        send_message(f"دستور ناشناخته: <code>{cmd}</code>\n/help", chat_id=chat_id)
        return

    try:
        handler(chat_id, args)
    except Exception:
        log.exception(f"Handler {cmd} crashed")
        send_message(f"❌ خطا در پردازش <code>{cmd}</code>. لاگ Actions رو ببین.", chat_id=chat_id)


def process_pending_updates() -> int:
    """Pull all queued Telegram updates and dispatch them. Returns count processed."""
    offset = load_telegram_offset()
    updates = get_updates(offset + 1 if offset else 0)
    log.info(f"Telegram updates pending: {len(updates)} (offset={offset})")

    max_id = offset
    for upd in updates:
        upd_id = int(upd.get("update_id", 0))
        max_id = max(max_id, upd_id)
        try:
            if "message" in upd:
                _handle_message(upd["message"])
            elif "callback_query" in upd:
                _handle_callback(upd["callback_query"])
        except Exception:
            log.exception(f"Failed to handle update {upd_id}")

    if max_id != offset:
        save_telegram_offset(max_id)
    return len(updates)
