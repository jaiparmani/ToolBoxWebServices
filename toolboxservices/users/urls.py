from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    UserViewSet, UserProfileView, PasswordChangeView, get_csrf_token, login_view, logout_view,
    PasswordResetRequestView, PasswordResetConfirmView,
    OTPRequestView, OTPVerifyView,
    MpinSetView, MpinLoginView, MpinResetRequestView, MpinResetConfirmView,
)

# Create a router for the UserViewSet
router = DefaultRouter()
router.register(r'users', UserViewSet)

urlpatterns = [
    # Include router URLs for UserViewSet
    path('', include(router.urls)),

    # Login / logout endpoints
    path('login/', login_view, name='login'),
    path('logout/', logout_view, name='logout'),
    # Passwordless email-OTP login (email or username)
    path('otp-request/', OTPRequestView.as_view(), name='otp-request'),
    path('otp-verify/', OTPVerifyView.as_view(), name='otp-verify'),

    # MPIN: set/change (authenticated), sign in, and email-based reset
    path('mpin/set/', MpinSetView.as_view(), name='mpin-set'),
    path('mpin/login/', MpinLoginView.as_view(), name='mpin-login'),
    path('mpin/reset/', MpinResetRequestView.as_view(), name='mpin-reset'),
    path('mpin/reset-confirm/', MpinResetConfirmView.as_view(), name='mpin-reset-confirm'),

    # User profile endpoints
    path('profile/', UserProfileView.as_view(), name='user-profile'),

    # Password change (authenticated) + forgot-password reset (public)
    path('password-change/', PasswordChangeView.as_view(), name='password-change'),
    path('password-reset/', PasswordResetRequestView.as_view(), name='password-reset'),
    path('password-reset-confirm/', PasswordResetConfirmView.as_view(), name='password-reset-confirm'),

    # CSRF token endpoint
    path('csrf/', get_csrf_token, name='csrf-token'),
]