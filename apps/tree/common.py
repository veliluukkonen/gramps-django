"""Shared helpers for the tree administration views."""

import json
import os
import time
import uuid

from django.conf import settings
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.auth.permissions import HasGrampsPermission, get_permissions_for_role

from .models import TaskResult

EXPORT_SUBDIR = "export"


def error_response(message, status_code=status.HTTP_400_BAD_REQUEST):
    """Error body in the shape the frontend reads (``error.message``)."""
    return Response({"error": {"message": message}}, status=status_code)


def is_true(value):
    return str(value).lower() in ("1", "true", "yes", "on")


def has_permissions(user, *perms):
    """True if the (authenticated) user holds all of the given permissions."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    granted = get_permissions_for_role(user.role)
    return all(p in granted for p in perms)


class TreeAPIView(APIView):
    """
    Base view: JWT-authenticated user with the Gramps permissions listed in
    ``required_permissions`` (a list, or a dict HTTP method -> list).
    """

    permission_classes = [IsAuthenticated, HasGrampsPermission]
    permissions_by_method = {}

    @property
    def required_permissions(self):
        return self.permissions_by_method.get(self.request.method, [])


def export_dir():
    """Directory for generated export files and uploaded import files."""
    path = os.path.join(os.path.abspath(str(settings.MEDIA_ROOT)), EXPORT_SUBDIR)
    os.makedirs(path, exist_ok=True)
    return path


def store_task(name, result=None, error=None):
    """
    Record the outcome of a synchronously executed "task".

    Returns the TaskResult. ``error`` marks the task as failed.
    """
    task = TaskResult(
        id=uuid.uuid4().hex,
        name=name,
        state=TaskResult.STATE_FAILURE if error else TaskResult.STATE_SUCCESS,
        result=result if error is None else {"error": str(error)},
        info=result if error is None else str(error),
        created=time.time(),
    )
    task.save()
    return task


def task_status(task):
    """GET /api/tasks/<id> body (gramps-web-api TaskStatusSchema)."""

    def as_str(value):
        if value is None:
            return None
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value)
        except TypeError:
            return str(value)

    return {
        "state": task.state,
        "result_object": task.result,
        "result": as_str(task.result),
        "info": as_str(task.info),
    }
