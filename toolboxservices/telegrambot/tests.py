import json

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.authtoken.models import Token

from .models import TelegramLink
from .views import _split_command


@override_settings(TELEGRAM_ROUTER_TOKEN="test-router-token")
class RelayViewTests(TestCase):
    def setUp(self):
        self.url = reverse("telegrambot:relay")

    def _post(self, body, token="test-router-token"):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token is not None else {}
        return self.client.post(
            self.url, data=json.dumps(body), content_type="application/json", **headers,
        )

    def test_wrong_token_is_forbidden(self):
        response = self._post({"chat_id": 1, "text": "hi"}, token="nope")
        self.assertEqual(response.status_code, 403)

    def test_missing_token_is_forbidden(self):
        response = self._post({"chat_id": 1, "text": "hi"}, token=None)
        self.assertEqual(response.status_code, 403)

    def test_unlinked_chat_gets_the_connect_prompt(self):
        response = self._post({"chat_id": 999, "username": "stranger", "text": "20 chai"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("Your Telegram ID: 999", data["reply"])

    def test_start_command_for_a_linked_user_returns_the_welcome_message(self):
        user = get_user_model().objects.create_user(username="jai", password="x")
        TelegramLink.objects.create(user=user, chat_id=42)
        response = self._post({"chat_id": 42, "username": "jai", "text": "/start"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("I log your money", response.json()["reply"])

    def test_link_command_links_the_chat_to_the_tokens_owner(self):
        user = get_user_model().objects.create_user(username="jai", password="x")
        token = Token.objects.create(user=user)
        response = self._post({"chat_id": 7, "username": "jai", "text": f"/link {token.key}"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Linked to", response.json()["reply"])
        self.assertTrue(TelegramLink.objects.filter(chat_id=7, user=user).exists())


class SplitCommandTests(TestCase):
    def test_plain_text(self):
        self.assertEqual(_split_command("20 chai"), ("", "20 chai"))

    def test_command_with_args(self):
        self.assertEqual(
            _split_command("/ask how much on food"), ("/ask", "how much on food")
        )

    def test_command_strips_botname(self):
        self.assertEqual(_split_command("/start@MyBot abc"), ("/start", "abc"))

    def test_bare_command(self):
        self.assertEqual(_split_command("/help"), ("/help", ""))
