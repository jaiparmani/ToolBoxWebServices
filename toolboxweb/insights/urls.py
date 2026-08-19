from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import HealthInsightViewSet

router = DefaultRouter()
router.register(r'health', HealthInsightViewSet, basename='health-insight')

urlpatterns = [
    path('', include(router.urls)),
]

# GET  /api/insights/health/?userid=X           - list past insights (paginated)
# GET  /api/insights/health/{id}/?userid=X      - retrieve one
# GET  /api/insights/health/latest/?userid=X    - most recent successful insight
# POST /api/insights/health/generate/?userid=X  - run the analysis now
#      body: {"days": 30, "force": false}
