from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ToolCategoryViewSet,
    ToolViewSet,
    ToolExecutionViewSet,
    ArraySumToolView
)

# Create a router for the ViewSets
router = DefaultRouter()
router.register(r'categories', ToolCategoryViewSet)
router.register(r'tools', ToolViewSet)
router.register(r'executions', ToolExecutionViewSet)    

urlpatterns = [
    # Include the router URLs for standard CRUD operations
    path('', include(router.urls)),

    # Custom endpoint for array sum tool
    path('array-sum/', ArraySumToolView.as_view(), name='array-sum'),

    # Additional custom tool endpoints can be added here
    # path('other-tool/', OtherToolView.as_view(), name='other-tool'),
]