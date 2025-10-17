from rest_framework import serializers
from .models import ToolCategory, Tool, ToolExecution


class ToolCategorySerializer(serializers.ModelSerializer):
    """Serializer for ToolCategory model"""
    tool_count = serializers.SerializerMethodField()

    class Meta:
        model = ToolCategory
        fields = ['id', 'name', 'description', 'created_at', 'tool_count']

    def get_tool_count(self, obj):
        return obj.tools.count()


class ToolSerializer(serializers.ModelSerializer):
    """Serializer for Tool model"""
    category_name = serializers.CharField(source='category.name', read_only=True)

    class Meta:
        model = Tool
        fields = ['id', 'name', 'description', 'category', 'category_name',
                 'input_type', 'output_type', 'is_active', 'created_at', 'updated_at']


class ToolExecutionSerializer(serializers.ModelSerializer):
    """Serializer for ToolExecution model"""
    tool_name = serializers.CharField(source='tool.name', read_only=True)
    category_name = serializers.CharField(source='tool.category.name', read_only=True)

    class Meta:
        model = ToolExecution
        fields = ['id', 'tool', 'tool_name', 'category_name', 'input_data',
                 'output_data', 'execution_time', 'status', 'error_message',
                 'created_at']
        read_only_fields = ['execution_time', 'created_at']