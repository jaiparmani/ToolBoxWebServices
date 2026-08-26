from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import viewsets, status, generics
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.decorators import api_view, permission_classes
from django.views.decorators.csrf import get_token
from django.http import JsonResponse
from .serializers import (
    UserRegistrationSerializer,
    UserProfileSerializer,
    PasswordChangeSerializer
)


def _user_payload(user):
    return {
        'id': user.id,
        'username': user.username,
        'email': user.email,
        'first_name': user.first_name,
        'last_name': user.last_name,
    }


class UserViewSet(viewsets.ModelViewSet):
    """User management. Registration is open; everything else is the caller's
    own record, derived from the auth token — never a client-supplied id."""
    queryset = User.objects.all()

    def get_serializer_class(self):
        if self.action == 'create':
            return UserRegistrationSerializer
        return UserProfileSerializer

    def get_permissions(self):
        # Anyone may register; every other action needs a valid token.
        if self.action == 'create':
            return [AllowAny()]
        return [IsAuthenticated()]

    def get_queryset(self):
        # Only ever the authenticated user's own record.
        if not self.request.user.is_authenticated:
            return User.objects.none()
        return User.objects.filter(id=self.request.user.id, is_active=True)

    def create(self, request, *args, **kwargs):
        """Register and hand back a token, so sign-up logs the user straight in."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        token, _ = Token.objects.get_or_create(user=user)
        return Response(
            {'token': token.key, 'user': _user_payload(user)},
            status=status.HTTP_201_CREATED,
        )


class UserProfileView(generics.RetrieveUpdateAPIView):
    """The authenticated user's own profile."""
    serializer_class = UserProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user


class PasswordChangeView(APIView):
    """Change the authenticated user's password."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        serializer = PasswordChangeSerializer(data=request.data, context={'request': request, 'user': user})
        if serializer.is_valid():
            user.set_password(serializer.validated_data['new_password'])
            user.save()
            # Rotate the token so an old credential can't outlive the change.
            Token.objects.filter(user=user).delete()
            token = Token.objects.create(user=user)
            return Response({'detail': 'Password changed successfully.', 'token': token.key}, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([AllowAny])
def login_view(request):
    """Authenticate by email + password and return an auth token.

    Replaces the old ?userid= "login", which performed no credential check at
    all. The token is what every subsequent API call authenticates with.
    """
    email = (request.data.get('email') or '').strip()
    password = request.data.get('password') or ''
    if not email or not password:
        return Response({'error': 'Email and password are required.'}, status=status.HTTP_400_BAD_REQUEST)

    matches = list(User.objects.filter(email__iexact=email, is_active=True))
    # Generic message either way, so this can't be used to probe which emails exist.
    invalid = Response({'error': 'Invalid email or password.'}, status=status.HTTP_401_UNAUTHORIZED)
    if len(matches) != 1:
        return invalid
    user = matches[0]
    if not user.check_password(password):
        return invalid

    token, _ = Token.objects.get_or_create(user=user)
    return Response(
        {'detail': 'Login successful.', 'token': token.key, 'user': _user_payload(user)},
        status=status.HTTP_200_OK,
    )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def logout_view(request):
    """Invalidate the caller's token."""
    Token.objects.filter(user=request.user).delete()
    return Response({'detail': 'Logged out.'}, status=status.HTTP_200_OK)


class PasswordResetRequestView(APIView):
    """Start a password reset: email a one-time link.

    Uses Django's stateless reset token (tied to the user's current password
    hash and last login), so no reset rows are stored and each link is single-
    use and expires. The response is always a generic success, so this can't be
    used to probe which emails have accounts.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        email = (request.data.get('email') or '').strip()
        generic = Response(
            {'detail': "If that email has an account, a reset link is on its way."},
            status=status.HTTP_200_OK,
        )
        if not email:
            return Response({'error': 'Email is required.'}, status=status.HTTP_400_BAD_REQUEST)

        matches = list(User.objects.filter(email__iexact=email, is_active=True))
        if len(matches) != 1:
            return generic  # unknown or ambiguous email: reveal nothing
        user = matches[0]

        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        frontend = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000').rstrip('/')
        link = f"{frontend}/reset-password?uid={uid}&token={token}"

        send_mail(
            subject="Reset your ToolBox password",
            message=(
                f"Hi {user.first_name or user.username},\n\n"
                f"Use this link to set a new password (it expires and can be used once):\n\n{link}\n\n"
                "If you didn't ask for this, you can ignore this email — your password won't change."
            ),
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@toolbox.local'),
            recipient_list=[user.email],
            fail_silently=True,  # a delivery failure must not reveal the account exists
        )
        return generic


class PasswordResetConfirmView(APIView):
    """Finish a password reset: verify the link and set the new password."""
    permission_classes = [AllowAny]

    def post(self, request):
        uid = request.data.get('uid') or ''
        token = request.data.get('token') or ''
        new_password = request.data.get('new_password') or ''
        confirm = request.data.get('new_password_confirm') or ''

        if new_password != confirm:
            return Response({'error': 'The passwords do not match.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user = User.objects.get(pk=force_str(urlsafe_base64_decode(uid)), is_active=True)
        except (User.DoesNotExist, ValueError, TypeError, OverflowError):
            user = None

        if not user or not default_token_generator.check_token(user, token):
            return Response({'error': 'This reset link is invalid or has expired.'},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            validate_password(new_password, user)
        except ValidationError as exc:
            return Response({'error': ' '.join(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)

        user.set_password(new_password)
        user.save()
        # Rotate the auth token and hand back a fresh one, so the reset logs them
        # straight in and any old credential is dead.
        Token.objects.filter(user=user).delete()
        auth_token = Token.objects.create(user=user)
        return Response({'detail': 'Password reset. You are signed in.', 'token': auth_token.key,
                         'user': _user_payload(user)}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([AllowAny])
def get_csrf_token(request):
    token = get_token(request)
    return JsonResponse({'csrftoken': token})
