"""
JWT token and user management views.

Compatible with gramps-web frontend token flow:
- POST /api/token/          → {access_token, refresh_token}
- POST /api/token/refresh/  → {access_token}
- POST /api/token/create_owner/ → {access_token} (limited-scope first-run token)
- POST /api/users/<username>/create_owner/ → creates the first account
"""

import json
import logging

from datetime import timedelta

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
from rest_framework_simplejwt.tokens import RefreshToken, Token

from .emails import base_url, notify_owners_of_new_user, send_password_reset_email
from .models import GrampsUser
from .permissions import (
    PERM_ADD_USER,
    PERM_DEL_USER,
    PERM_EDIT_OTHER_USER,
    PERM_EDIT_USER_ROLE,
    PERM_MAKE_ADMIN,
    PERM_VIEW_OTHER_USER,
    ROLE_ADMIN,
    ROLE_DISABLED,
    ROLE_GUEST,
    HasGrampsPermission,
    get_permissions_for_role,
)
from .serializers import (
    PasswordChangeSerializer,
    PasswordResetSerializer,
    RegisterSerializer,
    UserCreateOwnerSerializer,
    UserCreateSerializer,
    UserPostSerializer,
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


class CreateOwnerToken(Token):
    """
    Short-lived, limited-scope JWT handed out by POST /api/token/create_owner/
    while the user database is empty. It only authorises
    POST /api/users/<username>/create_owner/.
    """

    token_type = "create_owner"
    lifetime = timedelta(hours=1)
    SCOPE = "create_owner"

    @classmethod
    def for_scope(cls, tree=""):
        token = cls()
        token["scope"] = cls.SCOPE
        if tree:
            token["tree"] = tree
        return token


def _bearer_token(request):
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header.split(" ", 1)[1].strip()
    return ""


def _error(message, status_code):
    return Response({"error": {"message": message}}, status=status_code)


def _role_allowed(acting_user, role):
    """Check that ``acting_user`` may assign ``role`` to another user."""
    perms = get_permissions_for_role(acting_user.role)
    if PERM_MAKE_ADMIN in perms:
        return True
    if role >= ROLE_ADMIN:
        return False
    return role <= acting_user.role


class TokenCreateOwnerView(APIView):
    """
    POST /api/token/create_owner/ — first-run bootstrap.

    Returns ``{"access_token": ...}`` with a limited-scope token that only
    allows creating the first (owner) account. Fails with 403 as soon as
    any user exists.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        if GrampsUser.objects.exists():
            return _error("Users already exist", status.HTTP_403_FORBIDDEN)
        data = request.data if isinstance(request.data, dict) else {}
        token = CreateOwnerToken.for_scope(tree=str(data.get("tree") or ""))
        return Response({"access_token": str(token)}, status=status.HTTP_201_CREATED)


class UserCreateOwnerView(APIView):
    """
    POST /api/users/<username>/create_owner/ — create the first account.

    Requires the token from /api/token/create_owner/ in the Authorization
    header. As in the gramps-web-api single-tree setup the first user gets
    the Admin role (which includes every owner permission, e.g. editing
    the e-mail settings in the first-run flow).
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, username):
        token_str = _bearer_token(request)
        if not token_str:
            return _error("Missing token", status.HTTP_401_UNAUTHORIZED)
        try:
            token = CreateOwnerToken(token_str)
        except TokenError:
            return _error("Invalid or expired token", status.HTTP_401_UNAUTHORIZED)
        if token.get("scope") != CreateOwnerToken.SCOPE:
            return _error("Wrong token", status.HTTP_403_FORBIDDEN)
        if GrampsUser.objects.exists():
            return _error("Users already exist", status.HTTP_403_FORBIDDEN)
        username = username.strip()
        if not username or username in ("-", "_"):
            return _error("Invalid username", status.HTTP_404_NOT_FOUND)

        serializer = UserCreateOwnerSerializer(data=request.data)
        if not serializer.is_valid():
            return _error(_first_error(serializer.errors), status.HTTP_400_BAD_REQUEST)
        data = serializer.validated_data
        try:
            GrampsUser.objects.create_user(
                username=username,
                password=data["password"],
                email=data.get("email", ""),
                full_name=data.get("full_name", ""),
                tree=data.get("tree") or token.get("tree", "") or "",
                role=ROLE_ADMIN,
            )
        except IntegrityError:
            return _error("Username already exists", status.HTTP_409_CONFLICT)
        return Response({}, status=status.HTTP_201_CREATED)


class UserListCreateView(generics.ListCreateAPIView):
    """
    GET  /api/users/  — List all users (requires ViewOtherUser);
                        items: {name, username, email, full_name, role, tree}
    POST /api/users/  — Create user(s) (requires AddUser). Accepts a single
                        user object (with password) or, like gramps-web-api,
                        a list of users without passwords.
    """

    queryset = GrampsUser.objects.all().order_by("username")
    permission_classes = [IsAuthenticated, HasGrampsPermission]
    serializer_class = UserSerializer

    @property
    def required_permissions(self):
        if self.request.method == "POST":
            return [PERM_ADD_USER]
        return [PERM_VIEW_OTHER_USER]

    def post(self, request, *args, **kwargs):
        data = request.data
        if isinstance(data, list):
            return self._create_many(request, data)
        serializer = UserCreateSerializer(data=data)
        if not serializer.is_valid():
            return _error(_first_error(serializer.errors), status.HTTP_400_BAD_REQUEST)
        role = serializer.validated_data.get("role", 0)
        if not _role_allowed(request.user, role):
            return _error("Not allowed to assign this role", status.HTTP_403_FORBIDDEN)
        username = serializer.validated_data["username"]
        if username in ("-", "_") or GrampsUser.objects.filter(username__iexact=username).exists():
            return _error("Username already exists", status.HTTP_409_CONFLICT)
        user = serializer.save()
        return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)

    def _create_many(self, request, users):
        if not users:
            return _error("Empty payload", status.HTTP_422_UNPROCESSABLE_ENTITY)
        names = []
        for item in users:
            name = (item.get("name") or item.get("username") or "").strip() if isinstance(item, dict) else ""
            if not name or name in ("-", "_"):
                return _error("Missing user name", status.HTTP_422_UNPROCESSABLE_ENTITY)
            role = int(item.get("role", 0) or 0)
            if not _role_allowed(request.user, role):
                return _error("Not allowed to assign this role", status.HTTP_403_FORBIDDEN)
            names.append(name)
        if len(set(n.lower() for n in names)) != len(names) or GrampsUser.objects.filter(
            username__in=names
        ).exists():
            return _error("Username already exists", status.HTTP_409_CONFLICT)
        for item, name in zip(users, names):
            user = GrampsUser(
                username=name,
                email=item.get("email") or "",
                full_name=item.get("full_name") or item.get("fullname") or "",
                role=int(item.get("role", 0) or 0),
                tree=item.get("tree") or request.user.tree or "",
            )
            user.set_unusable_password()
            user.save()
        return Response({}, status=status.HTTP_201_CREATED)


class UserDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET    /api/users/<username>/ — Get user details
    POST   /api/users/<username>/ — Create a user (requires AddUser)
    PUT    /api/users/<username>/ — Update user (partial update allowed)
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
        if self.request.method == "POST":
            return [PERM_ADD_USER]
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

    def post(self, request, username):
        username = username.strip()
        if not username or username in ("-", "_"):
            return _error("Invalid username", status.HTTP_404_NOT_FOUND)
        serializer = UserPostSerializer(data=request.data)
        if not serializer.is_valid():
            return _error(_first_error(serializer.errors), status.HTTP_400_BAD_REQUEST)
        data = serializer.validated_data
        if not _role_allowed(request.user, data["role"]):
            return _error("Not allowed to assign this role", status.HTTP_403_FORBIDDEN)
        if GrampsUser.objects.filter(username__iexact=username).exists():
            return _error("Username already exists", status.HTTP_409_CONFLICT)
        try:
            GrampsUser.objects.create_user(
                username=username,
                password=data["password"],
                email=data.get("email", ""),
                full_name=data.get("full_name", ""),
                role=data["role"],
                tree=data.get("tree") or request.user.tree or "",
            )
        except IntegrityError:
            return _error("Username already exists", status.HTTP_409_CONFLICT)
        return Response({}, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        user = self.get_object()
        data = request.data if isinstance(request.data, dict) else {}
        perms = get_permissions_for_role(request.user.role)
        if "role" in data:
            try:
                role = int(data["role"])
            except (TypeError, ValueError):
                return _error("Invalid role", status.HTTP_400_BAD_REQUEST)
            if PERM_EDIT_USER_ROLE not in perms:
                return _error("Not allowed to change roles", status.HTTP_403_FORBIDDEN)
            if role != user.role and not _role_allowed(request.user, role):
                return _error("Not allowed to assign this role", status.HTTP_403_FORBIDDEN)
        if "name_new" in data:
            new_name = str(data["name_new"]).strip()
            if not new_name:
                return _error("Username cannot be empty", status.HTTP_400_BAD_REQUEST)
            if new_name in ("-", "_"):
                return _error("Username cannot be a reserved name", status.HTTP_400_BAD_REQUEST)
            if GrampsUser.objects.filter(username__iexact=new_name).exclude(pk=user.pk).exists():
                return _error("Username already exists", status.HTTP_409_CONFLICT)
            user.username = new_name
        serializer = self.get_serializer(user, data=data, partial=True)
        if not serializer.is_valid():
            return _error(_first_error(serializer.errors), status.HTTP_400_BAD_REQUEST)
        try:
            serializer.save()
        except IntegrityError:
            return _error("Username or e-mail already taken", status.HTTP_409_CONFLICT)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def destroy(self, request, *args, **kwargs):
        if self.kwargs.get("username") == "-":
            return _error("Deleting the own account is not allowed", status.HTTP_404_NOT_FOUND)
        instance = self.get_object()
        instance.delete()
        # 200 with an empty body like gramps-web-api: the frontend treats
        # anything other than 200/201/202 (e.g. 204 No Content) as an error.
        return Response({}, status=status.HTTP_200_OK)


class PasswordChangeView(APIView):
    """
    POST /api/users/<username>/password/change — Change password.

    ``-`` is the current user; then ``old_password`` is required. Changing
    another user's password requires EditOtherUser (no old password needed).
    Responds 201 with an empty body like gramps-web-api.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, username):
        own = username == "-" or username == request.user.username
        if own:
            user = request.user
        else:
            perms = get_permissions_for_role(request.user.role)
            if PERM_EDIT_OTHER_USER not in perms:
                return _error("Permission denied", status.HTTP_403_FORBIDDEN)
            try:
                user = GrampsUser.objects.get(username=username)
            except GrampsUser.DoesNotExist:
                return _error("User not found", status.HTTP_404_NOT_FOUND)

        serializer = PasswordChangeSerializer(data=request.data)
        if not serializer.is_valid():
            return _error(_first_error(serializer.errors), status.HTTP_400_BAD_REQUEST)
        new_password = serializer.validated_data["new_password"]
        if own:
            old_password = serializer.validated_data.get("old_password") or ""
            if not old_password:
                return _error("Old password is required", status.HTTP_400_BAD_REQUEST)
            if not user.check_password(old_password):
                return _error("Old password incorrect", status.HTTP_403_FORBIDDEN)

        user.set_password(new_password)
        user.save(update_fields=["password"])
        return Response({}, status=status.HTTP_201_CREATED)


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
