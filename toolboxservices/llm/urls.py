from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import OpenRouterKeyViewSet

router = DefaultRouter()
router.register(r'keys', OpenRouterKeyViewSet, basename='openrouter-key')

urlpatterns = [
    path('', include(router.urls)),
]

# GET    /api/llm/keys/                 - the queue, front first (keys masked)
# POST   /api/llm/keys/                 - add a key   body: {"key": "sk-...", "label": ""}
# DELETE /api/llm/keys/{id}/            - remove a key
# POST   /api/llm/keys/{id}/move_to_back/ - send a key to the end of the queue
