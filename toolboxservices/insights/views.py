from datetime import timedelta

from django.contrib.auth.models import User
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .models import Insight
from .serializers import InsightSerializer
from .services import (
    InsightGenerationError, InsightNotPossible, InsightRateLimited,
    generate_expense_insight, generate_health_insight,
)

# Don't spend a model call regenerating the same day's review unless asked.
REGENERATE_AFTER = timedelta(hours=12)


class StandardResultsSetPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


def _resolve_user(request):
    """Same ?userid= contract the rest of the API uses."""
    userid = request.GET.get('userid')
    if not userid:
        return None, Response({'error': 'userid parameter is required'},
                              status=status.HTTP_400_BAD_REQUEST)
    try:
        return User.objects.get(id=int(userid), is_active=True), None
    except (ValueError, ObjectDoesNotExist):
        return None, Response({'error': 'Invalid userid parameter'},
                              status=status.HTTP_400_BAD_REQUEST)


class BaseInsightViewSet(viewsets.ReadOnlyModelViewSet):
    """Past insights for one scope, plus the endpoint that creates a new one.

    Subclasses set `scope` and `generator`; everything else - caching, failure
    recording, the ?userid= contract - is identical across scopes.
    """
    serializer_class = InsightSerializer
    permission_classes = [AllowAny]
    pagination_class = StandardResultsSetPagination

    scope = None
    generator = None

    def get_queryset(self):
        userid = self.request.GET.get('userid')
        if not userid:
            return Insight.objects.none()
        try:
            user_id = int(userid)
        except ValueError:
            return Insight.objects.none()
        return Insight.objects.filter(user_id=user_id, user__is_active=True, scope=self.scope)

    @action(detail=False, methods=['get'])
    def latest(self, request):
        """Most recent successful review, or 404 if there has never been one."""
        insight = self.get_queryset().filter(status='success').first()
        if not insight:
            return Response({'error': 'No insight has been generated yet.'},
                            status=status.HTTP_404_NOT_FOUND)
        return Response(self.get_serializer(insight).data)

    @action(detail=False, methods=['post'])
    def generate(self, request):
        """Run the analysis now.

        Body (all optional): {"days": 30, "force": false}
        Returns the cached insight instead of calling the model when a recent
        one exists and force is not set.
        """
        user, error = _resolve_user(request)
        if error:
            return error

        try:
            days = int(request.data.get('days', 30))
        except (TypeError, ValueError):
            return Response({'error': 'days must be an integer'}, status=status.HTTP_400_BAD_REQUEST)
        days = max(1, min(days, 180))
        force = bool(request.data.get('force', False))

        if not force:
            recent = self.get_queryset().filter(
                status='success', created_at__gte=timezone.now() - REGENERATE_AFTER
            ).first()
            if recent:
                data = self.get_serializer(recent).data
                data['regenerated'] = False
                return Response(data)

        try:
            parsed, meta = self.generator(user, days=days)
        except InsightNotPossible as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except InsightRateLimited as exc:
            # Quota, not a failed analysis - don't write a "failed" row for it.
            return Response({'error': str(exc)}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        except InsightGenerationError as exc:
            # Record the failure so a run of bad days is visible in the history.
            Insight.objects.create(
                user=user, scope=self.scope, status='failed',
                period_start=timezone.now().date() - timedelta(days=days - 1),
                period_end=timezone.now().date(),
                error_message=str(exc),
            )
            return Response({'error': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        insight = Insight.objects.create(
            user=user,
            scope=self.scope,
            status='success',
            period_start=meta['period_start'],
            period_end=meta['period_end'],
            headline=parsed['headline'][:255],
            summary=parsed['summary'],
            payload={
                'observations': parsed['observations'],
                'concerns': parsed['concerns'],
                'suggestions': parsed['suggestions'],
                'data_gaps': parsed['data_gaps'],
                'entries_analysed': meta['entry_count'],
            },
            model=meta['model'],
            effort=meta['effort'],
            input_tokens=meta['input_tokens'],
            output_tokens=meta['output_tokens'],
        )

        data = self.get_serializer(insight).data
        data['regenerated'] = True
        return Response(data, status=status.HTTP_201_CREATED)


class HealthInsightViewSet(BaseInsightViewSet):
    """Claude-written reviews of logged health metrics."""
    scope = 'health'
    generator = staticmethod(generate_health_insight)


class ExpenseInsightViewSet(BaseInsightViewSet):
    """LLM-written reviews of a period's spending."""
    scope = 'expense'
    generator = staticmethod(generate_expense_insight)
