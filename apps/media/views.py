"""
Media file endpoints.

- GET  /api/media/<handle>/file                       serve the original file
- PUT  /api/media/<handle>/file                       replace the file (raw body)
- GET  /api/media/<handle>/thumbnail/<size>           JPEG thumbnail
- GET  /api/media/<handle>/cropped/<x1>/<y1>/<x2>/<y2>[/thumbnail/<size>]
- POST /api/media/archive/                            build a ZIP of all media files
- GET  /api/media/archive/<filename>                  download (and delete) the ZIP
- POST /api/media/archive/upload/zip                  restore missing files from a ZIP

File-serving endpoints accept the JWT either in the Authorization header or
in the ``?jwt=`` query parameter, because browsers load images directly.
Error bodies are ``{"error": {"message": "..."}}``.
"""

import mimetypes
import os
import re
import shutil
import tempfile
import time
import uuid
import zipfile

from django.http import FileResponse, HttpResponse, JsonResponse
from django.utils.http import content_disposition_header
from PIL import Image, ImageOps, UnidentifiedImageError
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.auth.permissions import (
    PERM_EDIT_OBJ,
    PERM_VIEW_PRIVATE,
    HasGrampsPermission,
    get_permissions_for_role,
)
from apps.core.models import MediaObject
from apps.core.writes import run_transaction, serialize

from . import upload as media_upload
from .auth import jwt_from_query_or_header

MIME_JPEG = "image/jpeg"
ARCHIVE_NAME_RE = re.compile(
    r"^([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})\.zip$"
)


# --- helpers ---------------------------------------------------------------


def error_response(message, status_code):
    """Return a gramps-web-api style JSON error."""
    return JsonResponse({"error": {"message": message}}, status=status_code)


def _true(value):
    return str(value).lower() in ("1", "true", "yes")


def _authenticate(request):
    """Return (user, None) or (None, 401 response)."""
    user = jwt_from_query_or_header(request)
    if user is None:
        return None, error_response("Authentication required", 401)
    return user, None


def _get_media(handle):
    """Return (media, None) or (None, 404 response)."""
    try:
        return MediaObject.objects.get(pk=handle), None
    except MediaObject.DoesNotExist:
        return None, error_response("Media object not found", 404)


def _open_media_file(media):
    """Return (abs_path, None) or (None, error response) for a media file."""
    if not media.path:
        return None, error_response("Media object has no file", 404)
    abs_path = media_upload.media_file_path(media)
    if not media_upload.is_within_media_root(abs_path):
        return None, error_response("File access not allowed", 403)
    if not os.path.isfile(abs_path):
        return None, error_response("Media file not found", 404)
    return abs_path, None


def _parse_percent(*values):
    try:
        coords = [float(v) for v in values]
    except (TypeError, ValueError):
        raise ValueError("Invalid crop coordinates")
    for c in coords:
        if c < 0 or c > 100:
            raise ValueError("Crop coordinates must be between 0 and 100")
    return coords


def _resolve_media_and_user(request, handle):
    """Common prologue for the file-serving views."""
    user, err = _authenticate(request)
    if err:
        return None, None, None, err
    media, err = _get_media(handle)
    if err:
        return None, None, None, err
    abs_path, err = _open_media_file(media)
    if err:
        return None, None, None, err
    return user, media, abs_path, None


# --- image processing (ported from gramps-web-api image.py) ----------------


def open_image(abs_path, mime):
    """
    Open the media file as a PIL image.

    Raises ``ValueError`` for MIME types that cannot be thumbnailed.
    """
    mime = (mime or mimetypes.guess_type(abs_path)[0] or "").lower()
    if not mime.startswith("image/"):
        raise ValueError(f"No thumbnailer found for MIME type {mime or 'unknown'}.")
    try:
        img = Image.open(abs_path)
        img.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"Cannot read image: {exc}")
    transposed = ImageOps.exif_transpose(img)
    return transposed if transposed is not None else img


def image_thumbnail(img, size, square=False):
    """Thumbnail with ``size`` as the longest side; optionally a centered square."""
    if square:
        # Don't enlarge: the square is at most the shorter side's length.
        size_square = min(min(img.size), size)
        return ImageOps.fit(
            img,
            (size_square, size_square),
            bleed=0.0,
            centering=(0.5, 0.5),
            method=Image.Resampling.BICUBIC,
        )
    img.thumbnail((size, size))
    return img


def image_square(img):
    """Crop an image to a centered square."""
    size = min(img.size)
    return ImageOps.fit(
        img, (size, size), bleed=0.0, centering=(0.0, 0.5),
        method=Image.Resampling.BICUBIC,
    )


def crop_image(img, x1, y1, x2, y2):
    """Crop using percentage coordinates (0-100) of the original image."""
    width, height = img.size
    return img.crop((
        x1 * width / 100, y1 * height / 100, x2 * width / 100, y2 * height / 100,
    ))


def jpeg_response(img, quality=85):
    """Serialize a PIL image as a JPEG HTTP response."""
    if img.mode != "RGB":
        img = img.convert("RGB")
    response = HttpResponse(content_type=MIME_JPEG)
    img.save(response, "JPEG", quality=quality)
    return response


def _image_error(exc):
    """Map image-processing exceptions to error responses."""
    if isinstance(exc, ValueError):
        return error_response(str(exc), 404)
    return error_response("Cannot process image", 500)


# --- /api/media/<handle>/file ---------------------------------------------


class MediaFileView(APIView):
    """
    GET: serve the original media file (``?download=1`` forces download).

    PUT: replace the file of an existing media object with the raw request
    body. The file is stored as ``<checksum><ext>`` and the object's path,
    mime and checksum are updated through a transaction. With
    ``?uploadmissing=1`` the body must have the object's current checksum and
    is stored at the object's existing path without changing the record.
    """

    permission_classes = [AllowAny]
    required_permissions = [PERM_EDIT_OBJ]

    def get_permissions(self):
        if self.request.method == "PUT":
            return [IsAuthenticated(), HasGrampsPermission()]
        return [AllowAny()]

    def get(self, request, handle):
        user, media, abs_path, err = _resolve_media_and_user(request, handle)
        if err:
            return err
        mime = media.mime or mimetypes.guess_type(abs_path)[0] or "application/octet-stream"
        response = FileResponse(open(abs_path, "rb"), content_type=mime)
        download = _true(request.query_params.get("download", ""))
        filename = os.path.basename(media.path)
        response["Content-Disposition"] = content_disposition_header(download, filename)
        if media.checksum:
            response["ETag"] = f'"{media.checksum}"'
        return response

    def put(self, request, handle):
        media, err = _get_media(handle)
        if err:
            return err
        checksum_old = media.checksum or ""
        if_match = request.headers.get("If-Match", "")
        if if_match:
            etags = [tag.strip().strip('"') for tag in if_match.split(",")]
            if "*" not in etags and checksum_old not in etags:
                return error_response("ETag mismatch. Resource has been modified.", 412)

        try:
            received = media_upload.receive_upload(request)
        except ValueError as exc:
            return error_response(str(exc), 400)

        new_mime, new_checksum = received.mime, received.checksum
        try:
            upload_missing = _true(request.query_params.get("uploadmissing", ""))
            if new_checksum == checksum_old:
                if not upload_missing or media_upload.file_exists(media) or not media.path:
                    return error_response(
                        "Uploaded file has the same checksum as the existing media object",
                        409,
                    )
                # Restoring a missing file: keep the record, use its path.
                try:
                    received.store(media.path)
                except ValueError as exc:
                    return error_response(str(exc), 400)
                received = None
                return HttpResponse(status=200)
            if upload_missing:
                return error_response("Uploaded file has the wrong checksum", 409)

            path = received.store()
            received = None
        finally:
            if received is not None:
                received.discard()

        data = serialize(media)
        data.update(path=path, mime=new_mime, checksum=new_checksum)

        def do(builder):
            builder.update(data, "Media", handle)

        changes = run_transaction(request.user, "Edit Media Object", do)
        return Response(changes, status=200)


# --- thumbnails and crops --------------------------------------------------


class MediaThumbnailView(APIView):
    """GET /api/media/<handle>/thumbnail/<size>  (``?square=1``)."""

    permission_classes = [AllowAny]

    def get(self, request, handle, size):
        user, media, abs_path, err = _resolve_media_and_user(request, handle)
        if err:
            return err
        square = _true(request.query_params.get("square", ""))
        try:
            img = open_image(abs_path, media.mime)
            img = image_thumbnail(img, int(size), square)
            return jpeg_response(img)
        except Exception as exc:  # noqa: BLE001
            return _image_error(exc)


class MediaCroppedView(APIView):
    """GET /api/media/<handle>/cropped/<x1>/<y1>/<x2>/<y2>  (``?square=1``)."""

    permission_classes = [AllowAny]

    def get(self, request, handle, x1, y1, x2, y2):
        user, media, abs_path, err = _resolve_media_and_user(request, handle)
        if err:
            return err
        square = _true(request.query_params.get("square", ""))
        try:
            coords = _parse_percent(x1, y1, x2, y2)
        except ValueError as exc:
            return error_response(str(exc), 422)
        try:
            img = open_image(abs_path, media.mime)
            img = crop_image(img, *coords)
            if square:
                img = image_square(img)
            return jpeg_response(img, quality=90)
        except Exception as exc:  # noqa: BLE001
            return _image_error(exc)


class MediaCroppedThumbnailView(APIView):
    """GET /api/media/<handle>/cropped/<x1>/<y1>/<x2>/<y2>/thumbnail/<size>."""

    permission_classes = [AllowAny]

    def get(self, request, handle, x1, y1, x2, y2, size):
        user, media, abs_path, err = _resolve_media_and_user(request, handle)
        if err:
            return err
        square = _true(request.query_params.get("square", ""))
        try:
            coords = _parse_percent(x1, y1, x2, y2)
        except ValueError as exc:
            return error_response(str(exc), 422)
        try:
            img = open_image(abs_path, media.mime)
            img = crop_image(img, *coords)
            img = image_thumbnail(img, int(size), square)
            return jpeg_response(img)
        except Exception as exc:  # noqa: BLE001
            return _image_error(exc)


# --- media archive (export) ------------------------------------------------


class MediaArchiveView(APIView):
    """
    POST /api/media/archive/

    Build a ZIP of all media files that exist on disk (private objects only
    for users with the ViewPrivate permission). Returns 201 with
    ``{"file_name", "url", "file_size"}`` like gramps-web-api's eager task.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        include_private = PERM_VIEW_PRIVATE in get_permissions_for_role(request.user.role)
        file_name = f"{uuid.uuid4()}.zip"
        zip_path = os.path.join(media_upload.export_dir(), file_name)
        media_upload.create_media_archive(
            MediaObject.objects.all().order_by("gramps_id"),
            zip_path,
            include_private=include_private,
        )
        return Response(
            {
                "file_name": file_name,
                "url": f"/api/media/archive/{file_name}",
                "file_size": os.path.getsize(zip_path),
            },
            status=201,
        )


class MediaArchiveFileView(APIView):
    """
    GET /api/media/archive/<filename>  (``?jwt=`` auth)

    Download a previously generated archive. The file is deleted after it
    has been handed to the response (one-time download, as in the original).
    """

    permission_classes = [AllowAny]

    def get(self, request, filename):
        user, err = _authenticate(request)
        if err:
            return err
        if not ARCHIVE_NAME_RE.match(filename):
            return error_response("Invalid filename", 422)
        file_path = os.path.join(media_upload.export_dir(), filename)
        if not os.path.isfile(file_path):
            return error_response("Archive not found", 404)
        date_str = time.strftime("%Y%m%d%H%M%S", time.localtime(os.path.getmtime(file_path)))
        fobj = open(file_path, "rb")
        try:
            os.remove(file_path)  # the open handle keeps the data readable (POSIX)
        except OSError:
            pass
        response = FileResponse(fobj, content_type="application/zip")
        response["Content-Disposition"] = content_disposition_header(
            True, f"gramps-web-media-export-{date_str}.zip"
        )
        return response


# --- media archive (import) ------------------------------------------------


class MediaImporter:
    """
    Restore missing media files from an extracted ZIP archive.

    Port of gramps-web-api ``MediaImporter``:

    - objects whose file is missing are matched to archive files by MD5
      checksum (file names do not matter) and the file is stored at the
      object's path;
    - objects without a checksum are matched by relative path; their checksum
      is then set (through a transaction) and they take part in the first
      step.
    """

    def __init__(self, user, zip_path):
        self.user = user
        self.zip_path = zip_path

    @staticmethod
    def _missing_files():
        """Return {checksum: [{"handle", "media_path", "mime"}]} for missing files."""
        missing = {}
        for media in MediaObject.objects.all():
            if media_upload.file_exists(media):
                continue
            missing.setdefault(media.checksum or "", []).append({
                "handle": media.handle,
                "media_path": media.path,
                "mime": media.mime,
            })
        return missing

    @staticmethod
    def _walk(temp_dir):
        for root, _, files in os.walk(temp_dir):
            for name in files:
                abs_path = os.path.join(root, name)
                yield abs_path, os.path.relpath(abs_path, temp_dir)

    def _fix_missing_checksums(self, temp_dir, missing):
        """Set checksums on objects without one if the archive has their path."""
        handles_by_path = {}
        for details in missing.get("", []):
            handles_by_path.setdefault(details["media_path"], []).append(details["handle"])
        checksums = {}
        for abs_path, rel_path in self._walk(temp_dir):
            if rel_path in handles_by_path:
                checksum = media_upload.checksum_of_file(abs_path)
                for handle in handles_by_path[rel_path]:
                    checksums[handle] = checksum
        if not checksums:
            return 0

        def do(builder):
            for handle, checksum in checksums.items():
                media = MediaObject.objects.get(pk=handle)
                data = serialize(media)
                data["checksum"] = checksum
                builder.update(data, "Media", handle)

        run_transaction(self.user, "Updating checksums on media", do)
        return len(checksums)

    def _files_to_upload(self, temp_dir, missing):
        to_upload = {}
        for abs_path, _ in self._walk(temp_dir):
            checksum = media_upload.checksum_of_file(abs_path)
            if checksum in missing and checksum not in to_upload:
                to_upload[checksum] = abs_path
        return to_upload

    @staticmethod
    def _upload(to_upload, missing):
        failures = 0
        for checksum, abs_path in to_upload.items():
            for details in missing[checksum]:
                try:
                    with open(abs_path, "rb") as f:
                        media_upload.store_stream(f, details["media_path"])
                except Exception:  # noqa: BLE001
                    failures += 1
        return failures

    def run(self):
        """Import the archive; returns {"missing", "uploaded", "failures"}."""
        missing = self._missing_files()
        if not missing:
            return {"missing": 0, "uploaded": 0, "failures": 0}
        temp_dir = tempfile.mkdtemp(prefix="media-import-")
        try:
            with zipfile.ZipFile(self.zip_path) as zf:
                zf.extractall(temp_dir)
            if "" in missing:
                if self._fix_missing_checksums(temp_dir, missing):
                    missing = self._missing_files()
                missing.pop("", None)
            to_upload = self._files_to_upload(temp_dir, missing)
            if not to_upload:
                return {"missing": len(missing), "uploaded": 0, "failures": 0}
            failures = self._upload(to_upload, missing)
            return {
                "missing": len(missing),
                "uploaded": len(to_upload) - failures,
                "failures": failures,
            }
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class MediaArchiveUploadView(APIView):
    """
    POST /api/media/archive/upload/zip  (raw ZIP body)

    Restores files for media objects whose file is missing. Returns 201 with
    ``{"missing", "uploaded", "failures"}``.
    """

    permission_classes = [IsAuthenticated, HasGrampsPermission]
    required_permissions = [PERM_EDIT_OBJ]

    def post(self, request):
        zip_path = os.path.join(media_upload.export_dir(), f"{uuid.uuid4()}.zip")
        try:
            size = media_upload.write_body_to_file(request, zip_path)
            if size == 0:
                return error_response("Imported file is empty", 400)
            try:
                with zipfile.ZipFile(zip_path) as zf:
                    zf.namelist()
            except zipfile.BadZipFile:
                return error_response("The uploaded file is not a valid ZIP file.", 400)
            result = MediaImporter(request.user, zip_path).run()
        finally:
            if os.path.exists(zip_path):
                os.remove(zip_path)
        return Response(result, status=status.HTTP_201_CREATED)
