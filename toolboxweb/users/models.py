import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone


class UserProfile(models.Model):
    """Extra per-user fields that don't live on Django's built-in User.

    Currently just a phone number - stored so it can identify an account at
    login (a phone entered at sign-in resolves to this user, and the one-time
    code is emailed to them). Delivering codes over SMS is a separate step that
    needs an SMS provider.
    """

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile')
    phone = models.CharField(max_length=20, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Profile for {self.user_id}"


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def ensure_profile(sender, instance, created, **kwargs):
    """Every user has exactly one profile.

    Guarded so a missing profile table (migration not yet applied on a server)
    can never make user creation itself fail — the profile is best-effort.
    """
    if created:
        try:
            UserProfile.objects.get_or_create(user=instance)
        except Exception:
            pass

OTP_TTL_MINUTES = 10       # how long a code stays usable
OTP_MAX_ATTEMPTS = 5       # wrong guesses before a code is burned
OTP_RESEND_SECONDS = 30    # throttle: don't email a fresh code more often than this


class EmailOTP(models.Model):
    """A one-time, email-delivered sign-in code.

    Stored HASHED (a database leak must not hand out working codes), short-lived,
    single-use, and attempt-limited. Requesting a new code invalidates any
    earlier unused one for that user, so only the latest code works.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='email_otps')
    code_hash = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    consumed = models.BooleanField(default=False)
    attempts = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['user', 'consumed'])]

    def __str__(self):
        return f"OTP for {self.user_id} ({'used' if self.consumed else 'active'})"

    @classmethod
    def issue(cls, user):
        """Invalidate any prior unused code and mint a fresh 6-digit one.

        Returns (code, throttled): if a code was emailed within OTP_RESEND_SECONDS
        we don't mint a new one (the existing code still works), and code is None.
        """
        recent = cls.objects.filter(
            user=user, consumed=False,
            created_at__gte=timezone.now() - timedelta(seconds=OTP_RESEND_SECONDS),
        ).exists()
        if recent:
            return None, True
        cls.objects.filter(user=user, consumed=False).update(consumed=True)
        code = f"{secrets.randbelow(1000000):06d}"
        cls.objects.create(
            user=user, code_hash=make_password(code),
            expires_at=timezone.now() + timedelta(minutes=OTP_TTL_MINUTES),
        )
        return code, False

    def is_usable(self):
        return (not self.consumed
                and self.attempts < OTP_MAX_ATTEMPTS
                and timezone.now() < self.expires_at)

    def verify(self, code):
        """Check a submitted code, counting the attempt. Burns the code on success."""
        if not self.is_usable():
            return False
        self.attempts += 1
        ok = check_password(str(code).strip(), self.code_hash)
        if ok:
            self.consumed = True
        self.save(update_fields=['attempts', 'consumed'])
        return ok
