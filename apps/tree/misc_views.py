"""
Tasks, batch delete, search index and report endpoints.

- GET  /api/tasks/<task_id>                     {state, result, info, result_object}
- POST /api/objects/delete/?namespaces=a,b      delete all objects (one transaction)
- POST /api/search/index/?full=1                no-op (search is live SQL)
- GET  /api/reports/, /api/reports/<id>         no report engine: [] / 404
"""

from django.db import IntegrityError
from rest_framework import status
from rest_framework.response import Response

from apps.auth.permissions import PERM_DEL_OBJ_BATCH, PERM_TRIGGER_REINDEX
from apps.core.writes import WriteError, model_for_class, normalize_class_name, run_transaction

from .common import TreeAPIView, error_response, store_task, task_status
from .models import TaskResult

# namespace -> class, in deletion order
DELETE_ORDER = [
    ("families", "Family"),
    ("people", "Person"),
    ("events", "Event"),
    ("citations", "Citation"),
    ("sources", "Source"),
    ("places", "Place"),
    ("repositories", "Repository"),
    ("media", "Media"),
    ("notes", "Note"),
    ("tags", "Tag"),
]
NAMESPACES = {ns for ns, _ in DELETE_ORDER}


class TaskView(TreeAPIView):
    """GET /api/tasks/<task_id>"""

    def get(self, request, task_id):
        try:
            task = TaskResult.objects.get(pk=task_id)
        except TaskResult.DoesNotExist:
            return error_response("Task not found", status.HTTP_404_NOT_FOUND)
        return Response(task_status(task))


def delete_all_objects(user, namespaces=None):
    """Delete every object of the given namespaces (all if None)."""
    selected = [
        (ns, cls) for ns, cls in DELETE_ORDER if namespaces is None or ns in namespaces
    ]
    description = f"Delete {', '.join(ns for ns, _ in selected)}" if namespaces else "Delete all objects"

    def func(builder):
        for _, class_name in selected:
            model = model_for_class(class_name)
            # Re-query per class: cascades (source -> citations) may have
            # deleted objects already.
            for handle in list(model.objects.values_list("handle", flat=True)):
                if model.objects.filter(pk=handle).exists():
                    builder.delete(class_name, handle)

    return run_transaction(user, description, func)


class DeleteObjectsView(TreeAPIView):
    """POST /api/objects/delete/?namespaces=people,families"""

    permissions_by_method = {"POST": [PERM_DEL_OBJ_BATCH]}

    def post(self, request):
        raw = request.query_params.get("namespaces")
        namespaces = None
        if raw:
            namespaces = [normalize_namespace(ns) for ns in raw.split(",") if ns.strip()]
            unknown = [ns for ns in namespaces if ns not in NAMESPACES]
            if unknown:
                return error_response(f"Unknown namespace {unknown}", status.HTTP_422_UNPROCESSABLE_ENTITY)
        try:
            result = delete_all_objects(request.user, namespaces)
        except (WriteError, IntegrityError) as exc:
            store_task("delete_objects", error=exc)
            return error_response(str(exc))
        store_task("delete_objects", result={"deleted": len(result)})
        return Response({}, status=status.HTTP_200_OK)


def normalize_namespace(value):
    """Accept 'people', 'Person', 'person', ... and return the plural namespace."""
    value = value.strip()
    if value in NAMESPACES:
        return value
    class_name = normalize_class_name(value)
    for ns, cls in DELETE_ORDER:
        if cls == class_name:
            return ns
    return value


class SearchIndexView(TreeAPIView):
    """POST /api/search/index/ - search runs on the live database; nothing to do."""

    permissions_by_method = {"POST": [PERM_TRIGGER_REINDEX]}

    def post(self, request):
        store_task("search_reindex", result={"message": "Search index is always up to date"})
        return Response({}, status=status.HTTP_201_CREATED)


class ReportsView(TreeAPIView):
    """GET /api/reports/ - no Gramps report engine is available."""

    def get(self, request):
        return Response([])


class ReportView(TreeAPIView):
    """GET /api/reports/<report_id> and /api/reports/<report_id>/file"""

    def get(self, request, report_id, filename=None):
        return error_response("Reports are not available", status.HTTP_404_NOT_FOUND)

    def post(self, request, report_id, filename=None):
        return error_response("Reports are not available", status.HTTP_404_NOT_FOUND)
