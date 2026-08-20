from django.db import models
from django.conf import settings


class Insight(models.Model):
    """A stored LLM analysis of one user's data over a date window.

    Kept generic (scope + JSON payload) so expense/hobby analyses can reuse the
    same table later instead of getting a model each - same reasoning as
    health.HealthMetric using one row shape for every metric type.
    """

    SCOPE_CHOICES = [
        ('health', 'Health'),
        ('expense', 'Expense'),
    ]

    STATUS_CHOICES = [
        ('success', 'Success'),
        ('refused', 'Refused'),
        ('failed', 'Failed'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='insights')
    scope = models.CharField(max_length=20, choices=SCOPE_CHOICES, default='health')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='success')

    # Window the analysis covered
    period_start = models.DateField()
    period_end = models.DateField()

    headline = models.CharField(max_length=255, blank=True)
    summary = models.TextField(blank=True)
    payload = models.JSONField(default=dict, blank=True,
                               help_text='observations / concerns / suggestions / data_gaps')

    # What produced it, for cost tracking and for spotting stale model versions
    model = models.CharField(max_length=60, blank=True)
    effort = models.CharField(max_length=20, blank=True)
    input_tokens = models.IntegerField(null=True, blank=True)
    output_tokens = models.IntegerField(null=True, blank=True)
    error_message = models.TextField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'scope', '-created_at']),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.scope} - {self.period_start} to {self.period_end}"
