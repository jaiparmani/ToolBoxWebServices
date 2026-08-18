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
    permission_classes = [AllowAny]
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        """Only return metrics for the specified user, optionally filtered by type/date"""
        userid = self.request.GET.get('userid')
        if not userid:
            return HealthMetric.objects.none()

        try:
            user_id = int(userid)
        except ValueError:
            return HealthMetric.objects.none()

        queryset = HealthMetric.objects.filter(user_id=user_id, user__is_active=True)

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
        """Associate metric with specified user"""
        userid = self.request.GET.get('userid')
        if not userid:
            raise PermissionDenied("userid parameter is required.")

        try:
            user_id = int(userid)
            user = User.objects.get(id=user_id, is_active=True)
            serializer.save(user=user)
        except (ValueError, ObjectDoesNotExist):
            raise PermissionDenied("Invalid userid parameter.")

    @action(detail=False, methods=['get'])
    def summary(self, request):
        """Latest reading plus 7-day stats, broken down by metric type"""
        userid = request.GET.get('userid')
        if not userid:
            return Response({'error': 'userid parameter is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user_id = int(userid)
        except ValueError:
            return Response({'error': 'Invalid userid parameter'}, status=status.HTTP_400_BAD_REQUEST)

        week_ago = timezone.now().date() - timedelta(days=7)
        base_qs = HealthMetric.objects.filter(user_id=user_id, user__is_active=True)

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
