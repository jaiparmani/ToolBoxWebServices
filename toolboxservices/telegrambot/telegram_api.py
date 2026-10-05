"""Thin wrapper over the Telegram Bot HTTP API.

The webhook never long-polls; it only ever needs to *send* a reply (and to
register/clear the webhook from a management command). All of that is plain
HTTPS POSTs to https://api.telegram.org/bot<token>/<method>, so there is no
need to pull in python-telegram-bot on the server.
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

API_ROOT = "https://api.telegram.org"
TIMEOUT = 15


def _token():
    token = getattr(settings, "TELEGRAM_BOT_TOKEN", "") or ""
    return token.strip()


def is_configured():
    return bool(_token())


def call(method, payload=None, timeout=TIMEOUT):
    """Call a Telegram Bot API method. Returns the parsed JSON dict (or {}).

    Never raises to the caller — a failed reply must not turn into a 500 that
    makes Telegram retry the same update forever.
    """
    token = _token()
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN is not set; cannot call %s", method)
        return {}
    url = f"{API_ROOT}/bot{token}/{method}"
    try:
        response = requests.post(url, json=payload or {}, timeout=timeout)
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Telegram %s failed: %s", method, exc)
        return {}
    # Telegram answers 200 with {"ok": false, ...} for things like a bad token
    # (401), an unknown chat_id, or a parse_mode/HTML error — the reply is never
    # delivered but nothing raised. Log it so a silent "no response" is visible.
    if isinstance(data, dict) and not data.get("ok", True):
        logger.error(
            "Telegram %s rejected: error_code=%s description=%s",
            method,
            data.get("error_code"),
            data.get("description"),
        )
    return data


def send_message(chat_id, text, parse_mode=None, disable_preview=True, reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": disable_preview,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return call("sendMessage", payload)


def confirm_edit_discard_keyboard():
    """A one-time reply keyboard offering Confirm / Edit / Discard.

    A reply keyboard (not an inline one) so tapping it just sends its label
    as a normal text message — which flows straight through the existing
    telegram-router -> /api/telegram/relay/ path like anything else the user
    types, with no need for the router to understand Telegram callback
    queries.

    The labels spell out "...this expense" rather than a bare "Confirm" /
    "Edit" / "Discard": the same bot also fronts brain-chat and life-rpg
    behind telegram-router's classifier (see settings.py's Telegram section),
    and a bare generic word is exactly the kind of message that classifier
    can hand to one of those instead of to ToolBox. Keep labels unambiguous
    even if that makes them longer.
    """
    return {
        "keyboard": [["✅ Confirm this expense", "✏️ Edit this expense"],
                     ["🗑 Discard this expense"]],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }


def cancel_keyboard():
    """Shown while waiting for the free-text correction after Edit is tapped."""
    return {
        "keyboard": [["✕ Cancel the edit"]],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }


def remove_keyboard():
    return {"remove_keyboard": True}


def send_chat_action(chat_id, action="typing"):
    return call("sendChatAction", {"chat_id": chat_id, "action": action})


def notify_user(user, text, parse_mode=None, reply_markup=None):
    """Push a message to a user's linked Telegram chat, if they have one.

    Used to reach a person outside the request/response loop — e.g. telling the
    other party a split was added against them. A no-op when the user has no
    Telegram link, the bot isn't configured, or the user has turned Telegram
    notifications off in Settings — checked here so every caller gets that
    preference for free rather than each remembering to check it. Never
    raises (a notification failure must not break the action that triggered
    it).
    """
    if user is None or not is_configured():
        return None
    try:
        if not getattr(user.profile, 'telegram_notifications_enabled', True):
            return None
    except Exception:
        pass  # no profile yet — default to notifying, same as the field's default
    try:
        from .models import TelegramLink
        link = TelegramLink.objects.filter(user=user).first()
        if link:
            send_message(link.chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup)
            return link
    except Exception:  # pragma: no cover - best-effort side channel
        logger.exception("notify_user failed")
    return None
