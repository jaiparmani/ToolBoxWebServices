"""Register the webhook URL with Telegram so it starts POSTing updates to us.

Run once after deploy (and again whenever the URL or secret changes):

    python manage.py set_telegram_webhook
    python manage.py set_telegram_webhook --base-url https://toolbox.pythonanywhere.com

The base URL defaults to settings.TELEGRAM_WEBHOOK_BASE_URL (env
TELEGRAM_WEBHOOK_BASE_URL). TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_SECRET must
be set in the environment. Telegram requires HTTPS for webhooks.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from telegrambot import telegram_api


class Command(BaseCommand):
    help = "Register this deployment's webhook URL with Telegram."

    def add_arguments(self, parser):
        parser.add_argument(
            "--base-url",
            default=getattr(settings, "TELEGRAM_WEBHOOK_BASE_URL", "") or "",
            help="Public https origin of the backend, e.g. "
            "https://toolbox.pythonanywhere.com",
        )
        parser.add_argument(
            "--drop-pending",
            action="store_true",
            help="Discard updates queued while the webhook was unset.",
        )

    def handle(self, *args, **options):
        if not telegram_api.is_configured():
            raise CommandError("TELEGRAM_BOT_TOKEN is not set in the environment.")

        secret = (getattr(settings, "TELEGRAM_WEBHOOK_SECRET", "") or "").strip()
        if not secret:
            raise CommandError("TELEGRAM_WEBHOOK_SECRET is not set in the environment.")

        base_url = (options["base_url"] or "").strip().rstrip("/")
        if not base_url:
            raise CommandError(
                "No base URL. Pass --base-url or set TELEGRAM_WEBHOOK_BASE_URL."
            )
        if not base_url.startswith("https://"):
            raise CommandError("Telegram requires an https:// webhook URL.")

        url = f"{base_url}/api/telegram/webhook/{secret}/"
        payload = {
            "url": url,
            # Telegram echoes this in the X-Telegram-Bot-Api-Secret-Token header
            # on every call, a second check on top of the secret in the path.
            "secret_token": secret,
            "allowed_updates": ["message", "edited_message"],
            "drop_pending_updates": bool(options["drop_pending"]),
        }
        result = telegram_api.call("setWebhook", payload)
        if result.get("ok"):
            self.stdout.write(self.style.SUCCESS(f"Webhook set to {url}"))
        else:
            raise CommandError(f"Telegram rejected setWebhook: {result}")

        info = telegram_api.call("getWebhookInfo")
        if info.get("ok"):
            self.stdout.write(f"getWebhookInfo: {info.get('result')}")
