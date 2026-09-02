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
    "Send me an expense and I'll log it.\n\n"
    "  20 vada pav 100 chai      - logs both\n"
    "  /ask how much on food last month\n"
    "  /import  (then paste a chat log in the next message)\n\n"
    "Everything is parsed by ToolBox itself, so it matches the website."
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
    text = (text or "").strip()
    if not text:
        return None, None

    # Anything multi-line is almost certainly a paste; so is the message right
    # after /import. Read either as a batch.
    awaiting = link.awaiting_import
    if awaiting:
        link.awaiting_import = False
        link.save(update_fields=["awaiting_import"])

    if awaiting or "\n" in text:
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
    for row in (data.get("results") or [])[:5]:
        lines.append(f"• {_describe(row)}")
    if (data.get("count") or 0) > 5:
        lines.append(f"…and {data['count'] - 5} more.")
    return "\n".join(lines), "HTML"


def handle_import(link):
    link.awaiting_import = True
    link.save(update_fields=["awaiting_import"])
    return "Go ahead - paste the log and I'll pull the transactions out.", None
