from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework.pagination import PageNumberPagination
from django.core.exceptions import PermissionDenied, ObjectDoesNotExist
from django.contrib.auth.models import User
from django.db.models import Avg, Max, Min, Sum
from django.utils import timezone
from datetime import timedelta

from .models import HealthMetric
from .serializers import HealthMetricSerializer


class StandardResultsSetPagination(PageNumberPagination):
    """Custom pagination for API responses"""
    page_size = 30
    page_size_query_param = 'page_size'
    max_page_size = 100


class HealthMetricViewSet(viewsets.ModelViewSet):
    """ViewSet for HealthMetric CRUD operations"""
    serializer_class = HealthMetricSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        """Only the authenticated user's metrics, optionally filtered by type/date."""
        queryset = HealthMetric.objects.filter(user=self.request.user)

        metric_type = self.request.GET.get('metric_type')
        if metric_type:
            queryset = queryset.filter(metric_type=metric_type)

        date_from = self.request.GET.get('date_from')
        date_to = self.request.GET.get('date_to')
        if date_from:
            queryset = queryset.filter(date__gte=date_from)
        if date_to:
            queryset = queryset.filter(date__lte=date_to)

        return queryset

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=False, methods=['get'])
    def summary(self, request):
        """Latest reading plus 7-day stats, broken down by metric type."""
        week_ago = timezone.now().date() - timedelta(days=7)
        base_qs = HealthMetric.objects.filter(user=request.user)

        result = {}
        for metric_type, label in HealthMetric.METRIC_TYPE_CHOICES:
            type_qs = base_qs.filter(metric_type=metric_type)
            latest = type_qs.order_by('-date', '-created_at').first()
            week_qs = type_qs.filter(date__gte=week_ago)
            week_stats = week_qs.aggregate(
                avg=Avg('value'), total=Sum('value'), min=Min('value'), max=Max('value')
            )

            result[metric_type] = {
                'label': label,
                'latest_value': latest.value if latest else None,
                'latest_date': latest.date if latest else None,
                'unit': latest.unit if latest else HealthMetric.DEFAULT_UNITS.get(metric_type, ''),
                'week_avg': week_stats['avg'],
                'week_total': week_stats['total'],
                'week_min': week_stats['min'],
                'week_max': week_stats['max'],
                'week_count': week_qs.count(),
            }

        return Response(result)
