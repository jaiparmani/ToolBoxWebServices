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
from decimal import ROUND_DOWN, Decimal, InvalidOperation

from llm.client import LLMError, LLMNotConfigured, LLMRateLimited, call_json

from .models import ExpenseCategory

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = ["amount", "transaction_type", "description", "category_name"]
VALID_TRANSACTION_TYPES = {"expense", "income", "debt", "credit"}

# A batch is capped so one paste can't turn into hundreds of model-invented rows.
MAX_BATCH_ITEMS = 50

# Two or three tags describe a purchase; more is noise in the UI.
MAX_TAGS_PER_ITEM = 3


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


_TAG_RULES = (
    "Finally, add up to three short lowercase tags describing the purchase in ways the "
    "category does not - who or what it was for, the occasion, how it was paid. Reuse a "
    "tag from the provided list whenever it fits, since tags are only useful when they "
    "group things together; invent one only when nothing fits. Use an empty list when "
    "nothing meaningful can be said - do not restate the category or the description."
)

SYSTEM_PROMPT = (
    "You convert a short, informally written note about money into a structured expense "
    "record. Notes are often terse shorthand (e.g. \"20 aamras\", \"58 chai vada pav\", "
    "\"got 500 from raj\") - the leading number is almost always the amount.\n"
    "\n"
    + _CLASSIFICATION_RULES + "\n"
    "\n"
    + _TAG_RULES + "\n"
    "\n"
    "The note may say WHEN it happened, often relatively (\"yesterday\", \"2 days ago\", "
    "\"last friday\", \"on the 3rd\"). Resolve it to a real calendar date in YYYY-MM-DD "
    "against the \"Today is ...\" date given in the user message; use null when the note "
    "gives no time at all (the caller then treats it as today). Never return a future date.\n"
    "\n"
    "Respond with ONLY a single JSON object - no markdown fences, no commentary - with "
    "exactly these keys: \"amount\" (positive number), \"transaction_type\" (one of "
    "\"expense\", \"income\", \"debt\", \"credit\"), \"description\" (short string), "
    "\"category_name\" (string), \"date\" (YYYY-MM-DD or null), "
    "\"tags\" (array of 0-3 short lowercase strings)."
)

BATCH_SYSTEM_PROMPT = (
    "You pull every money transaction out of a piece of text and return them as a list.\n"
    "\n"
    "The text can be in any shape - an exported chat log, a typed list, notes with one "
    "entry per line, or a single sentence. Do not assume one transaction per line: a line "
    "or sentence often holds several, separated by commas, \"and\", or nothing at all. "
    "Split them apart. Terse shorthand is normal, and each amount is usually followed (or "
    "preceded) by what it was for.\n"
    "\n"
    "Examples of the splitting expected:\n"
    "  \"20 vada pav 100 chai\"           -> two: 20 vada pav, 100 chai\n"
    "  \"20 vada pav, 100 chai, 250 lunch\" -> three: 20 vada pav, 100 chai, 250 lunch\n"
    "  \"spent 40 on samosa and 90 on metro\" -> two: 40 samosa, 90 metro\n"
    "  \"[28/05/25, 3:21 PM] Jai: 20 aamras\" -> one: 20 aamras, dated 2025-05-28\n"
    "\n"
    + _CLASSIFICATION_RULES + "\n"
    "\n"
    + _TAG_RULES + "\n"
    "\n"
    "Ignore text that isn't a transaction (greetings, chatter, system notices like "
    "\"Messages are end-to-end encrypted\"), and never invent one that isn't there. If an "
    "entry carries a date, report it as \"date\" in YYYY-MM-DD form - a leading DD/MM/YY "
    "timestamp is day first - otherwise use null.\n"
    "\n"
    "Respond with ONLY a single JSON object - no markdown fences, no commentary - shaped "
    "{\"items\": [...]}, where each item has exactly these keys: \"amount\" (positive "
    "number), \"transaction_type\" (one of \"expense\", \"income\", \"debt\", \"credit\"), "
    "\"description\" (short string), \"category_name\" (string), \"date\" (YYYY-MM-DD or "
    "null), \"tags\" (array of 0-3 short lowercase strings). Use {\"items\": []} only when "
    "the text genuinely contains no transaction."
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

    parsed['tags'] = _clean_tags(parsed.get('tags'))
    return parsed


def _clean_tags(value):
    """Normalise the model's tag list. Tags are a nicety - never fail on them."""
    if isinstance(value, str):
        value = [v for v in re.split(r'[,;]', value)]
    if not isinstance(value, list):
        return []

    cleaned = []
    for tag in value:
        if not isinstance(tag, (str, int, float)):
            continue
        tag = str(tag).strip().lstrip('#').lower()[:50]
        if tag and tag not in cleaned:
            cleaned.append(tag)
    return cleaned[:MAX_TAGS_PER_ITEM]


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


def validate_supplied_items(items):
    """Validate rows a client sends back after reviewing them.

    Saving the reviewed rows avoids parsing the same text twice - which cost a
    second model call, and risked storing something other than what the user
    approved, since the pool can answer differently each time. The rows still
    get checked here rather than trusted.
    """
    if not isinstance(items, list):
        raise ExpenseParseNotPossible("items must be a list.")

    validated = []
    for raw in items[:MAX_BATCH_ITEMS]:
        if not isinstance(raw, dict):
            raise ExpenseParseNotPossible("Each item must be an object.")
        try:
            item = _validate_item(dict(raw))
        except LLMError as exc:
            raise ExpenseParseNotPossible(f"Invalid item: {exc}") from exc
        item['date'] = _coerce_date(raw.get('date'))
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


def parse_expense_text(text, known_tags=()):
    """Ask the model to turn one free-text note into an expense dict."""
    text = (text or '').strip()
    if not text:
        raise ExpenseParseNotPossible("No text provided.")

    user_content = (
        f"Today is {date.today().isoformat()}.\n"
        f"Existing categories: {json.dumps(_category_context())}\n"
        f"Existing tags: {json.dumps(list(known_tags))}\n\n"
        f"Note: \"{text}\""
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    result = _translate_errors(
        call_json,
        messages,
        validate=_validate_item,
        expect_key='amount',
        retry_instruction=(
            "That was not usable. Reply with ONLY a JSON object, no prose and no code "
            "fences, with exactly these keys: amount (positive number), transaction_type "
            "(one of \"expense\", \"income\", \"debt\", \"credit\"), description (string), "
            "category_name (string), date (YYYY-MM-DD or null)."
        ),
    )
    # Resolve the model's date to a real date (or None → caller uses today).
    if isinstance(result, dict):
        result['date'] = _coerce_date(result.get('date'))
    return result


def parse_expense_batch(text, known_tags=()):
    """Extract every transaction in a pasted log. Returns a list of expense dicts.

    An empty list is a legitimate result - the paste may hold no transactions.
    """
    text = (text or '').strip()
    if not text:
        raise ExpenseParseNotPossible("No text provided.")

    user_content = (
        f"Existing categories: {json.dumps(_category_context())}\n"
        f"Existing tags: {json.dumps(list(known_tags))}\n\n"
        f"Today's date is {date.today().isoformat()}.\n\n"
        f"Log:\n{text}"
    )
    messages = [
        {"role": "system", "content": BATCH_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    retry_instruction = (
        "That was not usable. Reply with ONLY a JSON object shaped "
        "{\"items\": [...]}, no prose and no code fences, where each item has the keys "
        "amount, transaction_type, description, category_name and date."
    )
    items = _translate_errors(
        call_json, messages, validate=_validate_batch, expect_key='items',
        retry_instruction=retry_instruction, max_tokens=4000,
    )

    # "Nothing here" is a legitimate answer, but it is also what a model returns
    # when it has misread the shape of the text - a comma-separated sentence
    # being the common case. If the text plainly contains amounts, insist once.
    if not items and re.search(r'\d', text):
        insist = messages + [{"role": "user", "content": (
            "That text does contain amounts. Read it again and list every transaction, "
            "remembering that one line or sentence can hold several separated by commas, "
            "\"and\", or nothing at all. Reply with ONLY {\"items\": [...]}."
        )}]
        items = _translate_errors(
            call_json, insist, validate=_validate_batch, expect_key='items',
            retry_instruction=retry_instruction, max_tokens=4000,
        )

    return items


# --------------------------------------------------------------------------
# Natural-language search
#
# The model never writes a query. It returns the same filter fields the REST
# API already accepts, which ExpenseFilter then validates and applies - so a
# bad or hostile answer can only produce a filter combination a user could
# have typed by hand, never arbitrary SQL.
# --------------------------------------------------------------------------

SEARCH_FIELDS = {
    'date_from', 'date_to', 'amount_min', 'amount_max',
    'category', 'transaction_type', 'search',
}

SEARCH_SYSTEM_PROMPT = (
    "You turn a question about someone's spending into a set of filters.\n"
    "\n"
    "Available filters, all optional - use only the ones the question calls for:\n"
    "  date_from, date_to   YYYY-MM-DD, inclusive\n"
    "  amount_min, amount_max   numbers\n"
    "  category             the exact name of one existing category\n"
    "  transaction_type     one of \"expense\", \"income\", \"debt\", \"credit\"\n"
    "  search               free text matched against description, location and payment "
    "method - use it for a thing, shop or person the categories don't cover\n"
    "\n"
    "Resolve relative dates against today's date, which is given to you. \"last month\" "
    "means the whole of the previous calendar month, \"this week\" the current week from "
    "Monday, \"yesterday\" a single day with date_from equal to date_to.\n"
    "\n"
    "Prefer category over search when the question names something that matches an "
    "existing category, since categories are exact and search is fuzzy. Return no filters "
    "at all - an empty object - when the question asks about everything.\n"
    "\n"
    "Also write \"interpretation\": one short sentence restating what you filtered for, so "
    "the person can see whether you understood them.\n"
    "\n"
    "Respond with ONLY a JSON object - no prose, no code fences - shaped "
    "{\"filters\": {...}, \"interpretation\": \"...\"}."
)


def _validate_search(parsed):
    """Keep only filters the API actually accepts, with values it can use."""
    if not isinstance(parsed, dict):
        raise LLMError("The model did not return a JSON object.")

    filters = parsed.get('filters')
    if filters is None:
        # Some models drop the envelope and return the filters directly.
        filters = {k: v for k, v in parsed.items() if k in SEARCH_FIELDS}
    if not isinstance(filters, dict):
        raise LLMError("The model's response has no \"filters\" object.")

    clean = {}
    for key, value in filters.items():
        if key not in SEARCH_FIELDS or value in (None, '', [], {}):
            continue  # anything unrecognised is dropped rather than passed through
        if key in ('date_from', 'date_to'):
            parsed_date = _coerce_date(value if isinstance(value, str) else None)
            if parsed_date:
                clean[key] = parsed_date.isoformat()
        elif key in ('amount_min', 'amount_max'):
            try:
                clean[key] = float(str(value).replace(',', ''))
            except ValueError:
                continue
        elif key == 'transaction_type':
            if str(value).strip().lower() in VALID_TRANSACTION_TYPES:
                clean[key] = str(value).strip().lower()
        else:
            clean[key] = str(value).strip()

    interpretation = parsed.get('interpretation')
    return {
        'filters': clean,
        'interpretation': (interpretation or '').strip() if isinstance(interpretation, str) else '',
    }


def parse_search_query(question):
    """Turn a question into {'filters': {...}, 'interpretation': '...'}."""
    question = (question or '').strip()
    if not question:
        raise ExpenseParseNotPossible("No question provided.")

    user_content = (
        f"Today's date is {date.today().isoformat()}.\n"
        f"Existing categories: {json.dumps(_category_context())}\n\n"
        f"Question: \"{question}\""
    )
    messages = [
        {"role": "system", "content": SEARCH_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    return _translate_errors(
        call_json, messages, validate=_validate_search, expect_key='filters',
        retry_instruction=(
            "That was not usable. Reply with ONLY a JSON object shaped "
            "{\"filters\": {...}, \"interpretation\": \"...\"}, no prose and no code fences."
        ),
    )


# --------------------------------------------------------------------------
# Lending Q&A — answer a question about splits/lending, grounded in real figures.
# Kept separate from spending analysis on purpose: this reasons ONLY over who
# owes whom, never over what the user spent.
# --------------------------------------------------------------------------

LENDING_SYSTEM_PROMPT = (
    "You answer a person's questions about their lending and borrowing — money "
    "split with friends. You are given a JSON summary of their splits: who owes "
    "them, who they owe, per-person balances, settled vs unsettled amounts, and "
    "recent split activity. All amounts are in Indian rupees (₹).\n"
    "\n"
    "Answer ONLY from the figures provided. Never invent a number, a name or a "
    "date that is not in the data. Quote the actual amounts and people involved. "
    "If the data does not contain what was asked, say so plainly.\n"
    "\n"
    "This is about lending, not spending — do not comment on the person's overall "
    "spending or budgets. Be direct and concrete; 1-4 short sentences is plenty.\n"
    "\n"
    "Respond with ONLY a JSON object: {\"answer\": \"...\"} — no prose outside it, "
    "no code fences."
)


def _validate_lending_answer(parsed):
    if not isinstance(parsed, dict):
        raise LLMError("The model did not return a JSON object.")
    answer = parsed.get('answer')
    if not isinstance(answer, str) or not answer.strip():
        raise LLMError("The model's response has no \"answer\" string.")
    return answer.strip()


def answer_lending_question(question, context):
    """Answer a lending/splits question from a pre-computed context dict.

    ``context`` is the deterministic lending summary built by the view (totals,
    per-person balances, recent splits) — the model only phrases an answer over
    figures we computed, so it can't get the arithmetic wrong. Returns the answer
    string. Raises the same ExpenseParse* errors as the other parsers.
    """
    question = (question or '').strip()
    if not question:
        raise ExpenseParseNotPossible("No question provided.")

    user_content = (
        f"Today's date is {date.today().isoformat()}.\n"
        f"Lending summary:\n{json.dumps(context, indent=2, default=str)}\n\n"
        f"Question: \"{question}\""
    )
    messages = [
        {"role": "system", "content": LENDING_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    return _translate_errors(
        call_json, messages, validate=_validate_lending_answer, expect_key='answer',
        retry_instruction=(
            "That was not usable. Reply with ONLY a JSON object shaped "
            "{\"answer\": \"...\"}, no prose and no code fences."
        ),
    )


# --------------------------------------------------------------------------
# "Can I afford it?" - pull an amount and a date out of a plain question.
#
# The maths is done against the projection in Python; the model only reads the
# sentence. And it degrades gracefully: with no API key, a small regex still
# extracts the amount and a weekday, so the command bar works without any AI.
# --------------------------------------------------------------------------

AFFORD_SYSTEM_PROMPT = (
    "You read a question about whether the user can afford a purchase and extract "
    "just two things: the amount and the date they mean.\n"
    "\n"
    "Today's date is given. Resolve any relative date ('friday', 'next week', "
    "'tomorrow', 'the 5th', 'in 3 days') to an absolute YYYY-MM-DD in the near "
    "future. If no date is mentioned, use today. The amount is the price of the "
    "thing they want to buy - a positive number.\n"
    "\n"
    "Respond with ONLY a JSON object - no markdown, no commentary - exactly: "
    "{\"amount\": <number>, \"date\": \"YYYY-MM-DD\", \"interpretation\": \"<short paraphrase>\"}."
)

_WEEKDAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']


def _next_weekday(name):
    """The next date (today or later) landing on the named weekday."""
    from datetime import timedelta
    idx = _WEEKDAYS.index(name)
    today = date.today()
    delta = (idx - today.weekday()) % 7
    return today + timedelta(days=delta)


def _validate_afford(parsed):
    if not isinstance(parsed, dict):
        raise LLMError("The model did not return a JSON object.")
    amount = parsed.get('amount')
    if isinstance(amount, str):
        try:
            amount = float(re.sub(r'[^0-9.]', '', amount) or 0)
        except ValueError:
            raise LLMError("No usable amount.")
    if not isinstance(amount, (int, float)) or isinstance(amount, bool) or amount <= 0:
        raise LLMError("No usable amount.")
    when = parsed.get('date')
    try:
        date.fromisoformat(when)
    except (TypeError, ValueError):
        when = date.today().isoformat()
    interp = parsed.get('interpretation')
    return {'amount': float(amount), 'date': when,
            'interpretation': interp.strip() if isinstance(interp, str) else ''}


def _afford_fallback(question):
    """No-AI extraction: first number as the amount, a weekday word as the date."""
    m = re.search(r'(\d[\d,]*\.?\d*)', question)
    if not m:
        raise ExpenseParseNotPossible("I couldn't find an amount in that. Try e.g. \"can I afford 500 on friday?\"")
    amount = float(m.group(1).replace(',', ''))
    when = date.today().isoformat()
    low = question.lower()
    if 'tomorrow' in low:
        from datetime import timedelta
        when = (date.today() + timedelta(days=1)).isoformat()
    else:
        for name in _WEEKDAYS:
            if name in low:
                when = _next_weekday(name).isoformat()
                break
    return {'amount': amount, 'date': when, 'interpretation': ''}


def parse_afford_query(question):
    """Extract {'amount', 'date', 'interpretation'} from a can-I-afford question."""
    question = (question or '').strip()
    if not question:
        raise ExpenseParseNotPossible("No question provided.")
    user_content = f"Today's date is {date.today().isoformat()}.\n\nQuestion: \"{question}\""
    messages = [
        {"role": "system", "content": AFFORD_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    try:
        return _translate_errors(
            call_json, messages, validate=_validate_afford, expect_key='amount',
            retry_instruction=(
                "Reply with ONLY {\"amount\": <number>, \"date\": \"YYYY-MM-DD\", "
                "\"interpretation\": \"...\"} - no prose, no code fences."
            ),
        )
    except (ExpenseParseNotPossible, ExpenseParseError):
        # No key or the model failed - fall back so the feature still works.
        return _afford_fallback(question)


# --------------------------------------------------------------------------
# Splitting a bill
#
# "split 1200 dinner with raj and priya" has to yield both an expense and who
# owes what. The model reads the sentence; the arithmetic is done here, because
# shares must sum to the total exactly and that is not something to leave to a
# model that cannot reliably divide 1000 by 3.
# --------------------------------------------------------------------------

SPLIT_SYSTEM_PROMPT = (
    "You read a note about a shared expense and report who it was shared with.\n"
    "\n"
    "By default the person writing paid the bill. List the OTHER people it was "
    "split with, by name, in \"people\". Do not include the writer themselves.\n"
    "\n"
    "If the note says someone ELSE paid (\"raj paid\", \"paid by priya\", "
    "\"priya got this\"), set \"paid_by\" to that person's name. That person "
    "should still appear in \"people\" (they are a participant). When no one "
    "else is named as the payer, omit \"paid_by\" (the writer paid).\n"
    "\n"
    "Reuse a spelling from the known people list when it plainly refers "
    "to the same person, so \"raj\" and \"Raj\" don't become two people.\n"
    "\n"
    "Set \"split_with_me\" to true when the writer is one of the people sharing the cost, "
    "and false only when they clearly just paid on someone else's behalf. Sharing is by "
    "far the common case.\n"
    "\n"
    "  \"split 1200 dinner with raj and priya\"       -> people [raj, priya], split_with_me true\n"
    "  \"900 lunch with raj, raj paid\"                -> people [raj], split_with_me true, paid_by \"raj\"\n"
    "  \"1000 cab with raj and priya, priya paid\"     -> people [raj, priya], split_with_me true, paid_by \"priya\"\n"
    "  \"paid 500 for raj's ticket\"                   -> people [raj], split_with_me false\n"
    "\n"
    "If the note gives explicit amounts per person, put them in \"shares\" as an object "
    "mapping name to number. Otherwise omit shares and the cost will be divided equally.\n"
    "\n"
    + _CLASSIFICATION_RULES + "\n"
    "\n"
    "Respond with ONLY a JSON object - no prose, no code fences - with these keys: "
    "\"amount\" (the full bill, positive number), \"description\" (short string), "
    "\"category_name\" (string), \"people\" (array of names), \"split_with_me\" (boolean), "
    "optionally \"shares\" (object of name to number), and optionally \"paid_by\" "
    "(string, only when someone other than the writer paid)."
)


def _validate_split(parsed):
    """Check the split object, leaving the arithmetic to the caller."""
    if not isinstance(parsed, dict):
        raise LLMError("The model did not return a JSON object.")

    for key in ('amount', 'description', 'category_name'):
        if key not in parsed:
            raise LLMError(f"The model's response is missing {key}.")

    amount = parsed['amount']
    if isinstance(amount, str):
        try:
            amount = float(re.sub(r'[^0-9.\-]', '', amount))
        except ValueError:
            raise LLMError("The model did not return a valid amount.")
    if not isinstance(amount, (int, float)) or isinstance(amount, bool) or amount <= 0:
        raise LLMError("The model did not return a valid positive amount.")
    parsed['amount'] = amount

    for key in ('description', 'category_name'):
        if not isinstance(parsed[key], str) or not parsed[key].strip():
            raise LLMError(f"The model returned an empty {key}.")
        parsed[key] = parsed[key].strip()

    people, seen = [], set()
    for name in parsed.get('people') or []:
        if not isinstance(name, (str, int)):
            continue
        name = str(name).strip()[:100]
        if name and name.lower() not in seen and name.lower() not in {'me', 'myself', 'i'}:
            seen.add(name.lower())
            people.append(name)
    if not people:
        raise LLMError("The model did not name anyone to split with.")
    parsed['people'] = people

    parsed['split_with_me'] = bool(parsed.get('split_with_me', True))

    shares = parsed.get('shares')
    parsed['shares'] = shares if isinstance(shares, dict) else None
    parsed['transaction_type'] = 'expense'
    parsed['tags'] = _clean_tags(parsed.get('tags'))

    paid_by = parsed.get('paid_by')
    if isinstance(paid_by, str) and paid_by.strip() and paid_by.strip().lower() not in {'me', 'myself', 'i'}:
        parsed['paid_by'] = paid_by.strip()
    else:
        parsed['paid_by'] = None

    return parsed


def divide_evenly(total, ways):
    """Split `total` into `ways` shares of whole paise that add back to `total`.

    Rounding each share independently loses or gains a paisa on totals like
    1000/3, which then shows up as a balance that never settles. The remainder
    is handed out one paisa at a time instead.
    """
    total = Decimal(str(total)).quantize(Decimal('0.01'))
    if ways < 1:
        return []
    base = (total / ways).quantize(Decimal('0.01'), rounding=ROUND_DOWN)
    shares = [base] * ways
    remainder = int(((total - base * ways) * 100).to_integral_value())
    for i in range(remainder):
        shares[i] += Decimal('0.01')
    return shares


def compute_shares(amount, people, split_with_me=True, shares=None):
    """Work out what each named person owes. Returns {name: Decimal}.

    Explicit amounts are honoured as given; anything left over is divided
    evenly among whoever wasn't named a figure.
    """
    amount = Decimal(str(amount)).quantize(Decimal('0.01'))
    owed = {}

    explicit = {}
    if shares:
        lowered = {str(k).strip().lower(): v for k, v in shares.items()}
        for name in people:
            value = lowered.get(name.lower())
            if value is None:
                continue
            try:
                parsed = Decimal(str(value)).quantize(Decimal('0.01'))
            except (InvalidOperation, ValueError):
                continue
            if parsed > 0:
                explicit[name] = parsed

    if explicit:
        for name, value in explicit.items():
            owed[name] = value
        remaining_people = [p for p in people if p not in explicit]
        leftover = amount - sum(explicit.values())
        # Whatever is unaccounted for belongs to the payer unless others are
        # still unnamed, in which case they share it.
        if remaining_people and leftover > 0:
            for name, share in zip(remaining_people, divide_evenly(leftover, len(remaining_people))):
                owed[name] = share
        return owed

    ways = len(people) + (1 if split_with_me else 0)
    portions = divide_evenly(amount, ways)
    # The payer keeps the first portion when they shared the cost, so any
    # rounding remainder lands on them rather than on a friend.
    portions = portions[1:] if split_with_me else portions
    for name, share in zip(people, portions):
        owed[name] = share
    return owed


def parse_split_text(text, known_people=(), known_tags=()):
    """Turn "split 1200 dinner with raj and priya" into an expense plus shares."""
    text = (text or '').strip()
    if not text:
        raise ExpenseParseNotPossible("No text provided.")

    user_content = (
        f"Existing categories: {json.dumps(_category_context())}\n"
        f"Known people: {json.dumps(list(known_people))}\n"
        f"Existing tags: {json.dumps(list(known_tags))}\n\n"
        f"Note: \"{text}\""
    )
    messages = [
        {"role": "system", "content": SPLIT_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    parsed = _translate_errors(
        call_json, messages, validate=_validate_split, expect_key='people',
        retry_instruction=(
            "That was not usable. Reply with ONLY a JSON object, no prose and no code "
            "fences, with the keys amount, description, category_name, people, "
            "split_with_me and optionally shares."
        ),
    )
    parsed['owed'] = compute_shares(
        parsed['amount'], parsed['people'], parsed['split_with_me'], parsed['shares'])
    return parsed
