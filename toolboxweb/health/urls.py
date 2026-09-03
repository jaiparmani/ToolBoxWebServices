from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import HealthMetricViewSet

router = DefaultRouter()
router.register(r'metrics', HealthMetricViewSet, basename='health-metric')

urlpatterns = [
    path('', include(router.urls)),
]

# GET    /api/health/metrics/?userid=X&metric_type=weight - list entries
# POST   /api/health/metrics/?userid=X - create entry
# PUT    /api/health/metrics/{id}/?userid=X - update entry
# DELETE /api/health/metrics/{id}/?userid=X - delete entry
# GET    /api/health/metrics/summary/?userid=X - latest + 7-day stats per metric type
