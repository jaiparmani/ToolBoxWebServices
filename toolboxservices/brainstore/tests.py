"""Where a note to brain goes, and what happens when it goes wrong."""

import json
from unittest.mock import patch

import requests as requests_module
from django.test import SimpleTestCase, override_settings

from .client import BrainError, BrainNotConfigured, ingest

BRAIN_URL = "https://brain.example.test"
BRAIN_TOKEN = "brain-t0ken"


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload


class BrainClientTests(SimpleTestCase):
    def test_not_configured_without_url_or_token(self):
        with self.assertRaises(BrainNotConfigured):
            ingest("some note")

    @override_settings(BRAIN_API_URL=BRAIN_URL, BRAIN_API_TOKEN=BRAIN_TOKEN)
    def test_posts_to_ingest_with_the_bearer_token(self):
        with patch(
            "brainstore.client.requests.post",
            return_value=FakeResponse(200, {"ok": True, "action": "create"}),
        ) as post:
            result = ingest(
                "Spent 2,000 on food this week.", source="toolbox", hint="weekly brief",
            )

        self.assertEqual(result, {"ok": True, "action": "create"})
        url = post.call_args.args[0]
        kwargs = post.call_args.kwargs
        self.assertEqual(url, f"{BRAIN_URL}/ingest")
        self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {BRAIN_TOKEN}")
        self.assertEqual(kwargs["json"], {
            "text": "Spent 2,000 on food this week.",
            "source": "toolbox",
            "hint": "weekly brief",
        })

    @override_settings(BRAIN_API_URL=BRAIN_URL, BRAIN_API_TOKEN=BRAIN_TOKEN)
    def test_hint_is_omitted_when_not_given(self):
        with patch(
            "brainstore.client.requests.post", return_value=FakeResponse(200, {"ok": True}),
        ) as post:
            ingest("a plain note")

        self.assertNotIn("hint", post.call_args.kwargs["json"])

    @override_settings(BRAIN_API_URL=BRAIN_URL, BRAIN_API_TOKEN=BRAIN_TOKEN)
    def test_a_rejected_token_is_reported_as_not_configured(self):
        with patch(
            "brainstore.client.requests.post",
            return_value=FakeResponse(401, {"error": "unauthorized"}),
        ):
            with self.assertRaises(BrainNotConfigured):
                ingest("note")

    @override_settings(BRAIN_API_URL=BRAIN_URL, BRAIN_API_TOKEN=BRAIN_TOKEN)
    def test_a_server_error_is_a_brain_error(self):
        with patch(
            "brainstore.client.requests.post",
            return_value=FakeResponse(500, {"error": "boom"}),
        ):
            with self.assertRaises(BrainError):
                ingest("note")

    @override_settings(BRAIN_API_URL=BRAIN_URL, BRAIN_API_TOKEN=BRAIN_TOKEN)
    def test_a_network_failure_is_a_brain_error(self):
        with patch(
            "brainstore.client.requests.post",
            side_effect=requests_module.ConnectionError("down"),
        ):
            with self.assertRaises(BrainError):
                ingest("note")
