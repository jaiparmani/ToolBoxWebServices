from rest_framework import serializers
from .models import HealthMetric


class HealthMetricSerializer(serializers.ModelSerializer):
    """Serializer for HealthMetric CRUD operations"""
    metric_type_display = serializers.CharField(source='get_metric_type_display', read_only=True)

    class Meta:
        model = HealthMetric
        fields = ['id', 'metric_type', 'metric_type_display', 'value', 'unit',
                  'date', 'notes', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_value(self, value):
        if value <= 0:
            raise serializers.ValidationError("Value must be greater than zero.")
        return value
