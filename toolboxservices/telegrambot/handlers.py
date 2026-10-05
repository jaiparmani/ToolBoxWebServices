"""Bot command logic, ported from callLLM/expense_bot.py.

The standalone bot POSTed free text to the ToolBox API's quick_add / bulk_add /
ask endpoints and reported what came back — so "all the intelligence lives in
the backend" and the bot always agrees with the website. Running inside Django
we skip the HTTP round-trip and invoke the very same DRF actions in-process via
ExpenseViewSet, which keeps that guarantee exactly (same parsing, same
serializers, same category/tag resolution).

A message arrives already mapped to a ToolBox user (see models.TelegramLink);
these functions turn a message into the reply text to send back.
"""

import html
import logging

from rest_framework.test import APIRequestFactory, force_authenticate

logger = logging.getLogger(__name__)

WELCOME = (
    "I log your money and answer questions about it — the same AI as the app.\n\n"
    "<b>Log an expense</b>\n"
    "  20 vada pav 100 chai      - logs both\n"
    "  had 250 lunch yesterday    - dates work too\n"
    "  /import  (then paste a chat log in the next message)\n\n"
    "<b>Split a bill</b>\n"
    "  /split 1200 dinner with raj and mira\n"
    "  (or just: split 1200 dinner with raj) — everyone you split with who\n"
    "  has linked Telegram gets pinged.\n\n"
    "<b>Analyse your spending</b>\n"
    "  /ask how much on food last month\n"
    "  /ask where did my money go this month\n"
    "  /review        - a full review of your spending\n\n"
    "<b>Lending &amp; splits</b>\n"
    "  /lending who owes me the most?\n"
    "  /lending how much do I owe raj?\n\n"
    "<b>Other</b>\n"
    "  /help          - show this again\n\n"
    "You can also just ask a question in plain text — if it looks like a "
    "question I'll answer it instead of logging it.\n\n"
    "Spending figures count only your own share: money others owe you on a "
    "split is lending, not spending, so it's never counted as spent. Ask about "
    "that side with /lending.\n\n"
    "Everything is computed by ToolBox itself, so it matches the website."
)

LINK_HELP = (
    "First, link your ToolBox account.\n\n"
    "Send:  /link <your ToolBox API token>\n\n"
    "Get the token by logging in to ToolBox (the token is what the app uses "
    "for the Authorization header). Then message me an expense like "
    "'20 chai' and I'll log it."
)


# ── ToolBox API, called in-process ───────────────────────────────────────────

def _call_expense_action(action_name, user, payload):
    """Invoke an ExpenseViewSet action as `user`. Returns (ok, data_or_message).

    Mirrors expense_bot._post: ok when the response status is < 400, otherwise a
    readable sentence pulled from the response body (the API puts things like a
    spent model-quota message in "error", worth passing through verbatim).
    """
    # Imported lazily so importing this module never drags in the whole expenses
    # view stack at Django startup.
    from expenses.views import ExpenseViewSet

    factory = APIRequestFactory()
    request = factory.post(
        f"/api/expenses/expenses/{action_name}/", payload, format="json"
    )
    force_authenticate(request, user=user)
    view = ExpenseViewSet.as_view({"post": action_name})
    try:
        response = view(request)
    except Exception as exc:  # a bug in a view must not 500 the webhook
        logger.exception("Telegram: %s failed", action_name)
        return False, f"Something went wrong handling that: {exc}"

    data = response.data
    if response.status_code >= 400:
        message = "Error."
        if isinstance(data, dict):
            message = data.get("error") or data.get("detail") or message
        return False, str(message)
    return True, data


# ── Formatting (ported verbatim from expense_bot.py) ──────────────────────────

def _rupees(value):
    try:
        return f"₹{float(value):,.2f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return f"₹{value}"


def _describe(expense):
    """One line describing a saved expense, including what the model inferred."""
    parts = [
        f"<b>{_rupees(expense.get('amount'))}</b> "
        f"{html.escape(str(expense.get('description', '')))}"
    ]
    category = (expense.get("category") or {}).get("name")
    if category:
        parts.append(f"· {html.escape(category)}")
    if expense.get("transaction_type") and expense["transaction_type"] != "expense":
        parts.append(f"({expense['transaction_type']})")
    tags = [t.get("name") for t in expense.get("tags") or [] if t.get("name")]
    if tags:
        parts.append("· " + " ".join(f"#{html.escape(t)}" for t in tags))
    return " ".join(parts)


# ── Command handlers → (reply_text, parse_mode) ───────────────────────────────

def handle_expense(user, text, link):
    """Log a single note (quick_add) or a pasted batch (bulk_add)."""
    from expenses.services import looks_like_batch

    text = (text or "").strip()
    if not text:
        return None, None

    # Anything multi-line is almost certainly a paste; so is the message right
    # after /import. A single line with multiple amounts ("20 chai, 100 vada
    # pav") is also a batch. Read all of these as a batch.
    awaiting = link.awaiting_import
    if awaiting:
        link.awaiting_import = False
        link.save(update_fields=["awaiting_import"])

    if awaiting or "\n" in text or looks_like_batch(text):
        ok, data = _call_expense_action(
            "bulk_add", user, {"text": text, "commit": True}
        )
        if not ok:
            return data, None
        if not data.get("count"):
            return (
                data.get("detail") or "I couldn't find any transactions in that.",
                None,
            )
        lines = [f"Logged {data['count']}:"] + [
            f"• {_describe(e)}" for e in data.get("items", [])
        ]
        return "\n".join(lines), "HTML"

    ok, data = _call_expense_action("quick_add", user, {"text": text})
    if not ok:
        return data, None
    return "Logged " + _describe(data), "HTML"


def handle_ask(user, question):
    """Spending Q&A — filter-based, netted of lending, same as the app's `ask`."""
    question = (question or "").strip()
    if not question:
        return "Ask me something: /ask how much on food last month", None

    ok, data = _call_expense_action("ask", user, {"question": question})
    if not ok:
        return data, None

    lines = [
        f"<b>{_rupees(data.get('total'))}</b> across "
        f"{data.get('count', 0)} transactions"
    ]
    if data.get("interpretation"):
        lines.append(html.escape(data["interpretation"]))
    # When part of the raw total was money others owe you, say so — the headline
    # figure is your own share (lending netted out).
    owed = data.get("owed_to_you")
    try:
        owed_val = float(owed or 0)
    except (TypeError, ValueError):
        owed_val = 0.0
    if owed_val > 0:
        lines.append(
            f"<i>(your share; {_rupees(owed_val)} others owe you on these is "
            f"lending, not counted — ask /lending)</i>"
        )
    for row in (data.get("results") or [])[:5]:
        lines.append(f"• {_describe(row)}")
    if (data.get("count") or 0) > 5:
        lines.append(f"…and {data['count'] - 5} more.")
    return "\n".join(lines), "HTML"


def _format_insight(data):
    """Render an insight dict (headline/summary/observations/…) as HTML."""
    lines = []
    if data.get("headline"):
        lines.append(f"<b>{html.escape(str(data['headline']))}</b>")
    if data.get("summary"):
        lines.append(html.escape(str(data["summary"])))

    def _section(title, items):
        items = [i for i in (items or []) if str(i).strip()]
        if not items:
            return
        lines.append(f"\n<b>{title}</b>")
        for i in items:
            lines.append(f"• {html.escape(str(i))}")

    _section("What stands out", data.get("observations"))
    _section("Worth watching", data.get("concerns"))
    _section("Suggestions", data.get("suggestions"))
    return "\n".join(lines) if lines else "Nothing to review yet."


def handle_review(user):
    """A full spending review — the same insight the in-app AI produces."""
    from expenses.assistant import spending_review
    from expenses.services import (
        ExpenseParseError, ExpenseParseNotPossible, ExpenseParseRateLimited,
    )
    try:
        data = spending_review(user)
    except ExpenseParseNotPossible as exc:
        return str(exc), None
    except ExpenseParseRateLimited:
        return "The AI is out of quota for now — try again a bit later.", None
    except ExpenseParseError:
        return "Couldn't put a review together right now. Try again shortly.", None
    except Exception:  # never let a review 500 the webhook
        logger.exception("Telegram: spending review failed")
        return "Something went wrong building your review.", None

    if data.get("type") != "insight":
        # Nothing to analyse — a plain reply came back.
        return data.get("reply") or "Not enough spending logged yet to review.", None
    return _format_insight(data), "HTML"


def handle_lending(user, question):
    """Lending/splits Q&A — the app's `ask_lending`, kept separate from spending."""
    question = (question or "").strip()
    if not question:
        return "Ask about your splits: /lending who owes me the most?", None

    ok, data = _call_expense_action("ask_lending", user, {"question": question})
    if not ok:
        return data, None

    lines = [html.escape(str(data.get("answer", "")))]
    totals = data.get("totals") or {}
    owed_to_you = totals.get("owed_to_you_unsettled")
    you_owe = totals.get("you_owe_unsettled")
    if owed_to_you or you_owe:
        lines.append(
            f"\n<i>Owed to you: {_rupees(owed_to_you or 0)} · "
            f"You owe: {_rupees(you_owe or 0)}</i>"
        )
    return "\n".join(lines), "HTML"


# Plain text that looks like an analysis question should be answered, not logged.
_ANALYSIS_STARTERS = (
    "how much", "how many", "how am i", "how are", "where did", "where's",
    "where is", "what did", "what's my", "whats my", "what is my", "did i",
    "am i", "show me", "show my", "list my", "which ", "why did", "when did",
    "review", "summar", "breakdown", "trend",
)


def looks_like_analysis_question(text):
    """True when plain text reads as a spending question rather than an expense.

    Deliberately conservative: a leading number (how expenses are usually typed,
    "20 chai") is never treated as a question, so logging isn't hijacked. Used to
    route bare questions to /ask without the user needing the slash command.
    """
    t = (text or "").strip().lower()
    if not t or t[0].isdigit() or t.startswith(("₹", "$", "rs", "-")):
        return False
    if "\n" in t:  # a paste is a batch to log, not a question
        return False
    if t.endswith("?"):
        return True
    return t.startswith(_ANALYSIS_STARTERS)


def handle_split(user, text):
    """Split a bill from chat: 'split 1200 dinner with raj and mira'.

    Runs the same split_add action the app uses (LLM parses who/how much), so
    shares and people resolve identically. Anyone you split with who has linked
    Telegram gets pinged automatically (see expenses.views).
    """
    text = (text or "").strip()
    if not text:
        return "Tell me the split, e.g. /split 1200 dinner with raj and mira", None

    ok, data = _call_expense_action("split_add", user, {"text": text})
    if not ok:
        return data, None

    expense = data.get("expense") or {}
    splits = data.get("splits") or []
    who = ", ".join(
        f"{html.escape(str(s.get('person_name') or 'someone'))} {_rupees(s.get('amount'))}"
        for s in splits
    ) or "them"
    lines = [
        f"Split logged: <b>{_rupees(expense.get('amount'))}</b> "
        f"{html.escape(str(expense.get('description', '')))}",
        f"Your share <b>{_rupees(data.get('your_share'))}</b> · "
        f"you're owed {_rupees(data.get('owed_to_you'))} from {who}",
    ]
    return "\n".join(lines), "HTML"


def looks_like_split(text):
    """True for 'split 1200 dinner with raj' — a split typed without the command."""
    t = (text or "").strip().lower()
    return t.startswith("split ") and " with " in t


def handle_import(link):
    link.awaiting_import = True
    link.save(update_fields=["awaiting_import"])
    return "Go ahead - paste the log and I'll pull the transactions out.", None


# ── Confirm/Edit/Discard form for a pending (bank_message) expense ────────────
#
# Two pieces of state on TelegramLink, never both set at once:
#   awaiting_confirmation_id — a Confirm/Edit/Discard keyboard is up for this
#                              expense id, waiting on the tap.
#   awaiting_edit_id         — "✏️ Edit" was tapped; the next plain text is a
#                              corrected one-line version of this expense id.
#
# All of it replies by calling telegram_api.send_message directly rather than
# through the (reply, parse_mode) tuple _handle_message normally returns,
# because only a direct call can carry reply_markup (showing/removing the
# keyboard) — the relay's plain JSON contract to telegram-router has no room
# for it.

def _call_expense_detail_action(method, user, pk, payload=None):
    """Invoke ExpenseViewSet's detail partial_update/destroy as `user`.

    Mirrors _call_expense_action, but for a specific expense id rather than a
    collection-level action.
    """
    from expenses.views import ExpenseViewSet

    factory = APIRequestFactory()
    if method == "patch":
        request = factory.patch(f"/api/expenses/expenses/{pk}/", payload, format="json")
        view = ExpenseViewSet.as_view({"patch": "partial_update"})
    else:
        request = factory.delete(f"/api/expenses/expenses/{pk}/")
        view = ExpenseViewSet.as_view({"delete": "destroy"})
    force_authenticate(request, user=user)
    try:
        response = view(request, pk=pk)
    except Exception as exc:
        logger.exception("Telegram: confirmation %s failed", method)
        return False, f"Something went wrong handling that: {exc}"

    if response.status_code >= 400:
        data = response.data
        message = "Error."
        if isinstance(data, dict):
            message = data.get("error") or data.get("detail") or message
        return False, str(message)
    return True, getattr(response, "data", None)


_CONFIRM_REPLIES = {"confirm", "confirm this expense", "✅ confirm", "✅ confirm this expense"}
_EDIT_REPLIES = {"edit", "edit this expense", "✏️ edit", "✏️ edit this expense"}
_DISCARD_REPLIES = {
    "discard", "discard this expense", "🗑 discard", "🗑 discard this expense",
    "🗑️ discard", "🗑️ discard this expense",
}
_CANCEL_REPLIES = {"cancel", "cancel the edit", "✕ cancel", "✕ cancel the edit"}
# Deliberately no bare "yes"/"no" (or other one-word phrases that read as
# generic chat) in any of the above: the same bot also fronts brain-chat and
# life-rpg behind telegram-router's classifier, and a message that reads as
# plain conversation is exactly what that classifier can hand to one of those
# instead of to ToolBox — see confirm_edit_discard_keyboard's docstring.


def classify_keyboard_reply(text):
    """'confirm' | 'edit' | 'discard' for a tap on that keyboard, else None."""
    t = (text or "").strip().lower()
    if t in _CONFIRM_REPLIES:
        return "confirm"
    if t in _EDIT_REPLIES:
        return "edit"
    if t in _DISCARD_REPLIES:
        return "discard"
    return None


def _prompt_text(expense_data, heading):
    return (
        f"{heading}\n\n{_describe(expense_data)}\n\n"
        f"Tap below, or review it in Money OS → Messages."
    )


def _send_confirmation_prompt(link, expense_data, heading):
    """Send the Confirm/Edit/Discard form and (re)arm awaiting_confirmation_id."""
    from . import telegram_api

    telegram_api.send_message(
        link.chat_id, _prompt_text(expense_data, heading), parse_mode="HTML",
        reply_markup=telegram_api.confirm_edit_discard_keyboard(),
    )
    link.awaiting_confirmation_id = expense_data["id"]
    link.awaiting_edit_id = None
    link.save(update_fields=["awaiting_confirmation_id", "awaiting_edit_id"])


def _advance_to_next_pending(link, exclude_id=None):
    """If another pending expense is waiting, send its Confirm/Edit/Discard
    form next.

    A Telegram reply keyboard is one-per-chat, not one-per-message — sending
    a second one doesn't add a second set of buttons, it silently replaces
    the first's with no way back to them. So only one pending expense is ever
    "live" via Telegram at a time; the rest queue (see notify_new_pending_
    expense) and are picked up here, one at a time, as each live one
    resolves. Any of them is always reachable the slow way too, via Money OS
    → Messages, regardless of Telegram queue order.
    """
    from expenses.models import Expense
    from expenses.serializers import ExpenseSerializer

    qs = Expense.objects.filter(user=link.user, pending_confirmation=True)
    if exclude_id is not None:
        qs = qs.exclude(pk=exclude_id)
    nxt = qs.order_by("created_at").first()
    if nxt:
        _send_confirmation_prompt(link, ExpenseSerializer(nxt).data,
                                   "📩 Next up — please confirm:")


def notify_new_pending_expense(user, expense):
    """Ping `user`'s Telegram about a freshly created pending (bank_message)
    expense: a full Confirm/Edit/Discard form if nothing else is already
    awaiting a reply in this chat, otherwise a plain heads-up — see
    _advance_to_next_pending for why only one can be interactive at once.
    """
    from . import telegram_api
    from .models import TelegramLink
    from expenses.serializers import ExpenseSerializer

    if not telegram_api.is_configured():
        return
    try:
        if not getattr(user.profile, "telegram_notifications_enabled", True):
            return
    except Exception:
        pass  # no profile yet — default to notifying, same as the field's default
    link = TelegramLink.objects.filter(user=user).first()
    if not link:
        return

    data = ExpenseSerializer(expense).data
    if link.awaiting_confirmation_id or link.awaiting_edit_id:
        telegram_api.send_message(
            link.chat_id,
            f"📩 Also logged: {_describe(data)} — I'll ask about this one "
            f"once you're done with the message above.",
            parse_mode="HTML",
        )
        return
    _send_confirmation_prompt(link, data, "📩 New message logged — please confirm:")


def handle_confirmation_tap(link, action):
    """Handle a Confirm/Edit/Discard keyboard tap. Replies directly to Telegram
    (see module docstring) — callers should treat this as fire-and-forget.
    """
    from . import telegram_api
    from expenses.models import Expense

    user = link.user
    expense_id = link.awaiting_confirmation_id
    link.awaiting_confirmation_id = None
    link.save(update_fields=["awaiting_confirmation_id"])

    expense = Expense.objects.filter(
        pk=expense_id, user=user, pending_confirmation=True).first()
    if expense is None:
        telegram_api.send_message(link.chat_id, "That one's already been handled.",
                                   reply_markup=telegram_api.remove_keyboard())
        _advance_to_next_pending(link)
        return

    if action == "edit":
        link.awaiting_edit_id = expense_id
        link.save(update_fields=["awaiting_edit_id"])
        from expenses.serializers import ExpenseSerializer
        current = _describe(ExpenseSerializer(expense).data)
        telegram_api.send_message(
            link.chat_id,
            f"Current: {current}\n\n"
            f"Send the corrected version, same as logging one — e.g. "
            f"\"450 swiggy dinner food\" — and I'll update the amount, "
            f"description and category.",
            parse_mode="HTML",
            reply_markup=telegram_api.cancel_keyboard(),
        )
        return

    if action == "confirm":
        ok, data = _call_expense_detail_action(
            "patch", user, expense_id, {"pending_confirmation": False}
        )
        text = ("Confirmed " + _describe(data)) if ok else f"Couldn't confirm that: {data}"
    else:  # discard
        ok, data = _call_expense_detail_action("delete", user, expense_id)
        text = "Discarded." if ok else f"Couldn't discard that: {data}"

    telegram_api.send_message(link.chat_id, text, parse_mode="HTML" if ok else None,
                               reply_markup=telegram_api.remove_keyboard())
    _advance_to_next_pending(link, exclude_id=expense_id)


def handle_edit_text(link, text):
    """Handle the free-text reply after "✏️ Edit" was tapped: either "cancel"
    (back out, unchanged) or a corrected one-line version to apply. Replies
    directly to Telegram (see module docstring) — fire-and-forget.
    """
    from . import telegram_api
    from expenses.models import Expense
    from expenses.serializers import ExpenseSerializer
    from expenses.services import (
        ExpenseParseError, ExpenseParseNotPossible, ExpenseParseRateLimited,
    )

    user = link.user
    expense_id = link.awaiting_edit_id
    t = (text or "").strip().lower()

    if t in _CANCEL_REPLIES:
        expense = Expense.objects.filter(pk=expense_id, user=user,
                                          pending_confirmation=True).first()
        if expense is None:
            link.awaiting_edit_id = None
            link.save(update_fields=["awaiting_edit_id"])
            telegram_api.send_message(link.chat_id, "That one's already been handled.",
                                       reply_markup=telegram_api.remove_keyboard())
            _advance_to_next_pending(link)
            return
        _send_confirmation_prompt(link, ExpenseSerializer(expense).data,
                                   "📩 Unchanged — please confirm:")
        return

    from expenses.assistant import edit_pending_expense
    try:
        data = edit_pending_expense(user, expense_id, text)
    except Expense.DoesNotExist:
        link.awaiting_edit_id = None
        link.save(update_fields=["awaiting_edit_id"])
        telegram_api.send_message(link.chat_id, "That one's already been handled.",
                                   reply_markup=telegram_api.remove_keyboard())
        _advance_to_next_pending(link)
        return
    except ExpenseParseRateLimited:
        telegram_api.send_message(
            link.chat_id,
            "The AI is out of quota right now — try again shortly, or send \"cancel\".",
            reply_markup=telegram_api.cancel_keyboard(),
        )
        return
    except (ExpenseParseError, ExpenseParseNotPossible):
        telegram_api.send_message(
            link.chat_id,
            "Couldn't make sense of that — try again, e.g. \"450 swiggy dinner "
            "food\", or send \"cancel\".",
            reply_markup=telegram_api.cancel_keyboard(),
        )
        return

    _send_confirmation_prompt(link, data, "✏️ Updated — please confirm:")
