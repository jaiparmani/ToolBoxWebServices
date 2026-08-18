from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator
import decimal


class HealthMetric(models.Model):
    """Generic log entry for a personal health metric (weight, water, sleep, steps, ...).

    One table for every metric type instead of a new model per tracker - adding a new
    metric later is just a new METRIC_TYPE_CHOICES entry, not a migration.
    """

    METRIC_TYPE_CHOICES = [
        ('weight', 'Weight'),
        ('water', 'Water Intake'),
        ('sleep', 'Sleep'),
        ('steps', 'Steps'),
    ]

    DEFAULT_UNITS = {
        'weight': 'kg',
        'water': 'ml',
        'sleep': 'hours',
        'steps': 'steps',
    }

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='health_metrics')
    metric_type = models.CharField(max_length=20, choices=METRIC_TYPE_CHOICES)
    value = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(decimal.Decimal('0.01'))]
    )
    unit = models.CharField(max_length=20, blank=True)
    date = models.DateField()
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date', '-created_at']
        indexes = [
            models.Index(fields=['user', 'metric_type', 'date']),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.get_metric_type_display()} - {self.value}{self.unit} - {self.date}"

    def save(self, *args, **kwargs):
        if not self.unit:
            self.unit = self.DEFAULT_UNITS.get(self.metric_type, '')
        super().save(*args, **kwargs)
