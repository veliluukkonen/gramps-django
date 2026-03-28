"""
JWT token and user management views.

Compatible with gramps-web frontend token flow:
- POST /api/token/          → {access_token, refresh_token}
- POST /api/token/refresh/  → {access_token}
- POST /api/token/create_owner/ → {access_token} (first user bootstrap)
"""

from django.contrib.auth import authenticate
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .models import GrampsUser
from .permissions import (
    PERM_ADD_USER,
    PERM_DEL_USER,
    PERM_EDIT_OTHER_USER,
    PERM_VIEW_OTHER_USER,
    ROLE_OWNER,
    HasGrampsPermission,
    get_permissions_for_role,
)
from .serializers import (
    PasswordChangeSerializer,
    UserCreateSerializer,
    UserSerializer,
)


def _build_tokens(user):
    """Create JWT access + refresh tokens with Gramps-compatible claims."""
    refresh = RefreshToken.for_user(user)
    permissions = list(get_permissions_for_role(user.role))
    refresh["permissions"] = permissions
    if user.tree:
        refresh["tree"] = user.tree
    return {
        "access_token": str(refresh.access_token),
        "refresh_token": str(refresh),
    }


class TokenObtainView(APIView):
    """POST /api/token/ — Login with username + password."""

    permission_classes = [AllowAny]

    def post(self, request):
        username = request.data.get("username", "")
        password = request.data.get("password", "")

        if not username or not password:
            return Response(
                {"error": "Username and password are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = authenticate(request, username=username, password=password)
        if user is None:
            return Response(
                {"error": "Invalid credentials"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not user.is_active:
            return Response(
                {"error": "Account is disabled"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        return Response(_build_tokens(user))


class TokenRefreshView(APIView):
    """POST /api/token/refresh/ — Refresh access token."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return Response(
                {"error": "Refresh token required in Authorization header"},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        refresh_token_str = auth_header.split(" ", 1)[1]
        try:
            refresh = RefreshToken(refresh_token_str)
            user_id = refresh.get("sub")
            user = GrampsUser.objects.get(pk=user_id)
            permissions = list(get_permissions_for_role(user.role))
            refresh["permissions"] = permissions
            if user.tree:
                refresh["tree"] = user.tree
            return Response({"access_token": str(refresh.access_token)})
        except Exception:
            return Response(
                {"error": "Invalid refresh token"},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )


class TokenCreateOwnerView(APIView):
    """
    POST /api/token/create_owner/ — Bootstrap first user.

    Only works when no users exist in the database.
    Creates an Owner-level user and returns tokens.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        if GrampsUser.objects.exists():
            return Response(
                {"error": "Users already exist. Use normal registration."},
                status=status.HTTP_403_FORBIDDEN,
            )

        username = request.data.get("username", "")
        password = request.data.get("password", "")

        if not username or not password:
            return Response(
                {"error": "Username and password are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = GrampsUser.objects.create_user(
            username=username,
            password=password,
            role=ROLE_OWNER,
        )
        return Response(_build_tokens(user), status=status.HTTP_201_CREATED)


class UserListCreateView(generics.ListCreateAPIView):
    """
    GET  /api/users/  — List all users (requires ViewOtherUser)
    POST /api/users/  — Create new user (requires AddUser)
    """

    queryset = GrampsUser.objects.all()
    permission_classes = [IsAuthenticated, HasGrampsPermission]

    def get_serializer_class(self):
        if self.request.method == "POST":
            return UserCreateSerializer
        return UserSerializer

    @property
    def required_permissions(self):
        if self.request.method == "POST":
            return [PERM_ADD_USER]
        return [PERM_VIEW_OTHER_USER]


class UserDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET    /api/users/<username>/ — Get user details
    PUT    /api/users/<username>/ — Update user
    DELETE /api/users/<username>/ — Delete user

    Special: username "-" means current authenticated user.
    """

    queryset = GrampsUser.objects.all()
    serializer_class = UserSerializer
    lookup_field = "username"
    permission_classes = [IsAuthenticated, HasGrampsPermission]

    def get_object(self):
        username = self.kwargs.get("username")
        if username == "-":
            return self.request.user
        return super().get_object()

    @property
    def required_permissions(self):
        username = self.kwargs.get("username")
        if username == "-":
            return []
        if self.request.method == "GET":
            if username == self.request.user.username:
                return []
            return [PERM_VIEW_OTHER_USER]
        elif self.request.method == "DELETE":
            return [PERM_DEL_USER]
        else:
            if username == self.request.user.username:
                return []
            return [PERM_EDIT_OTHER_USER]


class PasswordChangeView(APIView):
    """POST /api/users/<username>/password/change — Change password."""

    permission_classes = [IsAuthenticated]

    def post(self, request, username):
        if request.user.username != username:
            perms = get_permissions_for_role(request.user.role)
            if PERM_EDIT_OTHER_USER not in perms:
                return Response(
                    {"error": "Permission denied"},
                    status=status.HTTP_403_FORBIDDEN,
                )

        try:
            user = GrampsUser.objects.get(username=username)
        except GrampsUser.DoesNotExist:
            return Response(
                {"error": "User not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = PasswordChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Own password change requires old password
        if request.user.username == username:
            if not user.check_password(serializer.validated_data["old_password"]):
                return Response(
                    {"error": "Invalid old password"},
                    status=status.HTTP_403_FORBIDDEN,
                )

        user.set_password(serializer.validated_data["new_password"])
        user.save()
        return Response({"message": "Password changed"})
