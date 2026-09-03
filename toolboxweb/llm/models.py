"""OpenRouter API keys, held as a queue.

One free-tier key allows a limited number of model requests per day across
every AI feature. Several keys multiply that: take the key at the front of
the queue, use it, and push it to the back, so calls spread evenly.

A key that comes back rate limited is pushed to the back too and the next one
is tried, so an exhausted key costs a little latency rather than the request.
(A 429 doesn't consume quota, so there is no need to remember which keys are
spent - the queue sorts itself out.)

Keys are stored as given: anyone with database access can read them, exactly
as with the environment variable this replaces.
"""

from django.db import models
from django.db.models import Max


class OpenRouterKey(models.Model):
    """One key in the rotation queue. Lower `position` is nearer the front."""

    key = models.CharField(max_length=255, unique=True)
    label = models.CharField(
        max_length=100, blank=True,
        help_text='Something to recognise this key by, e.g. the account it belongs to.')

    # Queue order. Using a number rather than a timestamp keeps "push to the
    # back" a single UPDATE, and ties are broken by id so the order is stable.
    position = models.BigIntegerField(default=0, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['position', 'id']
        verbose_name = 'OpenRouter key'

    def __str__(self):
        return f"{self.label or 'key'} ({self.masked})"

    @property
    def masked(self):
        """Never show a whole key - the UI, logs and CLI all use this."""
        if len(self.key) <= 14:
            return f"{self.key[:4]}...{self.key[-2:]}"
        return f"{self.key[:12]}...{self.key[-4:]}"

    @classmethod
    def _next_position(cls):
        return (cls.objects.aggregate(Max('position'))['position__max'] or 0) + 1

    def save(self, *args, **kwargs):
        # New keys join at the back of the queue.
        if self._state.adding and not self.position:
            self.position = self._next_position()
        super().save(*args, **kwargs)

    def push_to_back(self):
        """Send this key to the end of the queue, so the next call uses another."""
        position = self._next_position()
        type(self).objects.filter(pk=self.pk).update(position=position)
        self.position = position
