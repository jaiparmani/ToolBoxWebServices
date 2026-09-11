"""Thin wrapper over the Meta WhatsApp Cloud API.

Parallel to telegrambot.telegram_api, but the Cloud API is REST rather than
Telegram's bot<token>/<method> RPC style: sending is always a POST to
https://graph.facebook.com/<version>/<PHONE_NUMBER_ID>/messages with the
access token as a Bearer header. There is no long-polling fallback — the
webhook is the only way in.
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

API_VERSION = "v21.0"
API_ROOT = "https://graph.facebook.com"
TIMEOUT = 15


def _token():
    return (getattr(settings, "WHATSAPP_ACCESS_TOKEN", "") or "").strip()


def _phone_number_id():
    return (getattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "") or "").strip()


def is_configured():
    return bool(_token() and _phone_number_id())


def send_message(to, text):
    """Send a plain-text WhatsApp message. Never raises.

    A failed send must not turn into a 500 that makes Meta retry the same
    update forever — same contract as telegram_api.call.
    """
    token = _token()
    phone_number_id = _phone_number_id()
    if not token or not phone_number_id:
        logger.error("WHATSAPP_ACCESS_TOKEN/WHATSAPP_PHONE_NUMBER_ID not set; cannot send")
        return {}

    url = f"{API_ROOT}/{API_VERSION}/{phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text, "preview_url": False},
    }
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=TIMEOUT)
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("WhatsApp send failed: %s", exc)
        return {}
    if response.status_code >= 400:
        logger.error("WhatsApp send rejected (%s): %s", response.status_code, data)
    return data


def notify_user(user, text):
    """Push a message to a user's linked WhatsApp number, if they have one.

    Used to reach a person outside the request/response loop — e.g. telling
    the other party a split was added against them. A no-op when the user has
    no WhatsApp link or the bot isn't configured, and never raises (a
    notification failure must not break the action that triggered it).
    """
    if user is None or not is_configured():
        return
    try:
        from .models import WhatsAppLink
        link = WhatsAppLink.objects.filter(user=user).first()
        if link:
            send_message(link.wa_id, text)
    except Exception:  # pragma: no cover - best-effort side channel
        logger.exception("whatsappbot notify_user failed")
