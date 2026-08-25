from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from .models import OpenRouterKey
from .serializers import OpenRouterKeySerializer


class OpenRouterKeyViewSet(mixins.ListModelMixin,
                           mixins.CreateModelMixin,
                           mixins.DestroyModelMixin,
                           viewsets.GenericViewSet):
    """Manage the shared key queue - admin only.

    List, add and delete only - there is no retrieve, and the serializer keeps
    `key` write-only, so a stored secret can never be read back through the API.
    These keys are shared infrastructure, not per-user, so only staff may touch
    them (a normal user must not be able to drain or poison the pool).
    """
    queryset = OpenRouterKey.objects.all()  # Meta.ordering = queue order
    serializer_class = OpenRouterKeySerializer
    permission_classes = [IsAdminUser]
    pagination_class = None

    @action(detail=True, methods=['post'])
    def move_to_back(self, request, pk=None):
        """Send a key to the end of the queue by hand."""
        record = self.get_object()
        record.push_to_back()
        return Response(self.get_serializer(record).data, status=status.HTTP_200_OK)
