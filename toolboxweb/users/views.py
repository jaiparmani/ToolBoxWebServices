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
    """Authenticate by identifier (email or username) + password -> auth token.

    The identifier accepts an email or a username (`email` is still accepted for
    backwards compatibility). The token is what every subsequent API call
    authenticates with.
    """
    identifier = (request.data.get('identifier') or request.data.get('email') or '').strip()
    password = request.data.get('password') or ''
    if not identifier or not password:
        return Response({'error': 'Email/username and password are required.'}, status=status.HTTP_400_BAD_REQUEST)

    # Generic message either way, so this can't be used to probe which accounts exist.
    invalid = Response({'error': 'Invalid credentials.'}, status=status.HTTP_401_UNAUTHORIZED)
    user = _resolve_login_identifier(identifier)
    if not user or not user.check_password(password):
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


def _resolve_login_identifier(identifier):
    """Map an email / username / phone to one active user, or None.

    Email is detected by '@', an all-digit string is treated as a phone (matched
    against the profile phone once that exists), otherwise it's a username. An
    ambiguous match resolves to nobody, so a code is never sent to the wrong
    person.
    """
    identifier = (identifier or '').strip()
    if not identifier:
        return None
    if '@' in identifier:
        qs = User.objects.filter(email__iexact=identifier, is_active=True)
    elif identifier.replace('+', '').isdigit():
        # Phone lookup — no phone field on accounts yet, so this finds nobody
        # until phone numbers are stored. The channel (SMS) is a separate step.
        digits = identifier.replace(' ', '')
        qs = User.objects.filter(profile__phone=digits, is_active=True) if _has_profile_phone() else User.objects.none()
    else:
        qs = User.objects.filter(username__iexact=identifier, is_active=True)
    return qs.first() if qs.count() == 1 else None


def _has_profile_phone():
    try:
        from .models import UserProfile  # noqa: F401
        return True
    except Exception:
        return False


class OTPRequestView(APIView):
    """Start passwordless login: email a one-time code.

    The identifier can be an email, a username (or a phone, once phones are
    stored). Whatever they type, the code is emailed to the account's address.
    The response is always generic, so it can't be used to probe for accounts.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        from .models import EmailOTP
        identifier = (request.data.get('identifier') or '').strip()
        generic = Response(
            {'detail': "If that account exists, a sign-in code is on its way to its email."},
            status=status.HTTP_200_OK,
        )
        if not identifier:
            return Response({'error': 'Enter your email or username.'}, status=status.HTTP_400_BAD_REQUEST)

        user = _resolve_login_identifier(identifier)
        if user and user.email:
            code, throttled = EmailOTP.issue(user)
            if code:
                send_mail(
                    subject="Your ToolBox sign-in code",
                    message=(
                        f"Hi {user.first_name or user.username},\n\n"
                        f"Your ToolBox sign-in code is:\n\n    {code}\n\n"
                        "It expires in 10 minutes and can be used once. "
                        "If you didn't try to sign in, you can ignore this email."
                    ),
                    from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@toolbox.local'),
                    recipient_list=[user.email],
                    fail_silently=True,
                )
        return generic


class OTPVerifyView(APIView):
    """Finish passwordless login: verify the code and return an auth token."""
    permission_classes = [AllowAny]

    def post(self, request):
        from .models import EmailOTP
        identifier = (request.data.get('identifier') or '').strip()
        code = (request.data.get('code') or '').strip()
        invalid = Response({'error': 'That code is invalid or has expired.'}, status=status.HTTP_400_BAD_REQUEST)

        user = _resolve_login_identifier(identifier)
        if not user or not code:
            return invalid
        otp = EmailOTP.objects.filter(user=user, consumed=False).order_by('-created_at').first()
        if not otp or not otp.verify(code):
            return invalid

        token, _ = Token.objects.get_or_create(user=user)
        return Response({'detail': 'Signed in.', 'token': token.key, 'user': _user_payload(user)},
                        status=status.HTTP_200_OK)


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


def _get_profile(user):
    """The user's profile, created on demand. None only if the store is down."""
    try:
        from .models import UserProfile
        profile, _ = UserProfile.objects.get_or_create(user=user)
        return profile
    except Exception:
        return None


class MpinSetView(APIView):
    """Set or change the authenticated user's sign-in PIN (settings page).

    If a PIN already exists (`has_mpin`), the caller must prove they know the
    current one via `current_mpin` before it can be changed — so a lifted token
    alone can't silently swap the PIN. When no PIN exists yet, it can be set
    without one. The forgot-PIN path (mpin/reset + reset-confirm) covers the
    case where the current PIN is unknown.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from .models import validate_mpin
        user = request.user
        mpin = (request.data.get('mpin') or '').strip()
        current_mpin = (request.data.get('current_mpin') or '').strip()

        err = validate_mpin(mpin)
        if err:
            return Response({'error': err}, status=status.HTTP_400_BAD_REQUEST)

        profile = _get_profile(user)
        if not profile:
            return Response({'error': 'Could not save your MPIN right now.'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        # Changing an existing PIN requires verifying the current one.
        if profile.has_mpin:
            if not current_mpin:
                return Response({'error': 'Enter your current MPIN to change it.'},
                                status=status.HTTP_400_BAD_REQUEST)
            result = profile.check_mpin(current_mpin)
            if result == 'locked':
                return Response(
                    {'error': 'Too many wrong PINs. Try again later, or reset your MPIN by email.'},
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )
            if result != 'ok':
                return Response({'error': 'Your current MPIN is incorrect.'},
                                status=status.HTTP_400_BAD_REQUEST)

        profile.set_mpin(mpin)
        return Response({'detail': 'MPIN set. You can now sign in with it.', 'has_mpin': True},
                        status=status.HTTP_200_OK)


class MpinLoginView(APIView):
    """Sign in with an identifier (email/username) + MPIN -> auth token.

    A wrong PIN counts toward a lockout on the profile; too many freezes it for
    a while. The message is generic so this can't probe which accounts exist or
    which have a PIN.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        identifier = (request.data.get('identifier') or request.data.get('email') or '').strip()
        mpin = (request.data.get('mpin') or '').strip()
        invalid = Response({'error': 'Invalid credentials.'}, status=status.HTTP_401_UNAUTHORIZED)
        if not identifier or not mpin:
            return Response({'error': 'Email/username and MPIN are required.'}, status=status.HTTP_400_BAD_REQUEST)

        user = _resolve_login_identifier(identifier)
        profile = _get_profile(user) if user else None
        if not profile or not profile.has_mpin:
            return invalid

        result = profile.check_mpin(mpin)
        if result == 'locked':
            return Response(
                {'error': 'Too many wrong PINs. Try again later, or reset your MPIN by email.'},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        if result != 'ok':
            return invalid

        token, _ = Token.objects.get_or_create(user=user)
        return Response({'detail': 'Signed in.', 'token': token.key, 'user': _user_payload(user)},
                        status=status.HTTP_200_OK)


class MpinResetRequestView(APIView):
    """Start an MPIN reset: email a one-time code (the same 6-digit email code
    used for passwordless sign-in). Always a generic response, so it can't be
    used to probe for accounts."""
    permission_classes = [AllowAny]

    def post(self, request):
        from .models import EmailOTP
        identifier = (request.data.get('identifier') or request.data.get('email') or '').strip()
        generic = Response(
            {'detail': "If that account exists, an MPIN-reset code is on its way to its email."},
            status=status.HTTP_200_OK,
        )
        if not identifier:
            return Response({'error': 'Enter your email or username.'}, status=status.HTTP_400_BAD_REQUEST)

        user = _resolve_login_identifier(identifier)
        if user and user.email:
            code, throttled = EmailOTP.issue(user)
            if code:
                send_mail(
                    subject="Your ToolBox MPIN-reset code",
                    message=(
                        f"Hi {user.first_name or user.username},\n\n"
                        f"Use this code to set a new ToolBox MPIN:\n\n    {code}\n\n"
                        "It expires in 10 minutes and can be used once. "
                        "If you didn't ask to reset your MPIN, you can ignore this email."
                    ),
                    from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@toolbox.local'),
                    recipient_list=[user.email],
                    fail_silently=True,
                )
        return generic


class MpinResetConfirmView(APIView):
    """Finish an MPIN reset: verify the emailed code, set the new PIN, and sign
    the user in. This is the recovery path when a PIN is forgotten or locked."""
    permission_classes = [AllowAny]

    def post(self, request):
        from .models import EmailOTP, validate_mpin
        identifier = (request.data.get('identifier') or request.data.get('email') or '').strip()
        code = (request.data.get('code') or '').strip()
        mpin = (request.data.get('mpin') or '').strip()
        invalid = Response({'error': 'That code is invalid or has expired.'}, status=status.HTTP_400_BAD_REQUEST)

        err = validate_mpin(mpin)
        if err:
            return Response({'error': err}, status=status.HTTP_400_BAD_REQUEST)

        user = _resolve_login_identifier(identifier)
        if not user or not code:
            return invalid
        otp = EmailOTP.objects.filter(user=user, consumed=False).order_by('-created_at').first()
        if not otp or not otp.verify(code):
            return invalid

        profile = _get_profile(user)
        if not profile:
            return Response({'error': 'Could not save your MPIN right now.'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        profile.set_mpin(mpin)
        token, _ = Token.objects.get_or_create(user=user)
        return Response({'detail': 'MPIN reset. You are signed in.', 'token': token.key,
                         'user': _user_payload(user)}, status=status.HTTP_200_OK)


class ShortcutAPIKeyListCreateView(APIView):
    """List the caller's shortcut API keys (masked) or create a new one.

    POST returns the full key exactly once — subsequent GETs only show the
    masked version, so the user must copy it at creation time.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from .models import ShortcutAPIKey
        keys = ShortcutAPIKey.objects.filter(user=request.user)
        return Response([
            {
                'id': k.id,
                'masked': k.masked,
                'label': k.label,
                'created_at': k.created_at.isoformat(),
                'last_used_at': k.last_used_at.isoformat() if k.last_used_at else None,
            }
            for k in keys
        ])

    def post(self, request):
        from .models import ShortcutAPIKey
        label = (request.data.get('label') or '').strip()[:120]
        obj, raw_key = ShortcutAPIKey.generate(request.user, label=label)
        return Response({
            'id': obj.id,
            'key': raw_key,
            'masked': obj.masked,
            'label': obj.label,
            'created_at': obj.created_at.isoformat(),
        }, status=status.HTTP_201_CREATED)


class ShortcutAPIKeyDeleteView(APIView):
    """Revoke (delete) one of the caller's API keys."""
    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        from .models import ShortcutAPIKey
        deleted, _ = ShortcutAPIKey.objects.filter(id=pk, user=request.user).delete()
        if not deleted:
            return Response({'error': 'Key not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response({'detail': 'Key revoked.'}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([AllowAny])
def get_csrf_token(request):
    token = get_token(request)
    return JsonResponse({'csrftoken': token})
