from rest_framework import serializers
from .models import Insight


class InsightSerializer(serializers.ModelSerializer):
    """Read-only representation of a stored insight."""
    scope_display = serializers.CharField(source='get_scope_display', read_only=True)

    class Meta:
        model = Insight
        fields = ['id', 'scope', 'scope_display', 'status', 'period_start', 'period_end',
                  'headline', 'summary', 'payload', 'model', 'effort',
                  'input_tokens', 'output_tokens', 'error_message', 'created_at']
        read_only_fields = fields
