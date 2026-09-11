"""WhatsApp webhook — mirrors telegrambot.views, on Meta's Cloud API instead.

Meta POSTs each update to POST /api/whatsapp/webhook/<secret>/, same
always-up-whenever-the-backend-is-up shape as the Telegram webhook: no
separate long-polling process to keep alive. There is no polling fallback
here though — Cloud API is webhook-only, so local dev needs a public HTTPS
tunnel (e.g. ngrok) to complete Meta's verification handshake.

The <secret> path segment must match settings.WHATSAPP_WEBHOOK_SECRET.
Meta also does a one-time GET verification (hub.mode/hub.verify_token/
hub.challenge) against the same URL when you save the webhook config in the
App dashboard; that's checked against settings.WHATSAPP_VERIFY_TOKEN.

All the command/business logic (parsing an expense, answering a spending
question, splitting a bill, …) is shared with the Telegram bot — imported
from telegrambot.handlers rather than duplicated, so both channels always
agree with the website. Telegram's handlers speak a little HTML markup
(<b>, <i>, escaped entities); _to_whatsapp() below converts that to
WhatsApp's own markdown (*bold*, _italic_) before sending.
"""

import html
import json
import logging
import re

from django.conf import settings
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from telegrambot import handlers
from . import whatsapp_api
from .models import WhatsAppLink

logger = logging.getLogger(__name__)


def _link_state(user):
    link = WhatsAppLink.objects.filter(user=user).first()
    if not link:
        return {"linked": False, "wa_id": None, "name": ""}
    return {"linked": True, "wa_id": link.wa_id, "name": link.name}


@api_view(["GET", "POST", "DELETE"])
@permission_classes([IsAuthenticated])
def whatsapp_link(request):
    """Link/unlink the signed-in ToolBox account to a WhatsApp number.

    GET    -> current link state.
    POST   {wa_id} -> link this account to that WhatsApp number (digits only,
             international format, e.g. 919876543210). Linking normally
             happens conversationally instead (send /link <token> to the
             bot); this exists for parity with telegram_link.
    DELETE -> unlink.
    """
    if request.method == "GET":
        return Response(_link_state(request.user))

    if request.method == "DELETE":
        WhatsAppLink.objects.filter(user=request.user).delete()
        return Response(_link_state(request.user))

    wa_id = str(request.data.get("wa_id") or "").strip()
    if not wa_id.isdigit():
        return Response(
            {"error": "Enter your WhatsApp number in digits only, international format (e.g. 919876543210)."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    clash = WhatsAppLink.objects.filter(wa_id=wa_id).exclude(user=request.user).first()
    if clash is not None:
        return Response(
            {"error": "That WhatsApp number is already linked to another account."},
            status=status.HTTP_409_CONFLICT,
        )

    WhatsAppLink.objects.update_or_create(user=request.user, defaults={"wa_id": wa_id})
    return Response(_link_state(request.user))


def _secret_ok(secret):
    expected = getattr(settings, "WHATSAPP_WEBHOOK_SECRET", "") or ""
    if not expected:
        logger.error("WHATSAPP_WEBHOOK_SECRET is not set; rejecting webhook call")
        return False
    return secret == expected


def _verify(request):
    """Meta's one-time GET handshake when you save the webhook in the App dashboard."""
    expected = getattr(settings, "WHATSAPP_VERIFY_TOKEN", "") or ""
    mode = request.GET.get("hub.mode")
    token = request.GET.get("hub.verify_token")
    challenge = request.GET.get("hub.challenge", "")
    if mode == "subscribe" and expected and token == expected:
        return HttpResponse(challenge)
    return HttpResponseForbidden("verification failed")


_TAG_RE = re.compile(r"<[^>]+>")


def _to_whatsapp(text):
    """Convert telegrambot.handlers' light HTML markup to WhatsApp markdown."""
    if not text:
        return text
    text = re.sub(r"<b>(.*?)</b>", r"*\1*", text, flags=re.S)
    text = re.sub(r"<i>(.*?)</i>", r"_\1_", text, flags=re.S)
    text = _TAG_RE.sub("", text)  # strip any tag we don't translate
    return html.unescape(text)


def _reply(wa_id, text):
    if not text:
        return
    whatsapp_api.send_message(wa_id, _to_whatsapp(text))


def _split_command(text):
    """('/ask', 'how much…') for '/ask how much…'; ('', text) for plain text."""
    stripped = (text or "").strip()
    if not stripped.startswith("/"):
        return "", stripped
    head, _, rest = stripped.partition(" ")
    return head.lower(), rest.strip()


def _link_for_wa_id(wa_id):
    return WhatsAppLink.objects.filter(wa_id=wa_id).select_related("user").first()


def _try_link(wa_id, name, token_key):
    """Link this WhatsApp number to the user owning `token_key`. Returns a reply string."""
    token_key = (token_key or "").strip()
    if not token_key:
        return handlers.LINK_HELP
    try:
        token = Token.objects.select_related("user").get(key=token_key)
    except Token.DoesNotExist:
        return (
            "That token didn't match any account. Copy the exact ToolBox API "
            "token and send:  /link <token>"
        )
    user = token.user
    if not user.is_active:
        return "That account is inactive."

    WhatsAppLink.objects.update_or_create(
        user=user, defaults={"wa_id": wa_id, "name": name or ""},
    )
    display = getattr(user, "email", "") or getattr(user, "username", "") or "your account"
    return f"Linked to {display}. Send me an expense like '20 chai' and I'll log it."


def _handle_message(message, name_by_wa_id):
    """Turn one WhatsApp message into a sent reply. Returns nothing."""
    wa_id = message.get("from")
    if not wa_id:
        return

    if message.get("type") != "text":
        return _reply(wa_id, "I can only read text messages right now — send it as text.")

    text = (message.get("text") or {}).get("body") or ""
    name = name_by_wa_id.get(wa_id, "")

    command, args = _split_command(text)

    # Linking is available before an account exists.
    if command in ("/link", "/start") and args:
        return _reply(wa_id, _try_link(wa_id, name, args))

    link = _link_for_wa_id(wa_id)
    if link is None:
        help_text = (
            "👋 Let's connect your account.\n\n"
            "Send:  /link <your ToolBox API token>\n\n"
            "(That's the same token ToolBox's API uses for the Authorization "
            "header — find it wherever you copied it from before.)"
        )
        return _reply(wa_id, help_text)

    user = link.user

    if command in ("/start", "/help"):
        return _reply(wa_id, handlers.WELCOME)
    if command == "/link":
        return _reply(wa_id, "You're already linked. Just send me an expense.")
    if command == "/ask":
        reply, _mode = handlers.handle_ask(user, args)
        return _reply(wa_id, reply)
    if command in ("/review", "/insight", "/spending"):
        reply, _mode = handlers.handle_review(user)
        return _reply(wa_id, reply)
    if command == "/lending":
        reply, _mode = handlers.handle_lending(user, args)
        return _reply(wa_id, reply)
    if command == "/split":
        reply, _mode = handlers.handle_split(user, args)
        return _reply(wa_id, reply)
    if command == "/import":
        reply, _mode = handlers.handle_import(link)
        return _reply(wa_id, reply)
    if command.startswith("/"):
        return _reply(wa_id, "Unknown command. Send /help.")

    # Plain text that reads as a spending question is answered, not logged.
    if not link.awaiting_import and handlers.looks_like_analysis_question(text):
        reply, _mode = handlers.handle_ask(user, text)
        return _reply(wa_id, reply)

    # "split 1200 dinner with raj and mira" → a split, without the slash command.
    if not link.awaiting_import and handlers.looks_like_split(text):
        reply, _mode = handlers.handle_split(user, text)
        return _reply(wa_id, reply)

    # Plain text → log it.
    reply, _mode = handlers.handle_expense(user, text, link)
    if reply:
        _reply(wa_id, reply)


def _handle_payload(payload):
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            messages = value.get("messages") or []
            if not messages:
                # Delivery/read-receipt payloads carry a "statuses" list
                # instead of "messages" — nothing to reply to.
                continue
            name_by_wa_id = {
                contact.get("wa_id"): (contact.get("profile") or {}).get("name", "")
                for contact in value.get("contacts") or []
                if contact.get("wa_id")
            }
            for message in messages:
                _handle_message(message, name_by_wa_id)


@csrf_exempt
def webhook(request, secret):
    if not _secret_ok(secret):
        return HttpResponseForbidden("bad secret")

    if request.method == "GET":
        return _verify(request)

    if request.method != "POST":
        return HttpResponse(status=405)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        # Malformed body: 200 so Meta doesn't hammer us retrying it.
        return JsonResponse({"ok": True})

    try:
        _handle_payload(payload)
    except Exception:  # never 500 back to Meta — it would retry forever
        logger.exception("WhatsApp webhook failed to handle payload")

    return JsonResponse({"ok": True})
