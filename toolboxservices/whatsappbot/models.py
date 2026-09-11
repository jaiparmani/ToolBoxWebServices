from django.conf import settings
from django.db import models


class WhatsAppLink(models.Model):
    """Maps a WhatsApp sender to a ToolBox user.

    Mirrors telegrambot.models.TelegramLink: a user links once by sending
    `/link <their ToolBox API token>` to the bot number, we resolve that DRF
    token to its user and remember the pairing here. `wa_id` is the sender's
    phone number the way Meta reports it (digits only, no leading +, e.g.
    "919876543210") — WhatsApp's equivalent of a Telegram chat id.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="whatsapp_link",
    )
    wa_id = models.CharField(max_length=32, unique=True, db_index=True)
    name = models.CharField(max_length=255, blank=True, default="")

    # After /import, the next single-line message is treated as a batch. Kept
    # on the link because the webhook is stateless between requests.
    awaiting_import = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "WhatsApp link"
        verbose_name_plural = "WhatsApp links"

    def __str__(self):
        return f"wa {self.wa_id} -> {self.user}"
