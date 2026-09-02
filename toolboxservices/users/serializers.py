import re

from rest_framework import serializers
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from .models import UserProfile, validate_mpin


def normalize_phone(value):
    """Validate a mobile number and return it normalized (digits, optional
    leading '+'). Raises serializers.ValidationError on anything that isn't a
    plausible mobile number. Shared so registration and profile edits agree."""
    raw = (value or '').strip()
    if not raw:
        raise serializers.ValidationError("Mobile number is required.")
    # Keep a single leading '+' plus digits; drop spaces, dashes, parens.
    cleaned = re.sub(r'[\s\-().]', '', raw)
    plus = cleaned.startswith('+')
    digits = cleaned[1:] if plus else cleaned
    if not digits.isdigit():
        raise serializers.ValidationError("Enter a valid mobile number.")
    if not (7 <= len(digits) <= 15):
        raise serializers.ValidationError("Enter a valid mobile number.")
    return ('+' + digits) if plus else digits


class UserRegistrationSerializer(serializers.ModelSerializer):
    """
    Serializer for user registration with password validation
    """
    password = serializers.CharField(
        write_only=True,
        min_length=8,
        style={'input_type': 'password'}
    )
    password_confirm = serializers.CharField(
        write_only=True,
        style={'input_type': 'password'}
    )
    # Mobile number is mandatory at sign-up and validated below.
    phone = serializers.CharField(required=True, allow_blank=False, max_length=20)
    # A 6-digit sign-in PIN is set at registration, so the account can MPIN-login
    # immediately. Stored hashed via the profile's set_mpin (same mechanism as
    # the MPIN login/reset flow).
    mpin = serializers.CharField(required=True, write_only=True, max_length=6)

    class Meta:
        model = User
        fields = ('id', 'username', 'email', 'first_name', 'last_name', 'phone', 'mpin', 'password', 'password_confirm', 'date_joined')
        read_only_fields = ('id', 'date_joined')

    def validate_username(self, value):
        """
        Check if username is unique
        """
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("A user with this username already exists.")
        return value

    def validate_email(self, value):
        """
        Check if email is unique
        """
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value

    def validate_phone(self, value):
        """Mobile number is mandatory and must look like a real number."""
        return normalize_phone(value)

    def validate_mpin(self, value):
        """A 6-digit, non-trivial PIN (same rules as the MPIN login/reset flow)."""
        err = validate_mpin(value)
        if err:
            raise serializers.ValidationError(err)
        return str(value).strip()

    def validate(self, data):
        """
        Validate that passwords match
        """
        if data['password'] != data['password_confirm']:
            raise serializers.ValidationError({
                'password_confirm': "Passwords do not match."
            })
        return data

    def validate_password(self, value):
        """
        Validate password strength using Django's password validators
        """
        user = User(username=self.initial_data.get('username', ''))
        try:
            validate_password(value, user)
        except ValidationError as e:
            raise serializers.ValidationError(list(e.messages))
        return value

    def create(self, validated_data):
        """
        Create user using Django's create_user method
        """
        validated_data.pop('password_confirm')  # Remove password_confirm from data
        phone = validated_data.pop('phone', '')
        mpin = validated_data.pop('mpin')

        user = User.objects.create_user(
            username=validated_data['username'],
            email=validated_data['email'],
            password=validated_data['password'],
            first_name=validated_data.get('first_name', ''),
            last_name=validated_data.get('last_name', '')
        )
        # The post_save signal already created the profile (best-effort). Store
        # the mandatory phone and set the sign-in PIN so the account can
        # MPIN-login right after registering.
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.phone = phone.strip()
        profile.save(update_fields=['phone'])
        profile.set_mpin(mpin)
        return user


class UserProfileSerializer(serializers.ModelSerializer):
    """
    Serializer for user profile operations (view/update)
    """
    # Read via a method field so a missing/partial profile (or a profile table
    # that hasn't been migrated yet on a server) can never 500 the whole
    # profile read — it just reports no phone. Writes are handled in update().
    phone = serializers.SerializerMethodField()
    # Whether the account has a sign-in PIN set — lets the app show "set up MPIN"
    # vs "change MPIN". Method field so a missing/unmigrated profile can't 500.
    has_mpin = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ('id', 'username', 'email', 'first_name', 'last_name', 'phone', 'has_mpin', 'date_joined')
        read_only_fields = ('id', 'date_joined', 'has_mpin')

    def get_phone(self, obj):
        try:
            return obj.profile.phone or ''
        except Exception:
            return ''

    def get_has_mpin(self, obj):
        try:
            return bool(obj.profile.has_mpin)
        except Exception:
            return False

    def validate_email(self, value):
        """
        Check if email is unique when updating
        """
        user = self.instance
        if User.objects.filter(email=value).exclude(pk=user.pk).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value

    def update(self, instance, validated_data):
        """Phone lives on the related profile; take it from the raw input (the
        field is read-only) and save it best-effort, so a profile store that
        isn't available yet doesn't break editing name/email."""
        user = super().update(instance, validated_data)
        phone = self.initial_data.get('phone', None)
        if phone is not None:
            try:
                from .models import UserProfile
                profile, _ = UserProfile.objects.get_or_create(user=user)
                profile.phone = (phone or '').strip()
                profile.save(update_fields=['phone'])
            except Exception:
                pass
        return user


class PasswordChangeSerializer(serializers.Serializer):
    """
    Serializer for password change operations
    """
    old_password = serializers.CharField(
        write_only=True,
        style={'input_type': 'password'}
    )
    new_password = serializers.CharField(
        write_only=True,
        min_length=8,
        style={'input_type': 'password'}
    )
    new_password_confirm = serializers.CharField(
        write_only=True,
        style={'input_type': 'password'}
    )

    def validate_old_password(self, value):
        """
        Validate that old password is correct
        """
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError("Old password is incorrect.")
        return value

    def validate_new_password(self, value):
        """
        Validate new password strength
        """
        user = self.context['request'].user
        try:
            validate_password(value, user)
        except ValidationError as e:
            raise serializers.ValidationError(list(e.messages))
        return value

    def validate(self, data):
        """
        Validate that new passwords match
        """
        if data['new_password'] != data['new_password_confirm']:
            raise serializers.ValidationError({
                'new_password_confirm': "New passwords do not match."
            })

        # Check that new password is different from old password
        if data['old_password'] == data['new_password']:
            raise serializers.ValidationError({
                'new_password': "New password must be different from old password."
            })

        return data