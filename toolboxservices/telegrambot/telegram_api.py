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


def send_message(chat_id, text, parse_mode=None, disable_preview=True):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": disable_preview,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    return call("sendMessage", payload)


def send_chat_action(chat_id, action="typing"):
    return call("sendChatAction", {"chat_id": chat_id, "action": action})
