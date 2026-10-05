from django.conf import settings
from django.db import models


class TelegramLink(models.Model):
    """Maps a Telegram chat to a ToolBox user.

    The old standalone bot ran against a single hard-coded ToolBox token, so it
    only ever logged to one account. The webhook serves every chat that messages
    the bot, so it needs to know *which* ToolBox user a given Telegram chat
    belongs to. A user links once by sending `/link <their ToolBox API token>`;
    we resolve that DRF token to its user and remember the pairing here.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="telegram_link",
    )
    # Telegram chat id (fits in 64 bits; stored as BigInteger). Unique so one
    # chat maps to exactly one account.
    chat_id = models.BigIntegerField(unique=True, db_index=True)
    username = models.CharField(max_length=255, blank=True, default="")

    # After /import, the next single-line paste is treated as a batch. Kept on
    # the link because the webhook is stateless between requests.
    awaiting_import = models.BooleanField(default=False)

    # Set when we've sent a "Confirm / Edit / Discard" keyboard for a pending
    # (bank_message) expense and are waiting on the tap. Lets the next plain
    # text from this chat be read as an answer to that prompt rather than a
    # new expense to log — the webhook is stateless, so this is the only
    # place that state can live between requests.
    awaiting_confirmation_id = models.PositiveIntegerField(null=True, blank=True)

    # Set instead of (never alongside) awaiting_confirmation_id once "✏️ Edit"
    # is tapped: the next plain text is read as a corrected one-line version
    # of this expense ("450 swiggy dinner food"), same shape as logging a
    # fresh one, applied to this row instead of creating a new one.
    awaiting_edit_id = models.PositiveIntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Telegram link"
        verbose_name_plural = "Telegram links"

    def __str__(self):
        return f"chat {self.chat_id} -> {self.user}"
