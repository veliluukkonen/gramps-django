"""
Media file serving endpoints.

Serves media files, thumbnails, and cropped images.
Supports JWT auth via query parameter for direct browser access.
"""

import os

from django.conf import settings
from django.http import FileResponse, HttpResponse
from PIL import Image
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from apps.core.models import MediaObject

from .auth import jwt_from_query_or_header


class MediaFileView(APIView):
    """
    GET /api/media/<handle>/file

    Serves the original media file.
    Query params: jwt (auth token), download (force download)
    """

    permission_classes = [AllowAny]

    def get(self, request, handle):
        user = jwt_from_query_or_header(request)
        if user is None:
            return HttpResponse(
                '{"error": "Authentication required"}',
                content_type="application/json",
                status=401,
            )

        try:
            media = MediaObject.objects.get(pk=handle)
        except MediaObject.DoesNotExist:
            return HttpResponse(
                '{"error": "Media not found"}',
                content_type="application/json",
                status=404,
            )

        file_path = os.path.join(settings.MEDIA_ROOT, media.path)
        if not os.path.isfile(file_path):
            return HttpResponse(
                '{"error": "File not found on disk"}',
                content_type="application/json",
                status=404,
            )

        download = request.query_params.get("download") in ("1", "true")
        response = FileResponse(
            open(file_path, "rb"),
            content_type=media.mime or "application/octet-stream",
        )
        if download:
            filename = os.path.basename(media.path)
            response["Content-Disposition"] = f'attachment; filename="{filename}"'
        if media.checksum:
            response["ETag"] = f'"{media.checksum}"'
        return response


class MediaThumbnailView(APIView):
    """
    GET /api/media/<handle>/thumbnail/<size>

    Serves a thumbnail of the media file.
    Query params: jwt, square (bool)
    """

    permission_classes = [AllowAny]

    def get(self, request, handle, size):
        user = jwt_from_query_or_header(request)
        if user is None:
            return HttpResponse(
                '{"error": "Authentication required"}',
                content_type="application/json",
                status=401,
            )

        try:
            media = MediaObject.objects.get(pk=handle)
        except MediaObject.DoesNotExist:
            return HttpResponse(
                '{"error": "Media not found"}',
                content_type="application/json",
                status=404,
            )

        file_path = os.path.join(settings.MEDIA_ROOT, media.path)
        if not os.path.isfile(file_path):
            return HttpResponse(
                '{"error": "File not found on disk"}',
                content_type="application/json",
                status=404,
            )

        square = request.query_params.get("square") in ("1", "true", "True")
        size = int(size)

        try:
            img = Image.open(file_path)
            img = _make_thumbnail(img, size, square)
            response = HttpResponse(content_type="image/jpeg")
            img.save(response, "JPEG", quality=85)
            return response
        except Exception:
            return HttpResponse(
                '{"error": "Cannot generate thumbnail"}',
                content_type="application/json",
                status=500,
            )


class MediaCroppedView(APIView):
    """
    GET /api/media/<handle>/cropped/<x1>/<y1>/<x2>/<y2>

    Serves a cropped version of the media file.
    Coordinates are percentages (0-100).
    """

    permission_classes = [AllowAny]

    def get(self, request, handle, x1, y1, x2, y2):
        user = jwt_from_query_or_header(request)
        if user is None:
            return HttpResponse(
                '{"error": "Authentication required"}',
                content_type="application/json",
                status=401,
            )

        try:
            media = MediaObject.objects.get(pk=handle)
        except MediaObject.DoesNotExist:
            return HttpResponse(
                '{"error": "Media not found"}',
                content_type="application/json",
                status=404,
            )

        file_path = os.path.join(settings.MEDIA_ROOT, media.path)
        if not os.path.isfile(file_path):
            return HttpResponse(
                '{"error": "File not found on disk"}',
                content_type="application/json",
                status=404,
            )

        try:
            img = Image.open(file_path)
            img = _crop_image(img, float(x1), float(y1), float(x2), float(y2))
            response = HttpResponse(content_type="image/jpeg")
            img.save(response, "JPEG", quality=90)
            return response
        except Exception:
            return HttpResponse(
                '{"error": "Cannot crop image"}',
                content_type="application/json",
                status=500,
            )


class MediaCroppedThumbnailView(APIView):
    """
    GET /api/media/<handle>/cropped/<x1>/<y1>/<x2>/<y2>/thumbnail/<size>
    """

    permission_classes = [AllowAny]

    def get(self, request, handle, x1, y1, x2, y2, size):
        user = jwt_from_query_or_header(request)
        if user is None:
            return HttpResponse(
                '{"error": "Authentication required"}',
                content_type="application/json",
                status=401,
            )

        try:
            media = MediaObject.objects.get(pk=handle)
        except MediaObject.DoesNotExist:
            return HttpResponse(
                '{"error": "Media not found"}',
                content_type="application/json",
                status=404,
            )

        file_path = os.path.join(settings.MEDIA_ROOT, media.path)
        if not os.path.isfile(file_path):
            return HttpResponse(
                '{"error": "File not found on disk"}',
                content_type="application/json",
                status=404,
            )

        square = request.query_params.get("square") in ("1", "true", "True")
        size = int(size)

        try:
            img = Image.open(file_path)
            img = _crop_image(img, float(x1), float(y1), float(x2), float(y2))
            img = _make_thumbnail(img, size, square)
            response = HttpResponse(content_type="image/jpeg")
            img.save(response, "JPEG", quality=85)
            return response
        except Exception:
            return HttpResponse(
                '{"error": "Cannot process image"}',
                content_type="application/json",
                status=500,
            )


def _make_thumbnail(img, size, square=False):
    """Create a thumbnail from a PIL Image."""
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    if square:
        # Crop to square first
        w, h = img.size
        min_dim = min(w, h)
        left = (w - min_dim) // 2
        top = (h - min_dim) // 2
        img = img.crop((left, top, left + min_dim, top + min_dim))

    img.thumbnail((size, size), Image.LANCZOS)
    return img


def _crop_image(img, x1, y1, x2, y2):
    """Crop an image using percentage coordinates (0-100)."""
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    w, h = img.size
    left = int(w * x1 / 100)
    upper = int(h * y1 / 100)
    right = int(w * x2 / 100)
    lower = int(h * y2 / 100)

    return img.crop((left, upper, right, lower))
