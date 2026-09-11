import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from .models import WhatsAppLink
from .views import _split_command, _to_whatsapp

User = get_user_model()


class SplitCommandTests(TestCase):
    def test_plain_text(self):
        self.assertEqual(_split_command("20 chai"), ("", "20 chai"))

    def test_command_with_args(self):
        self.assertEqual(
            _split_command("/ask how much on food"), ("/ask", "how much on food")
        )

    def test_bare_command(self):
        self.assertEqual(_split_command("/help"), ("/help", ""))


class ToWhatsappMarkupTests(TestCase):
    def test_bold_and_italic(self):
        self.assertEqual(_to_whatsapp("<b>20</b> chai"), "*20* chai")
        self.assertEqual(_to_whatsapp("<i>note</i>"), "_note_")

    def test_unescapes_entities(self):
        self.assertEqual(_to_whatsapp("Lending &amp; splits"), "Lending & splits")

    def test_strips_unknown_tags(self):
        self.assertEqual(_to_whatsapp("<u>x</u>"), "x")


@override_settings(WHATSAPP_WEBHOOK_SECRET="testsecret", WHATSAPP_VERIFY_TOKEN="verifyme")
class WebhookVerificationTests(TestCase):
    def test_get_verification_success(self):
        url = reverse("whatsappbot:webhook", args=["testsecret"])
        response = self.client.get(url, {
            "hub.mode": "subscribe",
            "hub.verify_token": "verifyme",
            "hub.challenge": "12345",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "12345")

    def test_get_verification_wrong_token(self):
        url = reverse("whatsappbot:webhook", args=["testsecret"])
        response = self.client.get(url, {
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong",
            "hub.challenge": "12345",
        })
        self.assertEqual(response.status_code, 403)

    def test_bad_path_secret_rejected(self):
        url = reverse("whatsappbot:webhook", args=["nope"])
        response = self.client.get(url, {
            "hub.mode": "subscribe",
            "hub.verify_token": "verifyme",
            "hub.challenge": "12345",
        })
        self.assertEqual(response.status_code, 403)


@override_settings(WHATSAPP_WEBHOOK_SECRET="testsecret", WHATSAPP_VERIFY_TOKEN="verifyme")
class WebhookMessageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="tester", password="pw")
        self.token = Token.objects.create(user=self.user)
        self.url = reverse("whatsappbot:webhook", args=["testsecret"])

    def _post(self, wa_id, text):
        payload = {
            "entry": [{
                "changes": [{
                    "value": {
                        "contacts": [{"wa_id": wa_id, "profile": {"name": "Tester"}}],
                        "messages": [{
                            "from": wa_id,
                            "type": "text",
                            "text": {"body": text},
                        }],
                    },
                }],
            }],
        }
        return self.client.post(self.url, data=json.dumps(payload), content_type="application/json")

    @patch("whatsappbot.views.whatsapp_api.send_message")
    def test_link_via_token(self, mock_send):
        response = self._post("919876543210", f"/link {self.token.key}")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(WhatsAppLink.objects.filter(user=self.user, wa_id="919876543210").exists())
        mock_send.assert_called_once()
        self.assertIn("Linked", mock_send.call_args.args[1])

    @patch("whatsappbot.views.whatsapp_api.send_message")
    def test_unlinked_number_gets_help(self, mock_send):
        response = self._post("919876543210", "20 chai")
        self.assertEqual(response.status_code, 200)
        mock_send.assert_called_once()
        self.assertIn("connect", mock_send.call_args.args[1].lower())

    @patch("whatsappbot.views.handlers.handle_expense")
    @patch("whatsappbot.views.whatsapp_api.send_message")
    def test_linked_plain_text_logs_expense(self, mock_send, mock_handle_expense):
        WhatsAppLink.objects.create(user=self.user, wa_id="919876543210")
        mock_handle_expense.return_value = ("Logged <b>20</b> chai", "HTML")

        response = self._post("919876543210", "20 chai")

        self.assertEqual(response.status_code, 200)
        mock_handle_expense.assert_called_once()
        mock_send.assert_called_once_with("919876543210", "Logged *20* chai")


@override_settings(WHATSAPP_WEBHOOK_SECRET="testsecret", WHATSAPP_VERIFY_TOKEN="verifyme")
class WhatsAppLinkEndpointTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="tester", password="pw")
        self.token = Token.objects.create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.token.key}")
        self.url = reverse("whatsappbot:link")

    def test_get_unlinked(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"linked": False, "wa_id": None, "name": ""})

    def test_post_and_delete(self):
        response = self.client.post(self.url, {"wa_id": "919876543210"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["linked"])

        response = self.client.delete(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["linked"])

    def test_post_rejects_non_numeric(self):
        response = self.client.post(self.url, {"wa_id": "not-a-number"})
        self.assertEqual(response.status_code, 400)
