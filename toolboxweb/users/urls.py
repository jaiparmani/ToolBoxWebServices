from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import UserViewSet, UserProfileView, PasswordChangeView, LoginView, LogoutView, get_csrf_token

# Create a router for the UserViewSet
router = DefaultRouter()
router.register(r'users', UserViewSet)

urlpatterns = [
    # Include router URLs for UserViewSet
    path('', include(router.urls)),

    # User profile endpoints
    path('profile/', UserProfileView.as_view(), name='user-profile'),

    # Password change endpoint
    path('password-change/', PasswordChangeView.as_view(), name='password-change'),

    # Login endpoint
    path('login/', LoginView, name='login'),

    # Logout endpoint
    path('logout/', LogoutView, name='logout'),

    # CSRF token endpoint
    path('csrf/', get_csrf_token, name='csrf-token'),
]