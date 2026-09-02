"""Unregister the webhook with Telegram (e.g. to fall back to local polling).

    python manage.py delete_telegram_webhook
"""

from django.core.management.base import BaseCommand, CommandError

from telegrambot import telegram_api


class Command(BaseCommand):
    help = "Remove the webhook registration from Telegram."

    def add_arguments(self, parser):
        parser.add_argument(
            "--drop-pending",
            action="store_true",
            help="Also discard any queued updates.",
        )

    def handle(self, *args, **options):
        if not telegram_api.is_configured():
            raise CommandError("TELEGRAM_BOT_TOKEN is not set in the environment.")
        result = telegram_api.call(
            "deleteWebhook", {"drop_pending_updates": bool(options["drop_pending"])}
        )
        if result.get("ok"):
            self.stdout.write(self.style.SUCCESS("Webhook removed."))
        else:
            raise CommandError(f"Telegram rejected deleteWebhook: {result}")
