"""
Importer endpoints (gramps-web-api ``importers.py``).

- GET  /api/importers/                 list of importers
- GET  /api/importers/<extension>      one importer
- POST /api/importers/<extension>/file upload a file (raw body or multipart
                                       ``file`` field) and import it

Only Gramps XML (.gramps, gzipped or plain) is supported. The import
merges into the existing tree and runs synchronously; the response is 201
with the number of imported objects.
"""

import os
import uuid

from rest_framework import status
from rest_framework.response import Response

from apps.auth.permissions import PERM_IMPORT_FILE
from apps.migration.importer import ImportError_, import_gramps_xml

from .common import TreeAPIView, error_response, export_dir, store_task

IMPORTERS = [
    {
        "name": "Gramps XML",
        "description": "Gramps XML database (.gramps), optionally gzip-compressed",
        "extension": "gramps",
        "module": "apps.migration.importer",
    },
]


def get_importers(extension=None):
    return [imp for imp in IMPORTERS if extension is None or imp["extension"] == extension]


class ImportersView(TreeAPIView):
    def get(self, request):
        return Response(get_importers())


class ImporterView(TreeAPIView):
    def get(self, request, extension):
        importers = get_importers(extension.lower())
        if not importers:
            return error_response(
                f"Importer for extension {extension} not found", status.HTTP_404_NOT_FOUND
            )
        return Response(importers[0])


def _save_upload(request, file_path):
    """Write the uploaded content (raw body or multipart) to ``file_path``."""
    content_type = (request.content_type or "").lower()
    with open(file_path, "wb") as out:
        if content_type.startswith("multipart/form-data"):
            upload = request.FILES.get("file")
            if upload is None:
                for upload in request.FILES.values():
                    break
            if upload is None:
                return
            for chunk in upload.chunks():
                out.write(chunk)
            return
        # Raw body: stream from the underlying HttpRequest so that Django's
        # in-memory body limit does not apply.
        stream = request._request
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                break
            out.write(chunk)


class ImporterFileView(TreeAPIView):
    permissions_by_method = {"POST": [PERM_IMPORT_FILE]}

    def post(self, request, extension):
        extension = extension.lower()
        importers = get_importers(extension)
        if not importers:
            return error_response(
                f"Importer for extension {extension} not found", status.HTTP_404_NOT_FOUND
            )
        file_path = os.path.join(export_dir(), f"{uuid.uuid4()}.{extension}")
        try:
            _save_upload(request, file_path)
            if os.path.getsize(file_path) == 0:
                return error_response("Imported file is empty")
            try:
                counts = import_gramps_xml(file_path)
            except ImportError_ as exc:
                store_task("import_file", error=exc)
                return error_response(str(exc))
        finally:
            try:
                os.remove(file_path)
            except OSError:
                pass
        store_task("import_file", result=counts)
        return Response(counts, status=status.HTTP_201_CREATED)
