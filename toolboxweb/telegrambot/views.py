"""Telegram webhook — the whole bot runs inside the Django web app.

Telegram POSTs each update to POST /api/telegram/webhook/<secret>/. Because this
is served by the normal web worker, the bot is up whenever the backend is up:
there is no separate long-polling process to keep alive (which is exactly what
the standalone script needed, and why it kept going down on PythonAnywhere,
where always-on tasks are limited).

Security: the <secret> path segment must match settings.TELEGRAM_WEBHOOK_SECRET,
and, when set with a secret_token, Telegram also echoes it in the
X-Telegram-Bot-Api-Secret-Token header — we check both. The view always returns
200 for an authenticated-but-unprocessable update so Telegram doesn't retry it
forever; only a bad secret gets a 403.
"""

import json
import logging

from django.conf import settings
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from . import handlers
from .models import TelegramLink
from .telegram_api import send_chat_action, send_message

logger = logging.getLogger(__name__)


def _link_state(user):
    """Serialise the signed-in user's Telegram link for the settings screen."""
    link = TelegramLink.objects.filter(user=user).first()
    if not link:
        return {"linked": False, "telegram_id": None, "username": ""}
    return {"linked": True, "telegram_id": link.chat_id, "username": link.username}


@api_view(["GET", "POST", "DELETE"])
@permission_classes([IsAuthenticated])
def telegram_link(request):
    """Link/unlink the signed-in ToolBox account to a Telegram chat from Settings.

    GET    -> current link state.
    POST   {telegram_id} -> link this account to that Telegram chat id (for a
             private chat, the chat id is the same as the user's Telegram id).
             The bot replies with this id when an unlinked chat messages it.
    DELETE -> unlink.

    A telegram id already tied to a *different* account is rejected, so one
    person can't silently redirect another's chat to their books.
    """
    if request.method == "GET":
        return Response(_link_state(request.user))

    if request.method == "DELETE":
        TelegramLink.objects.filter(user=request.user).delete()
        return Response(_link_state(request.user))

    raw = request.data.get("telegram_id")
    try:
        chat_id = int(str(raw).strip())
    except (TypeError, ValueError):
        return Response(
            {"error": "Enter your numeric Telegram ID (message the bot and it will reply with it)."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    clash = TelegramLink.objects.filter(chat_id=chat_id).exclude(user=request.user).first()
    if clash is not None:
        return Response(
            {"error": "That Telegram ID is already linked to another account."},
            status=status.HTTP_409_CONFLICT,
        )

    TelegramLink.objects.update_or_create(
        user=request.user, defaults={"chat_id": chat_id},
    )
    return Response(_link_state(request.user))


def _secret_ok(request, secret):
    expected = getattr(settings, "TELEGRAM_WEBHOOK_SECRET", "") or ""
    if not expected:
        # Refuse to run wide open. Setting the secret is part of deployment.
        logger.error("TELEGRAM_WEBHOOK_SECRET is not set; rejecting webhook call")
        return False
    header = request.META.get("HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN", "")
    # The path secret is the primary gate; the header is Telegram's own echo and
    # only checked when present, so a mismatch there can't lock out a correct URL.
    if secret != expected:
        return False
    if header and header != expected:
        return False
    return True


def _link_for_chat(chat_id):
    return TelegramLink.objects.filter(chat_id=chat_id).select_related("user").first()


def _try_link(chat_id, username, token_key):
    """Link this chat to the user owning `token_key`. Returns a reply string."""
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

    link, _ = TelegramLink.objects.update_or_create(
        user=user,
        defaults={"chat_id": chat_id, "username": username or ""},
    )
    # A chat_id can only belong to one link (unique); update_or_create above is
    # keyed on the user, so re-linking the same user just moves the chat over.
    display = getattr(user, "email", "") or getattr(user, "username", "") or "your account"
    return f"Linked to {display}. Send me an expense like '20 chai' and I'll log it."


def _split_command(text):
    """('/ask', 'how much…') for '/ask how much…'; ('', text) for plain text."""
    stripped = text.strip()
    if not stripped.startswith("/"):
        return "", stripped
    head, _, rest = stripped.partition(" ")
    command = head.split("@", 1)[0].lower()  # strip @BotName suffix
    return command, rest.strip()


def _handle_update(update):
    """Turn one Telegram update into a sent reply. Returns nothing."""
    message = update.get("message") or update.get("edited_message")
    if not message:
        return
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return
    text = message.get("text") or ""
    from_user = message.get("from") or {}
    username = from_user.get("username") or from_user.get("first_name") or ""

    command, args = _split_command(text)

    # Linking is available before an account exists.
    if command in ("/link", "/start") and args:
        return send_message(chat_id, _try_link(chat_id, username, args))

    link = _link_for_chat(chat_id)
    if link is None:
        # Unlinked: tell them their Telegram id so they can paste it into
        # Money OS → Settings → Connect Telegram (or link with a token here).
        help_text = (
            "👋 Let's connect your account.\n\n"
            f"Your Telegram ID: {chat_id}\n\n"
            "Open Money OS → Settings → Connect Telegram and paste this ID.\n\n"
            "(Or send /link <your ToolBox API token>.)"
        )
        return send_message(chat_id, help_text)

    user = link.user

    if command in ("/start", "/help"):
        return send_message(chat_id, handlers.WELCOME)
    if command == "/link":
        # Already linked, no token given.
        return send_message(chat_id, "You're already linked. Just send me an expense.")
    if command == "/ask":
        send_chat_action(chat_id)
        reply, mode = handlers.handle_ask(user, args)
        return send_message(chat_id, reply, parse_mode=mode)
    if command == "/import":
        reply, mode = handlers.handle_import(link)
        return send_message(chat_id, reply, parse_mode=mode)
    if command.startswith("/"):
        return send_message(chat_id, "Unknown command. Send /help.")

    # Plain text → log it.
    send_chat_action(chat_id)
    reply, mode = handlers.handle_expense(user, text, link)
    if reply:
        send_message(chat_id, reply, parse_mode=mode)


@csrf_exempt
def webhook(request, secret):
    if request.method != "POST":
        return HttpResponse(status=405)
    if not _secret_ok(request, secret):
        return HttpResponseForbidden("bad secret")

    try:
        update = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        # Malformed body: 200 so Telegram doesn't hammer us retrying it.
        return JsonResponse({"ok": True})

    try:
        _handle_update(update)
    except Exception:  # never 500 back to Telegram — it would retry forever
        logger.exception("Telegram webhook failed to handle update")

    return JsonResponse({"ok": True})
