"""Telegram — command logic lives here; the webhook itself does not.

telegram-router (a separate Cloudflare Worker) now owns the one webhook the
shared bot can have: it classifies each inbound message and, for anything
meant for ToolBox, POSTs it to /api/telegram/relay/ below. This app never
parses a raw Telegram update or calls Telegram back directly for that reply —
it just turns {chat_id, username, text} into {reply, parse_mode, buttons} and
lets the router deliver it (buttons is optional — a list of
{"text", "callback_data"} dicts the router renders as a row of inline
buttons; see telegram-router's dispatch.ts/router.ts). Proactive, unrelated
notifications (telegram_api.py's send_message/notify_user — e.g. pinging the
other party of a split, or the pending-expense confirm form) still go
straight to Telegram from here, since those have nothing to do with replying
to an inbound message.

A tap on one of this app's own inline buttons (the pending-expense
Confirm/Edit/Discard form) is *not* a Telegram callback_query reaching us
directly — the router owns the one webhook a bot may have, so it receives the
callback_query, resolves it, and forwards the tap to us through this exact
same relay as a synthetic message whose text is the literal callback_data,
e.g. "tb:confirm:123" (see telegram-router's TOOLBOX_CALLBACK). _handle_message
special-cases that shape up front, deterministically — bypassing the
classifier entirely — since a tap always names its own expense id and must
never be confused with anything a person actually typed.

Security: the relay is gated by a shared secret (TELEGRAM_ROUTER_TOKEN),
compared in constant time so it can't be recovered by timing. The view always
returns 200 for an authenticated-but-unprocessable message so a retry from the
router doesn't compound into a second reply; only a bad token gets a 403.
"""

import hmac
import logging
import re

from django.conf import settings
from django.http import HttpResponseForbidden, JsonResponse

from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from . import handlers
from .models import TelegramLink

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


def _relay_auth_ok(request):
    expected = getattr(settings, "TELEGRAM_ROUTER_TOKEN", "") or ""
    if not expected:
        # Refuse to run wide open. Setting the token is part of deployment.
        logger.error("TELEGRAM_ROUTER_TOKEN is not set; rejecting relay call")
        return False
    header = request.META.get("HTTP_AUTHORIZATION", "")
    given = header[len("Bearer "):] if header.startswith("Bearer ") else ""
    return hmac.compare_digest(given, expected)


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


# `tb:<action>:<expenseId>` — telegram-router's literal forward of a tap on
# this app's own pending-expense inline buttons (see module docstring and
# telegram-router's TOOLBOX_CALLBACK). Never typed by a person.
_TB_CALLBACK_RE = re.compile(r"^tb:(confirm|edit|discard|cancel_edit):(\d+)$")


def _handle_message(chat_id, username, text):
    """Turn one inbound message into (reply, parse_mode, buttons). Any may be
    None/empty; `buttons` is a list of {"text", "callback_data"} dicts for the
    router to render as inline buttons, or None for a plain reply.

    This is exactly today's command dispatch, unchanged in spirit — only the
    outer layer moved: it used to unwrap a raw Telegram update and call
    send_message/send_chat_action itself; now telegram-router does both of
    those, and this just answers the question "what should I say back".
    """
    tb_match = _TB_CALLBACK_RE.match(text.strip())
    if tb_match:
        link = _link_for_chat(chat_id)
        if link is None:  # the link was removed between the prompt and the tap
            return None, None, None
        return handlers.handle_toolbox_callback(link, tb_match.group(1), int(tb_match.group(2)))

    command, args = _split_command(text)

    # Linking is available before an account exists.
    if command in ("/link", "/start") and args:
        return _try_link(chat_id, username, args), None, None

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
        return help_text, None, None

    user = link.user

    # The free-text correction after "✏️ Edit" was tapped — the tap itself is
    # handled above (_TB_CALLBACK_RE); this is the plain message that follows
    # it, so it goes through the classifier like any other text (fine: a
    # money-shaped correction reads unambiguously as ToolBox's).
    if not command and link.awaiting_edit_id:
        return handlers.handle_edit_text(link, text)

    if command in ("/start", "/help"):
        return handlers.WELCOME, None, None
    if command == "/link":
        # Already linked, no token given.
        return "You're already linked. Just send me an expense.", None, None
    if command == "/ask":
        return (*handlers.handle_ask(user, args), None)
    if command in ("/review", "/insight", "/spending"):
        return (*handlers.handle_review(user), None)
    if command == "/lending":
        return (*handlers.handle_lending(user, args), None)
    if command == "/split":
        return (*handlers.handle_split(user, args), None)
    if command == "/import":
        return (*handlers.handle_import(link), None)
    if command.startswith("/"):
        return "Unknown command. Send /help.", None, None

    # Plain text that reads as a spending question is answered, not logged — so a
    # user doesn't have to remember /ask. A leading number or a paste is still a
    # transaction to log (see looks_like_analysis_question).
    if not link.awaiting_import and handlers.looks_like_analysis_question(text):
        return (*handlers.handle_ask(user, text), None)

    # "split 1200 dinner with raj and mira" → a split, without the slash command.
    if not link.awaiting_import and handlers.looks_like_split(text):
        return (*handlers.handle_split(user, text), None)

    # Plain text → log it.
    return (*handlers.handle_expense(user, text, link), None)


@api_view(["POST"])
@authentication_classes([])  # this app's ApiKeyAuthentication also claims "Bearer …"
@permission_classes([AllowAny])  # gated by _relay_auth_ok's shared secret instead
def relay(request):
    """POST {chat_id, username, text} -> {reply, parse_mode, buttons}.

    Called by telegram-router once it has decided a message belongs to
    ToolBox (or forwarded a tap on one of this app's own inline buttons — see
    module docstring). It, not this view, talks to Telegram — this always
    answers 200 with whatever there is (or isn't) to say, so a retry from the
    router can never turn into a second reply landing in the chat.
    """
    if not _relay_auth_ok(request):
        return HttpResponseForbidden("bad token")

    chat_id = request.data.get("chat_id")
    text = request.data.get("text") or ""
    username = request.data.get("username") or ""
    if chat_id is None or not text:
        return JsonResponse({"reply": None, "parse_mode": None})

    try:
        reply, mode, buttons = _handle_message(chat_id, username, text)
    except Exception:  # never 500 back to the router — it would retry forever
        logger.exception("Telegram relay failed to handle message")
        reply, mode, buttons = "Something went wrong handling that. Try again shortly.", None, None

    payload = {"reply": reply, "parse_mode": mode}
    if buttons:
        payload["buttons"] = buttons
    return JsonResponse(payload)
