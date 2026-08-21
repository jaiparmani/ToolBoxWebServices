from rest_framework import serializers

from .models import OpenRouterKey


class OpenRouterKeySerializer(serializers.ModelSerializer):
    """A key as the UI sees it.

    `key` is write-only: once stored, only the masked form ever goes back out,
    so the API can never be used to read the secrets it holds.
    """

    key = serializers.CharField(write_only=True, trim_whitespace=True)
    masked = serializers.CharField(read_only=True)

    class Meta:
        model = OpenRouterKey
        fields = ['id', 'key', 'masked', 'label', 'position', 'created_at']
        read_only_fields = ['id', 'masked', 'position', 'created_at']

    def validate_key(self, value):
        value = value.strip()
        if not value.startswith('sk-'):
            raise serializers.ValidationError(
                "That doesn't look like an OpenRouter key (expected sk-...).")
        if OpenRouterKey.objects.filter(key=value).exists():
            raise serializers.ValidationError('That key is already stored.')
        return value
