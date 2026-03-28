from rest_framework import serializers

from .models import GrampsUser
from .permissions import ROLE_CHOICES


class TokenObtainSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)


class TokenRefreshSerializer(serializers.Serializer):
    pass  # Refresh token comes from Authorization header


class UserSerializer(serializers.ModelSerializer):
    role = serializers.IntegerField()

    class Meta:
        model = GrampsUser
        fields = ["username", "email", "full_name", "role", "tree"]
        read_only_fields = ["username"]


class UserCreateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=6)

    class Meta:
        model = GrampsUser
        fields = ["username", "password", "email", "full_name", "role"]

    def create(self, validated_data):
        password = validated_data.pop("password")
        user = GrampsUser(**validated_data)
        user.set_password(password)
        user.save()
        return user


class PasswordChangeSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=6)
