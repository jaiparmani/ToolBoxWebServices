"""Stored OpenRouter credentials, rotated round-robin.

One free-tier key allows a limited number of model requests per day across
every feature. Holding several keys and spreading calls over them multiplies
that ceiling, and lets a key that has hit its cap sit out until it resets
instead of failing the request.

Keys are stored as given - anyone with database access can read them, same as
the environment variable this replaces. Rotate them at the provider if the
database is ever exposed.
"""

from django.db import models
from django.db.models import F, Q
from django.utils import timezone


class OpenRouterKeyQuerySet(models.QuerySet):
    def usable(self):
        """Active keys that aren't sitting out a rate-limit cooldown.

        Ordered least-recently-used first, which is what makes the rotation
        round-robin: whichever key has waited longest goes next.
        """
        now = timezone.now()
        return (
            self.filter(is_active=True)
            .filter(Q(rate_limited_until__isnull=True) | Q(rate_limited_until__lte=now))
            .order_by(F('last_used_at').asc(nulls_first=True), 'id')
        )


class OpenRouterKey(models.Model):
    """One API key in the rotation."""

    label = models.CharField(
        max_length=100, blank=True,
        help_text='Something to recognise this key by, e.g. the account it belongs to.')
    key = models.CharField(max_length=255, unique=True)
    is_active = models.BooleanField(
        default=True, help_text='Uncheck to take a key out of rotation without deleting it.')

    # Rotation state
    last_used_at = models.DateTimeField(null=True, blank=True)
    use_count = models.PositiveIntegerField(default=0)

    # Set when the provider reports the key's quota is spent. The key is
    # skipped until this passes, so one exhausted key doesn't fail a request
    # that another key could serve.
    rate_limited_until = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = OpenRouterKeyQuerySet.as_manager()

    class Meta:
        ordering = ['id']
        verbose_name = 'OpenRouter key'

    def __str__(self):
        return f"{self.label or 'key'} ({self.masked})"

    @property
    def masked(self):
        """Never print a whole key - logs and command output use this."""
        if len(self.key) <= 14:
            return f"{self.key[:4]}...{self.key[-2:]}"
        return f"{self.key[:12]}...{self.key[-4:]}"

    @property
    def is_cooling_down(self):
        return bool(self.rate_limited_until and self.rate_limited_until > timezone.now())

    def mark_used(self):
        """Record a call. Moves this key to the back of the rotation."""
        now = timezone.now()
        # F() so concurrent calls can't lose a count to a read-modify-write race.
        type(self).objects.filter(pk=self.pk).update(
            last_used_at=now, use_count=F('use_count') + 1)
        self.last_used_at = now

    def mark_rate_limited(self, until=None, message=''):
        """Take this key out of rotation until its quota resets.

        Without a reset time from the provider, sit out an hour rather than
        retrying immediately and burning the request.
        """
        until = until or (timezone.now() + timezone.timedelta(hours=1))
        type(self).objects.filter(pk=self.pk).update(
            rate_limited_until=until, last_error=message[:500])
        self.rate_limited_until = until

    def clear_rate_limit(self):
        type(self).objects.filter(pk=self.pk).update(rate_limited_until=None, last_error='')
        self.rate_limited_until = None
