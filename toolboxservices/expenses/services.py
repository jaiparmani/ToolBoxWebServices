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
    "Pick the category that best matches from the list you are given. Only invent a new "
    "category name when nothing on the list is a reasonable fit, and keep it short.\n"
    "\n"
    "Default to transaction_type \"expense\" unless the note clearly describes money coming "
    "in (income) or a borrowing/lending relationship (debt/credit).\n"
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
            json={"model": model, "messages": messages},
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


def _extract_json(text):
    """Pull a JSON object out of the model's reply, tolerating markdown fences/preamble."""
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if not match:
        raise ExpenseParseError("Could not find JSON in the model's response.")
    return json.loads(match.group(0))


def parse_expense_text(text):
    """Ask the model to turn free text into an expense. Returns the parsed dict."""
    text = (text or '').strip()
    if not text:
        raise ExpenseParseNotPossible("No text provided.")

    api_key, model = _openrouter_config()

    category_names = list(
        ExpenseCategory.objects.filter(is_active=True).values_list('name', flat=True)
    )
    user_content = (
        f"Existing categories: {json.dumps(category_names)}\n\n"
        f"Note: \"{text}\""
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    content = _call_openrouter(messages, api_key, model)

    try:
        parsed = _extract_json(content)
    except json.JSONDecodeError as exc:
        raise ExpenseParseError("The model returned malformed JSON.") from exc

    missing = [k for k in REQUIRED_FIELDS if k not in parsed]
    if missing:
        raise ExpenseParseError(f"The model's response is missing fields: {', '.join(missing)}")

    if not isinstance(parsed.get('amount'), (int, float)) or parsed['amount'] <= 0:
        raise ExpenseParseError("The model did not return a valid positive amount.")

    if parsed.get('transaction_type') not in VALID_TRANSACTION_TYPES:
        raise ExpenseParseError("The model returned an invalid transaction_type.")

    return parsed
