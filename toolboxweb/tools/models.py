from django.db import models
import json


class ToolCategory(models.Model):
    """Model for tool categories like Math, Text, Conversion, etc."""

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'Tool Categories'

    def __str__(self):
        return self.name


class Tool(models.Model):
    """Model for individual tools in the toolbox"""

    INPUT_TYPE_CHOICES = [
        ('text', 'Text'),
        ('number', 'Number'),
        ('array', 'Array'),
        ('json', 'JSON'),
        ('file', 'File'),
    ]

    OUTPUT_TYPE_CHOICES = [
        ('text', 'Text'),
        ('number', 'Number'),
        ('array', 'Array'),
        ('json', 'JSON'),
        ('boolean', 'Boolean'),
    ]

    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
        ('maintenance', 'Maintenance'),
    ]

    name = models.CharField(max_length=200)
    description = models.TextField()
    category = models.ForeignKey(ToolCategory, on_delete=models.CASCADE, related_name='tools')
    input_type = models.CharField(max_length=20, choices=INPUT_TYPE_CHOICES, default='text')
    output_type = models.CharField(max_length=20, choices=OUTPUT_TYPE_CHOICES, default='text')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        unique_together = ['name', 'category']

    def __str__(self):
        return f"{self.category.name} - {self.name}"


class ToolExecution(models.Model):
    """Model for recording tool usage and execution results"""

    STATUS_CHOICES = [
        ('success', 'Success'),
        ('failed', 'Failed'),
        ('running', 'Running'),
    ]

    tool = models.ForeignKey(Tool, on_delete=models.CASCADE, related_name='executions')
    input_data = models.JSONField()  # Stores the input parameters/data
    output_data = models.JSONField(null=True, blank=True)  # Stores the result
    execution_time = models.FloatField(null=True, blank=True, help_text='Execution time in seconds')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='running')
    error_message = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Tool Execution'

    def __str__(self):
        return f"{self.tool.name} execution at {self.created_at}"
