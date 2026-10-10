"""
Tree endpoints (single-tree setup; every tree id is the one tree).

- GET  /api/trees/                 list with the one tree
- GET  /api/trees/<id>             tree details (usage, quotas, min_role_ai)
- PUT  /api/trees/<id>             rename / quotas / min_role_ai
- POST /api/trees/<id>/repair      check & repair (backlinks, dangling refs)
- POST /api/trees/<id>/migrate     schema upgrade (no-op)
"""

import os

from django.conf import settings
from rest_framework import status
from rest_framework.response import Response

from apps.auth.permissions import (
    PERM_EDIT_TREE,
    PERM_EDIT_TREE_MIN_ROLE_AI,
    PERM_EDIT_TREE_QUOTA,
    PERM_REPAIR_TREE,
    PERM_UPGRADE_TREE_SCHEMA,
)
from apps.core.models import MediaObject, Person

from .common import TreeAPIView, error_response, has_permissions, store_task
from .models import Config
from .repair import repair_tree

DEFAULT_TREE_ID = "gramps"


def tree_id_for(request):
    """The id of the single tree (the user's tree claim or a constant)."""
    user_tree = getattr(request.user, "tree", "") if request else ""
    return user_tree or Config.get("tree_id", DEFAULT_TREE_ID)


def tree_name():
    return Config.get("tree_name") or settings.TREE_NAME


def media_usage():
    """Total size in bytes of the media files present on disk."""
    try:
        from apps.media.upload import media_file_path
    except ImportError:  # pragma: no cover - media app not available
        media_file_path = None
    total = 0
    for media in MediaObject.objects.only("handle", "path").iterator():
        if not media.path:
            continue
        try:
            if media_file_path is not None:
                path = media_file_path(media)
            else:
                path = media.path
                if not os.path.isabs(path):
                    path = os.path.join(str(settings.MEDIA_ROOT), path)
            total += os.path.getsize(path)
        except (OSError, ValueError):
            continue
    return total


def tree_details(request):
    return {
        "id": tree_id_for(request),
        "name": tree_name(),
        "enabled": True,
        "usage_people": Person.objects.count(),
        "usage_media": media_usage(),
        "quota_people": Config.get("quota_people"),
        "quota_media": Config.get("quota_media"),
        "min_role_ai": Config.get("min_role_ai"),
    }


class TreesView(TreeAPIView):
    """GET /api/trees/"""

    def get(self, request):
        return Response([tree_details(request)])


class TreeView(TreeAPIView):
    """GET/PUT /api/trees/<tree_id>"""

    permissions_by_method = {"PUT": [PERM_EDIT_TREE]}

    def get(self, request, tree_id):
        return Response(tree_details(request))

    def put(self, request, tree_id):
        args = request.data if isinstance(request.data, dict) else {}
        result = {}
        name = args.get("name")
        if name:
            old_name = tree_name()
            Config.set("tree_name", str(name))
            result.update({"old_name": old_name, "new_name": str(name)})
        quotas = {k: args.get(k) for k in ("quota_media", "quota_people") if args.get(k) is not None}
        if quotas:
            if not has_permissions(request.user, PERM_EDIT_TREE_QUOTA):
                return error_response("Not allowed to change quotas", status.HTTP_403_FORBIDDEN)
            for key, value in quotas.items():
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    return error_response(f"Invalid value for {key}")
                Config.set(key, value)
                result[key] = value
        if args.get("min_role_ai") is not None:
            if not has_permissions(request.user, PERM_EDIT_TREE_MIN_ROLE_AI):
                return error_response("Not allowed to change min_role_ai", status.HTTP_403_FORBIDDEN)
            try:
                value = int(args["min_role_ai"])
            except (TypeError, ValueError):
                return error_response("Invalid value for min_role_ai")
            Config.set("min_role_ai", value)
            result["min_role_ai"] = value
        return Response(result)


class TreeRepairView(TreeAPIView):
    """POST /api/trees/<tree_id>/repair"""

    permissions_by_method = {"POST": [PERM_REPAIR_TREE]}

    def post(self, request, tree_id):
        try:
            result = repair_tree(request.user)
        except Exception as exc:  # noqa: BLE001 - report as failed task
            store_task("repair_tree", error=exc)
            return error_response(f"Repair failed: {exc}", status.HTTP_500_INTERNAL_SERVER_ERROR)
        store_task("repair_tree", result=result)
        return Response(result, status=status.HTTP_201_CREATED)


class TreeMigrateView(TreeAPIView):
    """POST /api/trees/<tree_id>/migrate - nothing to migrate in this backend."""

    permissions_by_method = {"POST": [PERM_UPGRADE_TREE_SCHEMA]}

    def post(self, request, tree_id):
        store_task("upgrade_database_schema", result={"message": "Nothing to migrate"})
        return Response({}, status=status.HTTP_200_OK)
