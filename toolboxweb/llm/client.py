"""Shared OpenRouter client for structured (JSON) model calls.

Every LLM feature in this project wants the same thing: send some messages,
get a JSON object back, and be confident the object is usable. The default
"openrouter/free" pool routes each call to a different model, so the hard
part is not the request - it is surviving whatever comes back. Some models
wrap the object in prose, some emit a <think> block first, some return the
amount as a string.

Keep that hardening here so features (expense parsing, insight generation)
only have to describe what they want and how to validate it.
"""

import json
import logging
import re
from datetime import datetime, timezone

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

DEFAULT_RETRY_INSTRUCTION = (
    "That was not usable. Reply with ONLY a JSON object - no prose, no code fences."
)


class LLMNotConfigured(Exception):
    """No API key, or the caller gave us nothing to work with."""


class LLMError(Exception):
    """The call failed, or the response could not be turned into usable JSON."""


class LLMRateLimited(LLMError):
    """Provider quota is exhausted. Not worth retrying - the caller should back off.

    The OpenRouter free tier allows a limited number of model requests per day
    across every feature, so this is an ordinary operating condition rather
    than a bug, and deserves its own status code at the API edge.

    Carries the provider's reset time when it gave one, so a stored key can be
    benched for exactly as long as it needs.
    """

    def __init__(self, message, reset_at=None):
        super().__init__(message)
        self.reset_at = reset_at


def _model():
    return getattr(settings, 'OPENROUTER_MODEL', 'openrouter/free')


def _candidate_keys():
    """Keys to try, in rotation order, as (key_string, record_or_None) pairs.

    Stored keys come first, least-recently-used first. The environment
    variable is the fallback so the app keeps working before any keys are
    added to the database - and so a fresh deployment isn't dead on arrival.
    """
    records = []
    try:
        from .models import OpenRouterKey
        records = list(OpenRouterKey.objects.usable())
    except Exception:  # table not migrated yet, or app not installed
        logger.debug("Stored OpenRouter keys unavailable; falling back to settings")

    candidates = [(record.key, record) for record in records]

    env_key = getattr(settings, 'OPENROUTER_API_KEY', '')
    # Only fall back when no stored key is usable, and never try the same
    # string twice in one call.
    if env_key and env_key not in {key for key, _ in candidates}:
        if not candidates:
            candidates.append((env_key, None))

    if not candidates:
        try:
            from .models import OpenRouterKey
            if OpenRouterKey.objects.filter(is_active=True).exists():
                raise LLMNotConfigured(
                    "Every OpenRouter key has hit its quota. They rotate back in as their "
                    "limits reset; add another key or add credits to use AI features now."
                )
        except LLMNotConfigured:
            raise
        except Exception:
            pass
        raise LLMNotConfigured(
            "No OpenRouter API key is configured, so AI features are unavailable. "
            "Add one with: manage.py openrouter_keys add <key>"
        )
    return candidates


def _post(messages, api_key, model, timeout, max_tokens):
    body = {
        "model": model,
        "messages": messages,
        # json_object mode stops compliant models wrapping the object in prose.
        # Not every model in the free pool honours it, hence extract_json().
        "response_format": {"type": "json_object"},
    }
    if max_tokens:
        body["max_tokens"] = max_tokens

    try:
        response = requests.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        logger.exception("Could not reach OpenRouter")
        raise LLMError("Could not reach the OpenRouter API. Check network access.") from exc

    if response.status_code == 429:
        logger.warning("OpenRouter rate limit hit: %s", response.text[:300])
        message, reset_at = _rate_limit_details(response)
        raise LLMRateLimited(message, reset_at=reset_at)

    if not response.ok:
        logger.error("OpenRouter returned %s: %s", response.status_code, response.text[:500])
        raise LLMError(f"OpenRouter API error ({response.status_code}): {response.text[:300]}")

    payload = response.json()
    choices = payload.get('choices') or []
    if not choices:
        raise LLMError("OpenRouter returned no choices.")

    content = choices[0].get('message', {}).get('content')
    if not content:
        raise LLMError("OpenRouter returned an empty message.")

    # The free pool reports which model actually served the call - worth
    # recording, since it differs between calls.
    usage = payload.get('usage') or {}
    meta = {
        "model": payload.get('model') or model,
        "input_tokens": usage.get('prompt_tokens'),
        "output_tokens": usage.get('completion_tokens'),
    }
    return content, meta


def _rate_limit_details(response):
    """(message, reset_at) - a sentence a user can act on, plus when to retry."""
    reset = None
    try:
        meta = (response.json().get('error') or {}).get('metadata') or {}
        raw = (meta.get('headers') or {}).get('X-RateLimit-Reset')
        if raw:
            reset = datetime.fromtimestamp(int(raw) / 1000, tz=timezone.utc)
    except (ValueError, AttributeError, TypeError):
        pass

    message = "The AI provider's request quota is used up."
    if reset:
        message += f" It resets at {reset.strftime('%Y-%m-%d %H:%M UTC')}."
    message += " Try again after that, or add credits to the OpenRouter account."
    return message, reset


def _json_spans(text):
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


def extract_json(text, expect_key=None):
    """Pull the answer object out of a model reply.

    Tolerates markdown fences, prose and <think> blocks. When `expect_key` is
    given, prefers the first balanced object carrying that key, so a reasoning
    preamble containing some other object doesn't win.
    """
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'```(?:json)?|```', '', text)

    try:
        candidate = json.loads(text)
        if isinstance(candidate, dict):
            return candidate
    except json.JSONDecodeError:
        pass

    fallback = None
    for span in _json_spans(text):
        try:
            candidate = json.loads(span)
        except json.JSONDecodeError:
            continue
        if not isinstance(candidate, dict):
            continue
        if expect_key is None or expect_key in candidate:
            return candidate
        if fallback is None:
            fallback = candidate

    if fallback is not None:
        return fallback
    raise LLMError("Could not find JSON in the model's response.")


def call_json(messages, validate=None, expect_key=None,
              retry_instruction=DEFAULT_RETRY_INSTRUCTION,
              max_attempts=2, timeout=30, max_tokens=None, return_meta=False):
    """Ask for a JSON object, validate it, and retry once on a bad reply.

    `validate` is a callable taking the parsed dict and returning the value to
    hand back (raising LLMError if the object is unusable). Because the free
    pool routes to a different model per call, a retry often lands on a model
    that behaves - so one bad responder shouldn't fail the whole request.

    With `return_meta`, returns (value, meta) where meta carries the model that
    actually served the call and its token counts.
    """
    model = _model()
    original = list(messages)
    last_rate_limit = None

    # Outer loop rotates keys, inner loop retries a key that answered badly.
    # A quota-exhausted key is benched and the next one tried, so N keys give
    # N times the daily headroom rather than failing at the first cap.
    for api_key, record in _candidate_keys():
        messages = original
        last_error = None

        for attempt in range(max_attempts):
            try:
                content, meta = _post(messages, api_key, model, timeout, max_tokens)
            except LLMRateLimited as exc:
                if record:
                    record.mark_rate_limited(exc.reset_at, str(exc))
                    logger.info("Key %s benched until %s", record.masked, exc.reset_at)
                last_rate_limit = exc
                break  # this key is spent - move to the next one

            if record:
                record.mark_used()

            try:
                parsed = extract_json(content, expect_key=expect_key)
                value = validate(parsed) if validate else parsed
                return (value, meta) if return_meta else value
            except LLMError as exc:
                last_error = exc
                logger.warning("LLM attempt %s/%s failed: %s", attempt + 1, max_attempts, exc)
                messages = original + [
                    {"role": "assistant", "content": content[:1000]},
                    {"role": "user", "content": retry_instruction},
                ]

        if last_error is not None:
            # The key worked, the model just wouldn't produce usable JSON.
            # Another key would reach the same pool, so don't burn one.
            raise last_error

    raise last_rate_limit
