"""
Exporter endpoints (gramps-web-api ``exporters.py``).

- GET  /api/exporters/                        list of exporters
- GET  /api/exporters/<ext>                   one exporter
- POST /api/exporters/<ext>/file?compress=1   generate the file, returns
       201 {"file_name", "file_type", "url"} (the frontend downloads ``url``)
- GET  /api/exporters/<ext>/file?jwt=...      generate and send the file directly
- GET  /api/exporters/<ext>/file/processed/<file_name>?jwt=...  download

Supported options: ``compress`` (gramps only, default true) and
``private`` (exclude private records; forced for users without the
ViewPrivate permission). Files are written to ``MEDIA_ROOT/export/`` and
deleted once downloaded.
"""

import os
import re
import time
import uuid

from django.http import HttpResponse
from django.utils.http import content_disposition_header
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.auth.permissions import PERM_VIEW_PRIVATE
from apps.media.auth import jwt_from_query_or_header

from .common import TreeAPIView, error_response, export_dir, has_permissions, is_true, store_task
from .export_gedcom import export_gedcom
from .export_xml import export_gramps_xml

EXPORTERS = [
    {
        "name": "Gramps XML",
        "description": "Gramps XML export is a complete archived XML backup of a Gramps "
        "family tree without the media object files. Suitable for backup purposes.",
        "extension": "gramps",
        "module": "apps.tree.export_xml",
    },
    {
        "name": "GEDCOM",
        "description": "GEDCOM 5.5.1 (people, families, events, places, sources, notes).",
        "extension": "ged",
        "module": "apps.tree.export_gedcom",
    },
]
MIME_TYPES = {"gramps": "application/gzip", "ged": "text/x-gedcom"}
FILENAME_RE = re.compile(r"^([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})(\.[\w.]+)$")


def get_exporters(extension=None):
    return [exp for exp in EXPORTERS if extension is None or exp["extension"] == extension]


def run_export(extension, options, view_private):
    """Generate the export file; returns (file_name, file_type)."""
    exclude_private = is_true(options.get("private", "")) or not view_private
    file_name = f"{uuid.uuid4()}.{extension}"
    file_path = os.path.join(export_dir(), file_name)
    if extension == "gramps":
        compress = is_true(options.get("compress", "1"))
        export_gramps_xml(file_path, compress=compress, exclude_private=exclude_private)
    elif extension == "ged":
        export_gedcom(file_path, exclude_private=exclude_private)
    else:
        raise ValueError(f"Exporter for extension {extension} not found")
    return file_name, "." + extension


def _send_file(file_path, file_type, download_name, delete=True):
    with open(file_path, "rb") as f:
        content = f.read()
    if delete:
        try:
            os.remove(file_path)
        except OSError:
            pass
    mime = MIME_TYPES.get(file_type.lstrip("."), "application/octet-stream")
    response = HttpResponse(content, content_type=mime)
    response["Content-Disposition"] = content_disposition_header(True, download_name)
    response["Content-Length"] = len(content)
    return response


class ExportersView(TreeAPIView):
    def get(self, request):
        return Response(get_exporters())


class ExporterView(TreeAPIView):
    def get(self, request, extension):
        exporters = get_exporters(extension.lower())
        if not exporters:
            return error_response("Exporter not found", status.HTTP_404_NOT_FOUND)
        return Response(exporters[0])


class ExporterFileView(APIView):
    """POST (generate) / GET (generate and download) /api/exporters/<ext>/file"""

    permission_classes = [AllowAny]

    def _user(self, request):
        user = jwt_from_query_or_header(request)
        if user is None or not user.is_active:
            return None
        return user

    def post(self, request, extension):
        user = self._user(request)
        if user is None:
            return error_response("Authentication required", status.HTTP_401_UNAUTHORIZED)
        extension = extension.lower()
        if not get_exporters(extension):
            return error_response("Exporter not found", status.HTTP_404_NOT_FOUND)
        options = {k: v for k, v in request.query_params.items() if k != "jwt"}
        try:
            file_name, file_type = run_export(
                extension, options, has_permissions(user, PERM_VIEW_PRIVATE)
            )
        except Exception as exc:  # noqa: BLE001 - surface as failed task
            store_task("export_db", error=exc)
            return error_response(f"Export failed: {exc}", status.HTTP_500_INTERNAL_SERVER_ERROR)
        result = {
            "file_name": file_name,
            "file_type": file_type,
            "url": f"/api/exporters/{extension}/file/processed/{file_name}",
        }
        store_task("export_db", result=result)
        return Response(result, status=status.HTTP_201_CREATED)

    def get(self, request, extension):
        user = self._user(request)
        if user is None:
            return error_response("Authentication required", status.HTTP_401_UNAUTHORIZED)
        extension = extension.lower()
        if not get_exporters(extension):
            return error_response("Exporter not found", status.HTTP_404_NOT_FOUND)
        options = {k: v for k, v in request.query_params.items() if k != "jwt"}
        try:
            file_name, file_type = run_export(
                extension, options, has_permissions(user, PERM_VIEW_PRIVATE)
            )
        except Exception as exc:  # noqa: BLE001
            return error_response(f"Export failed: {exc}", status.HTTP_500_INTERNAL_SERVER_ERROR)
        file_path = os.path.join(export_dir(), file_name)
        date_str = time.strftime("%Y%m%d%H%M%S")
        return _send_file(file_path, file_type, f"gramps-web-export-{date_str}{file_type}")


class ExporterFileResultView(APIView):
    """GET /api/exporters/<ext>/file/processed/<file_name>?jwt=..."""

    permission_classes = [AllowAny]

    def get(self, request, extension, filename):
        user = jwt_from_query_or_header(request)
        if user is None or not user.is_active:
            return error_response("Authentication required", status.HTTP_401_UNAUTHORIZED)
        match = FILENAME_RE.match(filename)
        if not match:
            return error_response("Invalid filename", status.HTTP_422_UNPROCESSABLE_ENTITY)
        file_type = match.group(2)
        file_path = os.path.join(export_dir(), filename)
        if not os.path.isfile(file_path):
            return error_response("File not found", status.HTTP_404_NOT_FOUND)
        date_str = time.strftime("%Y%m%d%H%M%S", time.localtime(os.path.getmtime(file_path)))
        return _send_file(file_path, file_type, f"gramps-web-export-{date_str}{file_type}")
