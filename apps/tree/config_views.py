"""
Configuration endpoints (gramps-web-api ``config.py``).

- GET    /api/config/          all settings (PERM_VIEW_SETTINGS)
- GET    /api/config/<key>/    one setting, 404 if unset
- PUT    /api/config/<key>/    {"value": ...} (PERM_EDIT_SETTINGS)
- DELETE /api/config/<key>/

Only the keys of gramps-web-api ``DB_CONFIG_ALLOWED_KEYS`` are exposed.
Internal keys (tree_name, quotas, ...) are managed via /api/trees/.
"""

from rest_framework import status
from rest_framework.response import Response

from apps.auth.permissions import PERM_EDIT_SETTINGS, PERM_VIEW_SETTINGS

from .common import TreeAPIView, error_response
from .models import Config

ALLOWED_KEYS = [
    "EMAIL_HOST",
    "EMAIL_PORT",
    "EMAIL_HOST_USER",
    "EMAIL_HOST_PASSWORD",
    "DEFAULT_FROM_EMAIL",
    "BASE_URL",
    "FRONTEND_URL",
]


class ConfigsView(TreeAPIView):
    """GET /api/config/"""

    permissions_by_method = {"GET": [PERM_VIEW_SETTINGS]}

    def get(self, request):
        values = {
            c.key: c.value for c in Config.objects.filter(key__in=ALLOWED_KEYS)
        }
        return Response(values)


class ConfigView(TreeAPIView):
    """GET/PUT/DELETE /api/config/<key>/"""

    permissions_by_method = {
        "GET": [PERM_VIEW_SETTINGS],
        "PUT": [PERM_EDIT_SETTINGS],
        "DELETE": [PERM_EDIT_SETTINGS],
    }

    def get(self, request, key):
        if key not in ALLOWED_KEYS:
            return error_response("Unknown configuration key", status.HTTP_404_NOT_FOUND)
        value = Config.get(key)
        if value is None:
            return error_response("Configuration key not set", status.HTTP_404_NOT_FOUND)
        return Response(value)

    def put(self, request, key):
        if key not in ALLOWED_KEYS:
            return error_response("Unknown configuration key", status.HTTP_404_NOT_FOUND)
        data = request.data if isinstance(request.data, dict) else {}
        if "value" not in data:
            return error_response("Missing value")
        Config.set(key, data["value"])
        return Response({}, status=status.HTTP_200_OK)

    def delete(self, request, key):
        if key not in ALLOWED_KEYS or Config.get(key) is None:
            return error_response("Configuration key not set", status.HTTP_404_NOT_FOUND)
        Config.objects.filter(pk=key).delete()
        return Response({}, status=status.HTTP_200_OK)
