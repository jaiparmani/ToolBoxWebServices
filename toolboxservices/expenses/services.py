"""Parse a short free-text note ("20 aamras", "58 chai vada pav") into a
structured expense using an OpenRouter chat model, matching it against
existing categories.
"""

import json
import logging
import re

import requests
from django.conf import settings

from .models import ExpenseCategory

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
REQUIRED_FIELDS = ["amount", "transaction_type", "description", "category_name"]
VALID_TRANSACTION_TYPES = {"expense", "income", "debt", "credit"}


class ExpenseParseNotPossible(Exception):
    """Caller's fault or nothing to work with - no API key, empty text."""


class ExpenseParseError(Exception):
    """The model call itself failed or came back unusable."""


SYSTEM_PROMPT = (
    "You convert a short, informally written note about money into a structured expense "
    "record. Notes are often terse shorthand (e.g. \"20 aamras\", \"58 chai vada pav\", "
    "\"got 500 from raj\") - the leading number is almost always the amount.\n"
    "\n"
    "Decide transaction_type first: \"expense\" for money spent, \"income\" for money "
    "received, \"debt\" for money borrowed, \"credit\" for money lent out. Default to "
    "\"expense\" unless the note clearly says otherwise.\n"
    "\n"
    "Then pick a category. Each existing category is listed with the transaction_type it "
    "belongs to - you may only reuse one whose type matches the type you chose AND whose "
    "meaning genuinely fits the note. Otherwise invent a short new category name (1-3 "
    "words) describing the kind of spending, such as \"Groceries\", \"Transport\", "
    "\"Lending\" or \"Salary\". Never reuse a category just because its name is familiar.\n"
    "\n"
    "Respond with ONLY a single JSON object - no markdown fences, no commentary - with "
    "exactly these keys: \"amount\" (positive number), \"transaction_type\" (one of "
    "\"expense\", \"income\", \"debt\", \"credit\"), \"description\" (short string), "
    "\"category_name\" (string)."
)


def _openrouter_config():
    """Read API key / model from settings, or explain why we can't call out."""
    api_key = getattr(settings, 'OPENROUTER_API_KEY', '')
    if not api_key:
        raise ExpenseParseNotPossible(
            "OPENROUTER_API_KEY is not set on the server, so expense text cannot be parsed."
        )
    model = getattr(settings, 'OPENROUTER_MODEL', 'openrouter/free')
    return api_key, model


def _call_openrouter(messages, api_key, model):
    try:
        response = requests.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            # json_object mode keeps compliant models from wrapping the object in
            # prose. The default "openrouter/free" pool routes to a different model
            # per call and not all of them honour it, hence the tolerant parsing and
            # the retry in parse_expense_text().
            json={
                "model": model,
                "messages": messages,
                "response_format": {"type": "json_object"},
            },
            timeout=30,
        )
    except requests.RequestException as exc:
        logger.exception("Could not reach OpenRouter while parsing expense text")
        raise ExpenseParseError("Could not reach the OpenRouter API. Check network access.") from exc

    if not response.ok:
        logger.error("OpenRouter returned %s: %s", response.status_code, response.text[:500])
        raise ExpenseParseError(f"OpenRouter API error ({response.status_code}): {response.text[:300]}")

    payload = response.json()
    choices = payload.get('choices') or []
    if not choices:
        raise ExpenseParseError("OpenRouter returned no choices.")

    content = choices[0].get('message', {}).get('content')
    if not content:
        raise ExpenseParseError("OpenRouter returned an empty message.")
    return content


def _json_objects(text):
    """Yield every balanced {...} span in `text`, outermost first.

    Brace counting rather than a regex: small models often emit a reasoning
    block containing its own braces before the real answer, and a greedy
    `\\{.*\\}` swallows everything between the first and last brace. Quoted
    strings and escapes are tracked so braces inside values don't miscount.
    """
    depth = 0
    start = None
    in_string = False
    escaped = False
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == '\\':
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            if depth:
                depth -= 1
                if depth == 0 and start is not None:
                    yield text[start:i + 1]


def _extract_json(text):
    """Pull the expense object out of the model's reply.

    Tolerates markdown fences, prose, and <think> blocks. Returns the first
    balanced object that actually carries the fields we need, so a reasoning
    preamble containing some other object doesn't win.
    """
    # Reasoning models emit these; the answer is always after the block.
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'```(?:json)?|```', '', text)

    try:
        candidate = json.loads(text)
        if isinstance(candidate, dict):
            return candidate
    except json.JSONDecodeError:
        pass

    fallback = None
    for span in _json_objects(text):
        try:
            candidate = json.loads(span)
        except json.JSONDecodeError:
            continue
        if not isinstance(candidate, dict):
            continue
        if 'amount' in candidate:
            return candidate
        if fallback is None:
            fallback = candidate

    if fallback is not None:
        return fallback
    raise ExpenseParseError("Could not find JSON in the model's response.")


def parse_expense_text(text):
    """Ask the model to turn free text into an expense. Returns the parsed dict."""
    text = (text or '').strip()
    if not text:
        raise ExpenseParseNotPossible("No text provided.")

    api_key, model = _openrouter_config()

    # Send each category's transaction_type too - without it the model can't tell
    # which categories are eligible for a given type and reuses whatever name looks
    # familiar (filing "lent 200 to raj" under a food category, for instance).
    existing = [
        {"name": name, "transaction_type": ttype}
        for name, ttype in ExpenseCategory.objects.filter(is_active=True)
        .values_list('name', 'transaction_type')
    ]
    user_content = (
        f"Existing categories: {json.dumps(existing)}\n\n"
        f"Note: \"{text}\""
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    # The free pool routes to a different model per call, so a single bad
    # responder shouldn't fail the request. Retry once with the offending reply
    # echoed back before giving up.
    last_error = None
    for attempt in range(2):
        content = _call_openrouter(messages, api_key, model)
        try:
            return _validate(_extract_json(content))
        except ExpenseParseError as exc:
            last_error = exc
            logger.warning("Expense parse attempt %s failed: %s", attempt + 1, exc)
            messages = messages[:2] + [
                {"role": "assistant", "content": content[:1000]},
                {"role": "user", "content": (
                    "That was not usable. Reply with ONLY a JSON object, no prose and no "
                    "code fences, with exactly these keys: amount (positive number), "
                    "transaction_type (one of \"expense\", \"income\", \"debt\", \"credit\"), "
                    "description (string), category_name (string)."
                )},
            ]

    raise last_error


def _validate(parsed):
    """Check the model's object has everything the caller needs, with usable values."""
    if not isinstance(parsed, dict):
        raise ExpenseParseError("The model did not return a JSON object.")

    missing = [k for k in REQUIRED_FIELDS if k not in parsed]
    if missing:
        raise ExpenseParseError(f"The model's response is missing fields: {', '.join(missing)}")

    # Models sometimes send the amount as a string ("58" or "₹58.00").
    amount = parsed.get('amount')
    if isinstance(amount, str):
        cleaned = re.sub(r'[^0-9.\-]', '', amount)
        try:
            amount = float(cleaned)
        except ValueError:
            raise ExpenseParseError("The model did not return a valid positive amount.")
        parsed['amount'] = amount

    if not isinstance(amount, (int, float)) or isinstance(amount, bool) or amount <= 0:
        raise ExpenseParseError("The model did not return a valid positive amount.")

    transaction_type = parsed.get('transaction_type')
    if isinstance(transaction_type, str):
        transaction_type = transaction_type.strip().lower()
        parsed['transaction_type'] = transaction_type
    if transaction_type not in VALID_TRANSACTION_TYPES:
        raise ExpenseParseError("The model returned an invalid transaction_type.")

    for key in ('description', 'category_name'):
        value = parsed.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ExpenseParseError(f"The model returned an empty {key}.")
        parsed[key] = value.strip()

    return parsed
