from rest_framework import serializers

from .models import GrampsUser
from .permissions import ROLE_CHOICES

PASSWORD_MIN_LENGTH = 6


class TokenObtainSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)


class TokenRefreshSerializer(serializers.Serializer):
    pass  # Refresh token comes from Authorization header


class UserSerializer(serializers.ModelSerializer):
    """
    User details in the gramps-web-api shape (``name`` is the username).
    ``username`` is kept as an alias for existing clients.
    """

    name = serializers.CharField(source="username", read_only=True)
    role = serializers.IntegerField(required=False)

    class Meta:
        model = GrampsUser
        fields = ["name", "username", "email", "full_name", "role", "tree"]
        read_only_fields = ["username"]


class UserCreateSerializer(serializers.ModelSerializer):
    """POST /api/users/ with a single user object (username in the body)."""

    password = serializers.CharField(write_only=True, min_length=PASSWORD_MIN_LENGTH)
    role = serializers.IntegerField(required=False, default=0)
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    full_name = serializers.CharField(required=False, allow_blank=True, default="", max_length=200)

    class Meta:
        model = GrampsUser
        fields = ["username", "password", "email", "full_name", "role", "tree"]
        extra_kwargs = {"tree": {"required": False}}

    def create(self, validated_data):
        password = validated_data.pop("password")
        user = GrampsUser(**validated_data)
        user.set_password(password)
        user.save()
        return user


class UserPostSerializer(serializers.Serializer):
    """Body of POST /api/users/<username>/ (gramps-web-api UserPostBodyArgs)."""

    password = serializers.CharField(write_only=True, min_length=PASSWORD_MIN_LENGTH)
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    full_name = serializers.CharField(required=False, allow_blank=True, default="", max_length=200)
    role = serializers.IntegerField(required=False, default=0)
    tree = serializers.CharField(required=False, allow_blank=True, default="", max_length=100)


class UserCreateOwnerSerializer(serializers.Serializer):
    """Body of POST /api/users/<username>/create_owner/."""

    password = serializers.CharField(write_only=True, min_length=PASSWORD_MIN_LENGTH)
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    full_name = serializers.CharField(required=False, allow_blank=True, default="", max_length=200)
    tree = serializers.CharField(required=False, allow_blank=True, default="", max_length=100)


class PasswordChangeSerializer(serializers.Serializer):
    """``old_password`` is checked by the view (required for the own account)."""

    old_password = serializers.CharField(write_only=True, required=False, allow_blank=True, default="")
    new_password = serializers.CharField(write_only=True, min_length=PASSWORD_MIN_LENGTH)


class RegisterSerializer(serializers.Serializer):
    """Payload of POST /api/users/<username>/register/ (unauthenticated)."""

    password = serializers.CharField(write_only=True, min_length=PASSWORD_MIN_LENGTH)
    email = serializers.EmailField()
    full_name = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    tree = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")


class PasswordResetSerializer(serializers.Serializer):
    new_password = serializers.CharField(write_only=True, min_length=PASSWORD_MIN_LENGTH)


__all__ = [
    "ROLE_CHOICES",
    "TokenObtainSerializer",
    "TokenRefreshSerializer",
    "UserSerializer",
    "UserCreateSerializer",
    "UserPostSerializer",
    "UserCreateOwnerSerializer",
    "PasswordChangeSerializer",
    "RegisterSerializer",
    "PasswordResetSerializer",
]
