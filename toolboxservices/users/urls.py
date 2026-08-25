from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import UserViewSet, UserProfileView, PasswordChangeView, get_csrf_token, login_view, logout_view

# Create a router for the UserViewSet
router = DefaultRouter()
router.register(r'users', UserViewSet)

urlpatterns = [
    # Include router URLs for UserViewSet
    path('', include(router.urls)),

    # Login / logout endpoints
    path('login/', login_view, name='login'),
    path('logout/', logout_view, name='logout'),

    # User profile endpoints
    path('profile/', UserProfileView.as_view(), name='user-profile'),

    # Password change endpoint
    path('password-change/', PasswordChangeView.as_view(), name='password-change'),

    # CSRF token endpoint
    path('csrf/', get_csrf_token, name='csrf-token'),
]