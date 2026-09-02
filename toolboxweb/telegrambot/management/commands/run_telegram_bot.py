"""Long-polling fallback — the same bot, driven by getUpdates instead of a webhook.

The webhook (POST /api/telegram/webhook/<secret>/) is the primary, always-up
path and needs no separate process. This command exists for local development
(no public HTTPS URL to hand Telegram) and as a fallback if you ever run
somewhere with a spare always-on worker. It reuses the exact same update
handler as the webhook, so behaviour is identical.

    python manage.py run_telegram_bot

Note: a webhook and getUpdates are mutually exclusive. Run
`delete_telegram_webhook` first, or this will get 409 Conflict from Telegram.
On PythonAnywhere this needs an always-on task (a paid feature); prefer the
webhook there.
"""

import time

from django.core.management.base import BaseCommand, CommandError

from telegrambot import telegram_api
from telegrambot.views import _handle_update


class Command(BaseCommand):
    help = "Run the Telegram bot by long-polling (dev/fallback; webhook is primary)."

    def handle(self, *args, **options):
        if not telegram_api.is_configured():
            raise CommandError("TELEGRAM_BOT_TOKEN is not set in the environment.")

        self.stdout.write(self.style.SUCCESS("Polling Telegram (Ctrl-C to stop)…"))
        offset = None
        while True:
            payload = {"timeout": 30, "allowed_updates": ["message", "edited_message"]}
            if offset is not None:
                payload["offset"] = offset
            result = telegram_api.call("getUpdates", payload, timeout=40)
            if not result.get("ok"):
                self.stderr.write(f"getUpdates error: {result}")
                time.sleep(3)
                continue
            for update in result.get("result", []):
                offset = update["update_id"] + 1
                try:
                    _handle_update(update)
                except Exception as exc:  # one bad update mustn't kill the loop
                    self.stderr.write(f"update {update.get('update_id')}: {exc}")
