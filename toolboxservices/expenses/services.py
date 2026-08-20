"""Turn free-text notes into structured expenses.

Two entry points, same validation:

  parse_expense_text()  - one note ("20 aamras") -> one expense dict.
  parse_expense_batch() - a pasted chat log -> a list of expense dicts.

The OpenRouter plumbing (retries, tolerant JSON extraction) lives in
llm.client; this module only describes what an expense looks like.
"""

import json
import logging
import re
from datetime import date, datetime

from llm.client import LLMError, LLMNotConfigured, LLMRateLimited, call_json

from .models import ExpenseCategory

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = ["amount", "transaction_type", "description", "category_name"]
VALID_TRANSACTION_TYPES = {"expense", "income", "debt", "credit"}

# A batch is capped so one paste can't turn into hundreds of model-invented rows.
MAX_BATCH_ITEMS = 50


class ExpenseParseNotPossible(Exception):
    """Caller's fault or nothing to work with - no API key, empty text."""


class ExpenseParseError(Exception):
    """The model call itself failed or came back unusable."""


class ExpenseParseRateLimited(ExpenseParseError):
    """The provider is out of quota - a temporary condition, not a failure."""


_CLASSIFICATION_RULES = (
    "Decide transaction_type first: \"expense\" for money spent, \"income\" for money "
    "received, \"debt\" for money borrowed, \"credit\" for money lent out. Default to "
    "\"expense\" unless the note clearly says otherwise.\n"
    "\n"
    "Then pick a category. Each existing category is listed with the transaction_type it "
    "belongs to - you may only reuse one whose type matches the type you chose AND whose "
    "meaning genuinely fits the note. Otherwise invent a short new category name (1-3 "
    "words) describing the kind of spending, such as \"Groceries\", \"Transport\", "
    "\"Lending\" or \"Salary\". Never reuse a category just because its name is familiar."
)

SYSTEM_PROMPT = (
    "You convert a short, informally written note about money into a structured expense "
    "record. Notes are often terse shorthand (e.g. \"20 aamras\", \"58 chai vada pav\", "
    "\"got 500 from raj\") - the leading number is almost always the amount.\n"
    "\n"
    + _CLASSIFICATION_RULES + "\n"
    "\n"
    "Respond with ONLY a single JSON object - no markdown fences, no commentary - with "
    "exactly these keys: \"amount\" (positive number), \"transaction_type\" (one of "
    "\"expense\", \"income\", \"debt\", \"credit\"), \"description\" (short string), "
    "\"category_name\" (string)."
)

BATCH_SYSTEM_PROMPT = (
    "You extract money transactions from a pasted log - usually exported chat messages, "
    "one entry per line, e.g.\n"
    "  [28/05/25, 3:21:37 PM] Jai Parmani: 20 aamras\n"
    "Terse shorthand is normal and the leading number is almost always the amount.\n"
    "\n"
    + _CLASSIFICATION_RULES + "\n"
    "\n"
    "Skip any line that is not a transaction (greetings, chatter, system messages like "
    "\"Messages are end-to-end encrypted\"). Never invent a transaction that is not in the "
    "text. If a line carries a date, report it as \"date\" in YYYY-MM-DD form (a leading "
    "DD/MM/YY timestamp means day first); use null when the line has no date.\n"
    "\n"
    "Respond with ONLY a single JSON object - no markdown fences, no commentary - shaped "
    "{\"items\": [...]}, where each item has exactly these keys: \"amount\" (positive "
    "number), \"transaction_type\" (one of \"expense\", \"income\", \"debt\", \"credit\"), "
    "\"description\" (short string), \"category_name\" (string), \"date\" (YYYY-MM-DD or "
    "null). Return {\"items\": []} if the text contains no transactions."
)


def _category_context():
    """Existing categories with their types.

    Without the type the model can't tell which categories are eligible for a
    given transaction_type and reuses whatever name looks familiar - filing
    "lent 200 to raj" under a food category, for instance.
    """
    return [
        {"name": name, "transaction_type": ttype}
        for name, ttype in ExpenseCategory.objects.filter(is_active=True)
        .values_list('name', 'transaction_type')
    ]


def _validate_item(parsed):
    """Check one expense object has everything the caller needs, with usable values."""
    if not isinstance(parsed, dict):
        raise LLMError("The model did not return a JSON object.")

    missing = [k for k in REQUIRED_FIELDS if k not in parsed]
    if missing:
        raise LLMError(f"The model's response is missing fields: {', '.join(missing)}")

    # Models sometimes send the amount as a string ("58" or "Rs 58.00").
    amount = parsed.get('amount')
    if isinstance(amount, str):
        cleaned = re.sub(r'[^0-9.\-]', '', amount)
        try:
            amount = float(cleaned)
        except ValueError:
            raise LLMError("The model did not return a valid positive amount.")
        parsed['amount'] = amount

    if not isinstance(amount, (int, float)) or isinstance(amount, bool) or amount <= 0:
        raise LLMError("The model did not return a valid positive amount.")

    transaction_type = parsed.get('transaction_type')
    if isinstance(transaction_type, str):
        transaction_type = transaction_type.strip().lower()
        parsed['transaction_type'] = transaction_type
    if transaction_type not in VALID_TRANSACTION_TYPES:
        raise LLMError("The model returned an invalid transaction_type.")

    for key in ('description', 'category_name'):
        value = parsed.get(key)
        if not isinstance(value, str) or not value.strip():
            raise LLMError(f"The model returned an empty {key}.")
        parsed[key] = value.strip()

    return parsed


def _coerce_date(value):
    """Model-supplied date -> date object, or None when absent/unusable.

    A bad date shouldn't sink an otherwise good row; the caller falls back to
    today. Future dates are dropped as hallucinations.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.strptime(value.strip()[:10], '%Y-%m-%d').date()
    except ValueError:
        return None
    if parsed > date.today():
        return None
    return parsed


def _validate_batch(parsed):
    """Validate the {"items": [...]} envelope, dropping unusable rows."""
    if not isinstance(parsed, dict):
        raise LLMError("The model did not return a JSON object.")

    items = parsed.get('items')
    if items is None and isinstance(parsed.get('amount'), (int, float, str)):
        items = [parsed]  # single object instead of the envelope
    if not isinstance(items, list):
        raise LLMError("The model's response has no \"items\" list.")

    validated = []
    for raw in items[:MAX_BATCH_ITEMS]:
        try:
            item = _validate_item(dict(raw) if isinstance(raw, dict) else raw)
        except (LLMError, TypeError) as exc:
            # One malformed row shouldn't discard the whole paste.
            logger.warning("Skipping unusable batch row: %s", exc)
            continue
        item['date'] = _coerce_date(raw.get('date') if isinstance(raw, dict) else None)
        validated.append(item)
    return validated


def _translate_errors(fn, *args, **kwargs):
    """Run an llm.client call, re-raising in this module's vocabulary."""
    try:
        return fn(*args, **kwargs)
    except LLMNotConfigured as exc:
        raise ExpenseParseNotPossible(str(exc)) from exc
    except LLMRateLimited as exc:
        raise ExpenseParseRateLimited(str(exc)) from exc
    except LLMError as exc:
        raise ExpenseParseError(str(exc)) from exc


def parse_expense_text(text):
    """Ask the model to turn one free-text note into an expense dict."""
    text = (text or '').strip()
    if not text:
        raise ExpenseParseNotPossible("No text provided.")

    user_content = (
        f"Existing categories: {json.dumps(_category_context())}\n\n"
        f"Note: \"{text}\""
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    return _translate_errors(
        call_json,
        messages,
        validate=_validate_item,
        expect_key='amount',
        retry_instruction=(
            "That was not usable. Reply with ONLY a JSON object, no prose and no code "
            "fences, with exactly these keys: amount (positive number), transaction_type "
            "(one of \"expense\", \"income\", \"debt\", \"credit\"), description (string), "
            "category_name (string)."
        ),
    )


def parse_expense_batch(text):
    """Extract every transaction in a pasted log. Returns a list of expense dicts.

    An empty list is a legitimate result - the paste may hold no transactions.
    """
    text = (text or '').strip()
    if not text:
        raise ExpenseParseNotPossible("No text provided.")

    user_content = (
        f"Existing categories: {json.dumps(_category_context())}\n\n"
        f"Today's date is {date.today().isoformat()}.\n\n"
        f"Log:\n{text}"
    )
    messages = [
        {"role": "system", "content": BATCH_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    return _translate_errors(
        call_json,
        messages,
        validate=_validate_batch,
        expect_key='items',
        retry_instruction=(
            "That was not usable. Reply with ONLY a JSON object shaped "
            "{\"items\": [...]}, no prose and no code fences, where each item has the keys "
            "amount, transaction_type, description, category_name and date."
        ),
        max_tokens=4000,
    )
