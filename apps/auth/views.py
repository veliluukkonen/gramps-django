"""
JWT token and user management views.

Compatible with gramps-web frontend token flow:
- POST /api/token/          → {access_token, refresh_token}
- POST /api/token/refresh/  → {access_token}
- POST /api/token/create_owner/ → {access_token} (first user bootstrap)
"""

import json
import logging

from django.conf import settings
from django.contrib.auth import authenticate
from django.db import IntegrityError
from django.http import JsonResponse
from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from .emails import base_url, notify_owners_of_new_user, send_password_reset_email
from .models import GrampsUser
from .permissions import (
    PERM_ADD_USER,
    PERM_DEL_USER,
    PERM_EDIT_OTHER_USER,
    PERM_VIEW_OTHER_USER,
    ROLE_DISABLED,
    ROLE_GUEST,
    ROLE_OWNER,
    HasGrampsPermission,
    get_permissions_for_role,
)
from .serializers import (
    PasswordChangeSerializer,
    PasswordResetSerializer,
    RegisterSerializer,
    UserCreateSerializer,
    UserSerializer,
)
from .tokens import PasswordResetToken

logger = logging.getLogger(__name__)
PASSWORD_MIN_LENGTH = 6


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

        if not user.is_active or user.role < ROLE_GUEST:
            return Response(
                {"error": "Account is disabled"},
                status=status.HTTP_403_FORBIDDEN,
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


class RegisterView(APIView):
    """
    POST /api/users/<username>/register/ — Self-registration (no auth).

    Mirrors gramps-web-api: the new account is created with role Disabled
    and an owner has to assign a role before the user can log in.
    Owners with an e-mail address are notified.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, username):
        if settings.REGISTRATION_DISABLED:
            return Response(
                {"error": {"message": "Registration is disabled"}},
                status=status.HTTP_405_METHOD_NOT_ALLOWED,
            )
        username = username.strip()
        if not username or username == "-":
            return Response(
                {"error": {"message": "Invalid username"}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = RegisterSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"error": {"message": _first_error(serializer.errors)}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        data = serializer.validated_data

        if (
            GrampsUser.objects.filter(username__iexact=username).exists()
            or GrampsUser.objects.filter(email__iexact=data["email"]).exists()
        ):
            return Response(
                {"error": {"message": "Username or e-mail already taken"}},
                status=status.HTTP_409_CONFLICT,
            )

        try:
            user = GrampsUser.objects.create_user(
                username=username,
                password=data["password"],
                email=data["email"],
                full_name=data.get("full_name", ""),
                tree=data.get("tree", ""),
                role=ROLE_DISABLED,
            )
        except IntegrityError:
            return Response(
                {"error": {"message": "Username or e-mail already taken"}},
                status=status.HTTP_409_CONFLICT,
            )

        notify_owners_of_new_user(user, f"{base_url(request)}/settings/users")
        return Response({}, status=status.HTTP_201_CREATED)


class PasswordResetTriggerView(APIView):
    """
    POST /api/users/<username>/password/reset/trigger/ — Send reset e-mail.

    Returns 404 if the user does not exist or has no e-mail address,
    500 if sending fails (matches what the frontend expects).
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, username):
        try:
            user = GrampsUser.objects.get(username=username)
        except GrampsUser.DoesNotExist:
            user = GrampsUser.objects.filter(email__iexact=username).first()
        if user is None or not user.email:
            return Response(
                {"error": "User not found or user has no e-mail address"},
                status=status.HTTP_404_NOT_FOUND,
            )

        token = PasswordResetToken.for_user(user)
        reset_url = f"{base_url(request)}/api/users/-/password/reset/?jwt={token}"
        try:
            send_password_reset_email(user, reset_url)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to send password reset e-mail to %s", user.username)
            return Response(
                {"error": "Could not send e-mail"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response({}, status=status.HTTP_201_CREATED)


@method_decorator(csrf_exempt, name="dispatch")
class PasswordResetView(View):
    """
    GET  /api/users/-/password/reset/?jwt=…  — HTML form (link from e-mail)
    POST /api/users/-/password/reset/        — set the new password

    POST accepts either the HTML form (fields jwt, new_password,
    new_password2) or JSON {"new_password": …} with the token in the
    Authorization: Bearer header, as in gramps-web-api.
    Security relies on the single-use JWT, so CSRF is not required.
    """

    template_name = "gramps_auth/password_reset.html"

    def _user_from_token(self, token_str):
        """Return (user, error_message)."""
        if not token_str:
            return None, "Linkki puuttuu tai on virheellinen."
        try:
            token = PasswordResetToken(token_str)
            user = GrampsUser.objects.get(pk=token["sub"])
        except (TokenError, GrampsUser.DoesNotExist, KeyError):
            return None, "Linkki on virheellinen tai vanhentunut. Pyydä uusi palautuslinkki."
        if not token.matches_user(user):
            return None, "Tämä linkki on jo käytetty. Pyydä uusi palautuslinkki."
        return user, None

    def _render(self, request, **ctx):
        ctx.setdefault("login_url", f"{base_url(request)}/login")
        ctx.setdefault("min_length", PASSWORD_MIN_LENGTH)
        status_code = ctx.pop("status", 200)
        return render(request, self.template_name, ctx, status=status_code)

    def get(self, request):
        token_str = request.GET.get("jwt", "")
        user, error = self._user_from_token(token_str)
        if error:
            return self._render(request, state="error", error=error, status=400)
        return self._render(request, state="form", jwt=token_str, username=user.username)

    def post(self, request):
        is_json = request.content_type == "application/json"
        if is_json:
            auth = request.headers.get("Authorization", "")
            token_str = auth.split(" ", 1)[1] if auth.startswith("Bearer ") else ""
            try:
                payload = json.loads(request.body or b"{}")
            except ValueError:
                payload = {}
        else:
            token_str = request.POST.get("jwt", "")
            payload = request.POST

        user, error = self._user_from_token(token_str)
        if error:
            if is_json:
                return JsonResponse({"error": error}, status=401)
            return self._render(request, state="error", error=error, status=400)

        serializer = PasswordResetSerializer(data={"new_password": payload.get("new_password", "")})
        if not serializer.is_valid():
            error = f"Salasanan on oltava vähintään {PASSWORD_MIN_LENGTH} merkkiä."
            if is_json:
                return JsonResponse({"error": error}, status=400)
            return self._render(request, state="form", jwt=token_str, username=user.username, error=error, status=400)
        if not is_json and payload.get("new_password") != payload.get("new_password2"):
            return self._render(
                request, state="form", jwt=token_str, username=user.username,
                error="Salasanat eivät täsmää.", status=400,
            )

        user.set_password(serializer.validated_data["new_password"])
        user.save(update_fields=["password"])
        if is_json:
            return JsonResponse({}, status=201)
        return self._render(request, state="done")


def _first_error(errors):
    """Flatten DRF serializer errors to one human-readable string."""
    for field, msgs in errors.items():
        msg = msgs[0] if isinstance(msgs, list) else msgs
        return f"{field}: {msg}"
    return "Invalid data"
