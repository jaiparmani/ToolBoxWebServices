"""Where a model call goes, and what happens when it goes wrong.

The point of these is the routing decision, not the model: with the gateway
configured every call must leave through it carrying this app's client token
and never a provider key, and with it unset the app must fall back to exactly
what it did before.
"""

import json
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from .client import (
    LLMError,
    LLMNotConfigured,
    LLMRateLimited,
    call_json,
    extract_json,
)

GATEWAY = "https://gateway.example.test"
CLIENT_TOKEN = "lgw_toolbox-token"
PROVIDER_KEY = "sk-or-v1-" + "a" * 64


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload


def completion(content, model="stub/model-a"):
    return FakeResponse(200, {
        "model": model,
        "usage": {"prompt_tokens": 5, "completion_tokens": 9},
        "choices": [{"message": {"content": content}}],
    })


MESSAGES = [{"role": "user", "content": "hello"}]


@override_settings(LLM_GATEWAY_URL=GATEWAY, LLM_GATEWAY_TOKEN=CLIENT_TOKEN, OPENROUTER_API_KEY="")
class GatewayRoutingTests(SimpleTestCase):
    def test_call_goes_to_the_gateway_with_the_client_token(self):
        with patch("llm.client.requests.post", return_value=completion('{"ok": true}')) as post:
            value = call_json(MESSAGES)

        self.assertEqual(value, {"ok": True})
        url = post.call_args.args[0]
        headers = post.call_args.kwargs["headers"]
        self.assertEqual(url, f"{GATEWAY}/v1/chat/completions")
        self.assertEqual(headers["Authorization"], f"Bearer {CLIENT_TOKEN}")

    def test_a_provider_key_never_leaves_this_app(self):
        """The whole point of the migration: the key is not here any more."""
        with patch("llm.client.requests.post", return_value=completion('{"ok": true}')) as post:
            call_json(MESSAGES)
        sent = json.dumps({
            "headers": post.call_args.kwargs["headers"],
            "body": post.call_args.kwargs["json"],
        })
        self.assertNotIn("sk-or-v1-", sent)

    def test_no_stored_key_is_needed(self):
        """With no OpenRouterKey rows and no env key, the call still works."""
        with patch("llm.client.requests.post", return_value=completion('{"ok": true}')):
            self.assertEqual(call_json(MESSAGES), {"ok": True})

    def test_salvaging_still_happens_here(self):
        messy = '<think>{"draft": 1}</think>\nSure:\n```json\n{"amount": 42}\n```'
        with patch("llm.client.requests.post", return_value=completion(messy)):
            self.assertEqual(call_json(MESSAGES, expect_key="amount"), {"amount": 42})

    def test_a_rejected_client_token_reads_as_configuration(self):
        rejected = FakeResponse(401, {"detail": "Send Authorization: Bearer <client token>."})
        with patch("llm.client.requests.post", return_value=rejected):
            with self.assertRaises(LLMNotConfigured) as caught:
                call_json(MESSAGES)
        self.assertIn("LLM_GATEWAY_TOKEN", str(caught.exception))

    def test_a_gateway_holding_no_keys_says_where_to_add_them(self):
        empty = FakeResponse(503, {"error": {"code": "no_keys_configured"}})
        with patch("llm.client.requests.post", return_value=empty):
            with self.assertRaises(LLMError) as caught:
                call_json(MESSAGES)
        self.assertIn("not stored in this app", str(caught.exception))

    def test_quota_exhaustion_is_still_its_own_error(self):
        spent = FakeResponse(429, {"error": {"metadata": {"headers": {"X-RateLimit-Reset": "1789000000000"}}}})
        with patch("llm.client.requests.post", return_value=spent):
            with self.assertRaises(LLMRateLimited):
                call_json(MESSAGES)

    def test_validate_and_retry_are_unchanged(self):
        def validate(parsed):
            if "items" not in parsed:
                raise LLMError("needs items")
            return parsed["items"]

        replies = [completion('{"wrong": 1}'), completion('{"items": [1, 2]}')]
        with patch("llm.client.requests.post", side_effect=replies) as post:
            self.assertEqual(call_json(MESSAGES, validate=validate, expect_key="items"), [1, 2])
        self.assertEqual(post.call_count, 2)


@override_settings(LLM_GATEWAY_URL="", LLM_GATEWAY_TOKEN="", OPENROUTER_API_KEY=PROVIDER_KEY)
class FallbackTests(SimpleTestCase):
    """Unset the gateway and the app is exactly where it started."""

    def test_falls_back_to_openrouter_with_the_stored_key(self):
        with patch("llm.client.requests.post", return_value=completion('{"ok": true}')) as post:
            call_json(MESSAGES)
        self.assertEqual(post.call_args.args[0], "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], f"Bearer {PROVIDER_KEY}")


@override_settings(LLM_GATEWAY_URL="", LLM_GATEWAY_TOKEN="", OPENROUTER_API_KEY="")
class NothingConfiguredTests(SimpleTestCase):
    def test_the_error_names_both_ways_to_fix_it(self):
        with self.assertRaises(LLMNotConfigured) as caught:
            call_json(MESSAGES)
        message = str(caught.exception)
        self.assertIn("LLM_GATEWAY_URL", message)
        self.assertIn("openrouter_keys add", message)


class SalvageTests(SimpleTestCase):
    """Unchanged by the migration, asserted so a later cleanup cannot drop it."""

    def test_prose_and_think_blocks(self):
        self.assertEqual(extract_json('<think>{"x":1}</think> here: {"a":2}')["a"], 2)

    def test_expect_key_beats_a_preamble(self):
        self.assertEqual(extract_json('{"reasoning":"x"} {"items":[3]}', "items")["items"], [3])
