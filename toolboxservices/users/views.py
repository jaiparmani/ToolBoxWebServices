from django.shortcuts import render
from django.contrib.auth.models import User
from rest_framework import viewsets, status, generics
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.decorators import api_view, permission_classes
from django.core.exceptions import PermissionDenied
from django.views.decorators.csrf import get_token
from django.utils.decorators import method_decorator
from django.http import JsonResponse
from .serializers import (
    UserRegistrationSerializer,
    UserProfileSerializer,
    PasswordChangeSerializer
)

# Create your views here.


class UserViewSet(viewsets.ModelViewSet):
    """
    ViewSet for user management with different serializers for different actions
    """
    queryset = User.objects.all()

    def get_serializer_class(self):
        """
        Return the appropriate serializer class based on the action
        """
        if self.action == 'create':
            return UserRegistrationSerializer
        return UserProfileSerializer

    def get_permissions(self):
        """
        Return the appropriate permission classes based on the action
        """
        if self.action == 'create':
            # Allow anyone to register
            permission_classes = [AllowAny]
        else:
            # No authentication required - userid handled in middleware
            permission_classes = [AllowAny]
        return [permission() for permission in permission_classes]

    def get_queryset(self):
        """
        Override queryset to show only current user data based on userid parameter
        """
        queryset = User.objects.all()

        # Get userid from query parameters
        userid = self.request.GET.get('userid')
        if not userid:
            return User.objects.none()

        try:
            user_id = int(userid)
        except ValueError:
            return User.objects.none()

        # Only return the user's own data
        return queryset.filter(id=user_id, is_active=True)

    def perform_create(self, serializer):
        """
        Handle user creation
        """
        serializer.save()

    def list(self, request, *args, **kwargs):
        """
        Override list method to handle permissions properly
        """
        queryset = self.filter_queryset(self.get_queryset())

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    def retrieve(self, request, *args, **kwargs):
        """
        Override retrieve method to handle permissions properly
        """
        instance = self.get_object()

        # Get userid from query parameters
        userid = request.GET.get('userid')
        if not userid:
            raise PermissionDenied("userid parameter is required.")

        try:
            user_id = int(userid)
        except ValueError:
            raise PermissionDenied("Invalid userid parameter.")

        # Check if user can access this data
        if instance.id != user_id:
            raise PermissionDenied("You can only view your own profile.")

        serializer = self.get_serializer(instance)
        return Response(serializer.data)


class UserProfileView(generics.RetrieveUpdateAPIView):
    """
    View for retrieving and updating current user's profile
    """
    serializer_class = UserProfileSerializer
    permission_classes = [AllowAny]

    def get_object(self):
        """
        Return the current user object based on userid parameter
        """
        userid = self.request.GET.get('userid')
        if not userid:
            raise PermissionDenied("userid parameter is required.")

        try:
            user_id = int(userid)
            return User.objects.get(id=user_id, is_active=True)
        except (ValueError, ObjectDoesNotExist):
            raise PermissionDenied("Invalid userid parameter.")

    def get_queryset(self):
        """
        Return queryset containing only the specified user
        """
        userid = self.request.GET.get('userid')
        if not userid:
            return User.objects.none()

        try:
            user_id = int(userid)
            return User.objects.filter(id=user_id, is_active=True)
        except ValueError:
            return User.objects.none()

    def update(self, request, *args, **kwargs):
        """
        Handle profile updates with proper validation
        """
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)

        return Response(serializer.data, status=status.HTTP_200_OK)


class PasswordChangeView(APIView):
    """
    View for handling password change requests
    """
    permission_classes = [AllowAny]

    def post(self, request):
        """
        Handle POST request for password change
        """
        userid = request.GET.get('userid')
        if not userid:
            return Response({'error': 'userid parameter is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user_id = int(userid)
            user = User.objects.get(id=user_id, is_active=True)
        except (ValueError, ObjectDoesNotExist):
            return Response({'error': 'Invalid userid parameter'}, status=status.HTTP_400_BAD_REQUEST)

        serializer = PasswordChangeSerializer(data=request.data, context={'request': request, 'user': user})

        if serializer.is_valid():
            # Set the new password
            user.set_password(serializer.validated_data['new_password'])
            user.save()

            return Response(
                {"detail": "Password changed successfully."},
                status=status.HTTP_200_OK
            )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([AllowAny])
def login_view(request):
    """
    Login view that validates userid via middleware.
    Since middleware already validates the userid parameter,
    we just need to return success if we reach this point.
    """
    userid = request.GET.get('userid')
    if not userid:
        return Response({'error': 'userid parameter is required'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        user_id = int(userid)
        # Middleware has already validated this user exists
        user = request.validated_user
        if not user:
            return Response({'error': 'Invalid userid'}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            'detail': 'Login successful.',
            'user': {
                'id': user.id,
                'username': user.username,
                'email': user.email,
                'first_name': user.first_name,
                'last_name': user.last_name
            }
        }, status=status.HTTP_200_OK)

    except (ValueError, ObjectDoesNotExist):
        return Response({'error': 'Invalid userid parameter'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([AllowAny])
def get_csrf_token(request):
    """
    Get CSRF token for API clients
    """
    token = get_token(request)
    return JsonResponse({'csrftoken': token})
