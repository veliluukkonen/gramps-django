"""
Media file storage helpers.

Files live under ``settings.MEDIA_ROOT``; ``MediaObject.path`` is relative to
it (absolute paths inside the media root are tolerated for databases that
were imported from Gramps desktop). Uploaded files are stored as
``<md5 checksum><extension>``, exactly like gramps-web-api's
``LocalFileHandler``: the same content always ends up at the same path, so
re-uploading a known file simply overwrites it with identical bytes.

The request body is streamed to a temporary file while the MD5 is computed,
so large uploads never have to fit in memory (and Django's
``DATA_UPLOAD_MAX_MEMORY_SIZE`` limit for ``request.body`` does not apply).
"""

import hashlib
import mimetypes
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

from django.conf import settings

# Extensions preferred for the given MIME types (mimetypes.guess_extension is
# platform dependent and e.g. returns ".jpe" for image/jpeg on some systems).
# Mirrors gramps-web-api ``const.MIME_TYPES``.
MIME_TYPES = {
    ".pdf": "application/pdf",
    ".gvpdf": "application/pdf",
    ".gspdf": "application/pdf",
    ".gv": "text/vnd.graphviz",
    ".dot": "text/vnd.graphviz",
    ".rtf": "application/rtf",
    ".ps": "application/postscript",
    ".svg": "image/svg+xml",
    ".svgz": "image/svg+xml",
    ".jpg": "image/jpeg",
    ".gif": "image/gif",
    ".png": "image/png",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".tex": "application/x-tex",
    ".txt": "text/plain",
    ".html": "text/html",
}

CHUNK_SIZE = 64 * 1024

# Sub directory of MEDIA_ROOT used for media archives (export and import).
EXPORT_SUBDIR = "export"


# --- paths -----------------------------------------------------------------


def media_root():
    """Return the absolute media base directory."""
    return os.path.abspath(str(settings.MEDIA_ROOT))


def export_dir():
    """Return the directory for media archives, creating it if needed."""
    path = os.path.join(media_root(), EXPORT_SUBDIR)
    os.makedirs(path, exist_ok=True)
    return path


def is_within_media_root(abs_path):
    """Return True if ``abs_path`` lies inside the media base directory."""
    base = Path(media_root()).resolve()
    try:
        target = Path(abs_path).resolve()
    except OSError:
        return False
    return base in target.parents


def media_file_path(media):
    """
    Return the absolute file path for a media object (or object-like).

    ``media`` may be a ``MediaObject`` or any object with a ``path``
    attribute. Relative paths are resolved against ``MEDIA_ROOT``; absolute
    paths are returned unchanged. No existence check is made.
    """
    path = getattr(media, "path", None) or ""
    if not path:
        return ""
    if os.path.isabs(path):
        return path
    return os.path.join(media_root(), path)


def file_exists(media):
    """
    Return True if the media object's file exists inside ``MEDIA_ROOT``.

    Files outside the media base directory are treated as missing, as in
    gramps-web-api.
    """
    abs_path = media_file_path(media)
    if not abs_path or not is_within_media_root(abs_path):
        return False
    return os.path.isfile(abs_path)


def relative_media_path(path):
    """
    Normalize a media path to one relative to ``MEDIA_ROOT``.

    Raises ``ValueError`` for empty paths and for paths that escape the base
    directory.
    """
    if not path:
        raise ValueError("Missing file path")
    if os.path.isabs(path):
        abs_path = path
    else:
        abs_path = os.path.join(media_root(), path)
    if not is_within_media_root(abs_path):
        raise ValueError("File path is not within the media directory")
    return os.path.relpath(os.path.abspath(abs_path), media_root())


# --- checksums and names ---------------------------------------------------


def get_checksum(fp):
    """Return the MD5 hex digest of a binary file-like object (Gramps style)."""
    md5 = hashlib.md5()
    while True:
        buf = fp.read(CHUNK_SIZE)
        if not buf:
            break
        md5.update(buf)
    return md5.hexdigest()


def checksum_of_file(abs_path):
    """Return the MD5 hex digest of the file at ``abs_path``."""
    with open(abs_path, "rb") as f:
        return get_checksum(f)


def get_extension(mime, filename=""):
    """
    Return the file extension (with leading dot) for a MIME type.

    Falls back to the extension of ``filename`` when the MIME type is
    unknown. Returns ``None`` if nothing suitable is found.
    """
    mime = (mime or "").split(";")[0].strip().lower()
    for ext, ext_mime in MIME_TYPES.items():
        if mime == ext_mime:
            return ext
    if mime:
        ext = mimetypes.guess_extension(mime, strict=False)
        if ext:
            return ext
    if filename:
        ext = os.path.splitext(filename)[1]
        if ext:
            return ext.lower()
    return None


def default_filename(checksum, mime, filename=""):
    """Return the default relative file name for a checksum and MIME type."""
    if not mime:
        raise ValueError("Media type not recognized")
    ext = get_extension(mime, filename)
    if not ext:
        raise ValueError("MIME type not recognized")
    return f"{checksum}{ext}"


# --- request parsing -------------------------------------------------------


def _http_request(request):
    """Return the underlying Django HttpRequest for a DRF Request."""
    return getattr(request, "_request", request)


def _iter_raw_body(http_request):
    """Yield the raw request body in chunks without loading it into memory."""
    while True:
        chunk = http_request.read(CHUNK_SIZE)
        if not chunk:
            break
        yield chunk


def _iter_body(request):
    """
    Yield (chunks, mime, filename) for an uploaded file.

    Supports a raw body (the gramps-web way: the File object is the body and
    the Content-Type header is its MIME type) and, for convenience, a
    ``multipart/form-data`` upload where the first file part is used.
    """
    http = _http_request(request)
    content_type = (http.content_type or "").lower()
    if content_type == "multipart/form-data":
        files = list(http.FILES.values())
        if not files:
            raise ValueError("No file in multipart request")
        upload = files[0]
        mime = (upload.content_type or "").split(";")[0].strip().lower()
        if not mime or mime == "application/octet-stream":
            guessed, _ = mimetypes.guess_type(upload.name or "")
            mime = guessed or mime
        return upload.chunks(CHUNK_SIZE), mime, upload.name or ""
    if not content_type or content_type in (
        "application/x-www-form-urlencoded",
    ):
        raise ValueError("Media type not recognized")
    return _iter_raw_body(http), content_type, ""


class ReceivedFile:
    """
    An uploaded file sitting in a temporary location inside ``MEDIA_ROOT``.

    Call :meth:`store` to move it to its final place, or :meth:`discard`.
    """

    def __init__(self, tmp_path, checksum, size, mime, filename=""):
        self.tmp_path = tmp_path
        self.checksum = checksum
        self.size = size
        self.mime = mime
        self.filename = filename

    @property
    def default_path(self):
        """Relative path ``<checksum><ext>`` for this file."""
        return default_filename(self.checksum, self.mime, self.filename)

    def store(self, rel_path=None):
        """
        Move the file to ``MEDIA_ROOT/<rel_path>`` (default: by checksum).

        An existing file at the destination is replaced. Returns the
        relative path.
        """
        rel_path = relative_media_path(rel_path or self.default_path)
        dest = os.path.join(media_root(), rel_path)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        os.replace(self.tmp_path, dest)
        self.tmp_path = None
        return rel_path

    def discard(self):
        """Delete the temporary file (if still present)."""
        if self.tmp_path and os.path.exists(self.tmp_path):
            os.remove(self.tmp_path)
        self.tmp_path = None


def receive_upload(request):
    """
    Read the uploaded file from the request into a temporary file.

    Returns a :class:`ReceivedFile`. Raises ``ValueError`` for an empty body
    or an unusable content type. The caller must ``store()`` or
    ``discard()`` the result.
    """
    chunks, mime, filename = _iter_body(request)
    if not mime:
        raise ValueError("Media type not recognized")
    # Validate the MIME type before reading anything.
    default_filename("0" * 32, mime, filename)

    root = media_root()
    os.makedirs(root, exist_ok=True)
    md5 = hashlib.md5()
    size = 0
    fd, tmp_path = tempfile.mkstemp(prefix=".upload-", dir=root)
    try:
        with os.fdopen(fd, "wb") as tmp:
            for chunk in chunks:
                md5.update(chunk)
                size += len(chunk)
                tmp.write(chunk)
    except BaseException:
        os.remove(tmp_path)
        raise
    if size == 0:
        os.remove(tmp_path)
        raise ValueError("Uploaded file is empty")
    return ReceivedFile(tmp_path, md5.hexdigest(), size, mime, filename)


def save_uploaded_file(request):
    """
    Store the file uploaded in ``request`` under ``MEDIA_ROOT``.

    The file is saved as ``<md5 checksum><ext>`` (an existing file with the
    same checksum is simply overwritten with identical content). Returns
    ``{"path": <relative path>, "mime": <mime type>, "checksum": <md5 hex>}``.
    Raises ``ValueError`` for empty bodies or unsupported content types.
    """
    received = receive_upload(request)
    try:
        path = received.store()
    except BaseException:
        received.discard()
        raise
    return {"path": path, "mime": received.mime, "checksum": received.checksum}


def store_stream(stream, rel_path):
    """
    Write a binary stream to ``MEDIA_ROOT/<rel_path>`` atomically.

    Returns the normalized relative path. Raises ``ValueError`` if the path
    escapes the media directory.
    """
    rel_path = relative_media_path(rel_path)
    dest = os.path.join(media_root(), rel_path)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".upload-", dir=os.path.dirname(dest))
    try:
        with os.fdopen(fd, "wb") as tmp:
            shutil.copyfileobj(stream, tmp, CHUNK_SIZE)
        os.replace(tmp_path, dest)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
    return rel_path


def write_body_to_file(request, dest_path):
    """Stream the raw request body to ``dest_path``. Returns the byte count."""
    http = _http_request(request)
    size = 0
    with open(dest_path, "wb") as f:
        for chunk in _iter_raw_body(http):
            f.write(chunk)
            size += len(chunk)
    return size


# --- archives --------------------------------------------------------------


def iter_archive_files(media_objects, include_private=True):
    """
    Yield ``(abs_path, arcname)`` for the media files that exist on disk.

    Duplicate paths (several objects sharing a file) are yielded once.
    """
    seen = set()
    for media in media_objects:
        if not include_private and media.private:
            continue
        if not file_exists(media):
            continue
        abs_path = os.path.abspath(media_file_path(media))
        if abs_path in seen:
            continue
        seen.add(abs_path)
        yield abs_path, os.path.relpath(abs_path, media_root())


def create_media_archive(media_objects, zip_path, include_private=True):
    """Write a ZIP archive of all existing media files. Returns file count."""
    count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for abs_path, arcname in iter_archive_files(media_objects, include_private):
            zf.write(abs_path, arcname=arcname)
            count += 1
    return count
