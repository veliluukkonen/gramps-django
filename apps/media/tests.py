"""Tests for the media file endpoints and upload helpers."""

import hashlib
import io
import os
import shutil
import tempfile
import zipfile

from django.test import RequestFactory, TestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.auth.models import GrampsUser
from apps.auth.permissions import ROLE_EDITOR, ROLE_GUEST, ROLE_MEMBER
from apps.core.models import MediaObject, Transaction

from . import upload as media_upload

MINIMAL_PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


def make_png(width=40, height=20, color=(200, 30, 30)):
    """Return PNG bytes of a solid colour image."""
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, "PNG")
    return buf.getvalue()


def md5(data):
    return hashlib.md5(data).hexdigest()


class MediaTestCase(TestCase):
    """Base class: temporary MEDIA_ROOT, an editor user and an API client."""

    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix="gramps-media-test-")
        self._settings = override_settings(MEDIA_ROOT=self.media_root)
        self._settings.enable()
        self.addCleanup(self._settings.disable)
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)

        self.user = GrampsUser.objects.create_user("editor", "pw", role=ROLE_EDITOR)
        self.token = str(AccessToken.for_user(self.user))
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token}")

    def token_for(self, username, role):
        user = GrampsUser.objects.create_user(username, "pw", role=role)
        return str(AccessToken.for_user(user))

    def write_media(self, data, mime="image/png", handle="M1", gramps_id="O0001",
                    private=False, path=None, checksum=None, write_file=True):
        """Create a MediaObject and (optionally) its file on disk."""
        checksum = md5(data) if checksum is None else checksum
        path = path or media_upload.default_filename(md5(data), mime)
        if write_file:
            abs_path = os.path.join(self.media_root, path)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "wb") as f:
                f.write(data)
        return MediaObject.objects.create(
            handle=handle, gramps_id=gramps_id, path=path, mime=mime,
            checksum=checksum, private=private, desc="test",
        )


class UploadHelperTests(MediaTestCase):
    def test_save_uploaded_file_names_by_checksum(self):
        png = make_png()
        request = RequestFactory().post("/api/media/", data=png, content_type="image/png")
        result = media_upload.save_uploaded_file(request)
        self.assertEqual(result, {"path": f"{md5(png)}.png", "mime": "image/png",
                                  "checksum": md5(png)})
        with open(os.path.join(self.media_root, result["path"]), "rb") as f:
            self.assertEqual(f.read(), png)

    def test_save_uploaded_file_dedups_same_content(self):
        png = make_png()
        for _ in range(2):
            request = RequestFactory().post("/api/media/", data=png, content_type="image/png")
            result = media_upload.save_uploaded_file(request)
        self.assertEqual(result["path"], f"{md5(png)}.png")
        self.assertEqual(os.listdir(self.media_root), [f"{md5(png)}.png"])

    def test_save_uploaded_file_jpeg_extension(self):
        request = RequestFactory().post("/api/media/", data=b"x" * 10, content_type="image/jpeg")
        self.assertEqual(media_upload.save_uploaded_file(request)["path"],
                         f"{md5(b'x' * 10)}.jpg")

    def test_save_uploaded_file_rejects_empty_body(self):
        request = RequestFactory().post("/api/media/", data=b"", content_type="image/png")
        with self.assertRaises(ValueError):
            media_upload.save_uploaded_file(request)
        self.assertEqual(os.listdir(self.media_root), [])

    def test_save_uploaded_file_rejects_unknown_mime(self):
        request = RequestFactory().post("/api/media/", data=b"abc",
                                        content_type="application/x-no-such-type")
        with self.assertRaises(ValueError):
            media_upload.save_uploaded_file(request)
        request = RequestFactory().post("/api/media/", data=b"abc", content_type="")
        with self.assertRaises(ValueError):
            media_upload.save_uploaded_file(request)

    def test_save_uploaded_file_multipart(self):
        png = make_png()
        # RequestFactory needs a name for the uploaded file to be a file.
        upload = io.BytesIO(png)
        upload.name = "photo.png"
        request = RequestFactory().post("/api/media/", data={"file": upload})
        result = media_upload.save_uploaded_file(request)
        self.assertEqual(result["path"], f"{md5(png)}.png")
        self.assertEqual(result["mime"], "image/png")

    def test_file_exists_and_media_file_path(self):
        png = make_png()
        media = self.write_media(png)
        self.assertTrue(media_upload.file_exists(media))
        self.assertEqual(media_upload.media_file_path(media),
                         os.path.join(self.media_root, media.path))
        missing = self.write_media(png, handle="M2", gramps_id="O0002", write_file=False,
                                   path="missing.png")
        self.assertFalse(media_upload.file_exists(missing))
        outside = self.write_media(png, handle="M3", gramps_id="O0003", write_file=False,
                                   path="/etc/passwd")
        self.assertFalse(media_upload.file_exists(outside))
        self.assertFalse(media_upload.file_exists(MediaObject(path="")))


class MediaFileViewTests(MediaTestCase):
    def test_get_file_requires_auth(self):
        media = self.write_media(make_png())
        resp = APIClient().get(f"/api/media/{media.handle}/file")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json(), {"error": {"message": "Authentication required"}})

    def test_get_file_with_jwt_query_and_download(self):
        png = make_png()
        media = self.write_media(png)
        resp = APIClient().get(f"/api/media/{media.handle}/file?jwt={self.token}&download=1")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "image/png")
        self.assertEqual(resp["Content-Disposition"],
                         f'attachment; filename="{md5(png)}.png"')
        self.assertEqual(resp["ETag"], f'"{md5(png)}"')
        self.assertEqual(b"".join(resp.streaming_content), png)

    def test_get_file_missing_on_disk(self):
        media = self.write_media(make_png(), write_file=False)
        resp = self.client.get(f"/api/media/{media.handle}/file")
        self.assertEqual(resp.status_code, 404)
        self.assertIn("error", resp.json())

    def test_put_file_replaces_file_and_updates_object(self):
        old_png = make_png(color=(0, 0, 0))
        new_png = make_png(color=(255, 255, 255))
        media = self.write_media(old_png)
        resp = self.client.put(f"/api/media/{media.handle}/file", data=new_png,
                               content_type="image/png")
        self.assertEqual(resp.status_code, 200, resp.content)
        changes = resp.json()
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["type"], "update")
        self.assertEqual(changes[0]["_class"], "Media")
        self.assertEqual(changes[0]["handle"], media.handle)
        self.assertEqual(changes[0]["old"]["checksum"], md5(old_png))
        self.assertEqual(changes[0]["new"]["checksum"], md5(new_png))
        self.assertEqual(changes[0]["new"]["path"], f"{md5(new_png)}.png")
        media.refresh_from_db()
        self.assertEqual(media.checksum, md5(new_png))
        self.assertEqual(media.path, f"{md5(new_png)}.png")
        self.assertEqual(media.mime, "image/png")
        self.assertTrue(media_upload.file_exists(media))
        self.assertEqual(Transaction.objects.count(), 1)

    def test_put_file_same_checksum_conflict(self):
        png = make_png()
        media = self.write_media(png)
        resp = self.client.put(f"/api/media/{media.handle}/file", data=png,
                               content_type="image/png")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(os.listdir(self.media_root), [f"{md5(png)}.png"])

    def test_put_file_uploadmissing_restores_file(self):
        png = make_png()
        media = self.write_media(png, write_file=False, path="sub/photo.png")
        resp = self.client.put(f"/api/media/{media.handle}/file?uploadmissing=1",
                               data=png, content_type="image/png")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(os.path.isfile(os.path.join(self.media_root, "sub/photo.png")))
        media.refresh_from_db()
        self.assertEqual(media.path, "sub/photo.png")
        self.assertEqual(Transaction.objects.count(), 0)

    def test_put_file_uploadmissing_wrong_checksum(self):
        media = self.write_media(make_png(), write_file=False)
        resp = self.client.put(f"/api/media/{media.handle}/file?uploadmissing=1",
                               data=make_png(color=(1, 2, 3)), content_type="image/png")
        self.assertEqual(resp.status_code, 409)

    def test_put_file_etag_mismatch(self):
        media = self.write_media(make_png())
        resp = self.client.put(f"/api/media/{media.handle}/file", data=make_png(color=(1, 2, 3)),
                               content_type="image/png", HTTP_IF_MATCH='"wrong"')
        self.assertEqual(resp.status_code, 412)

    def test_put_file_empty_body(self):
        media = self.write_media(make_png())
        resp = self.client.put(f"/api/media/{media.handle}/file", data=b"",
                               content_type="image/png")
        self.assertEqual(resp.status_code, 400)

    def test_put_file_requires_edit_permission(self):
        media = self.write_media(make_png())
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token_for('member', ROLE_MEMBER)}")
        resp = client.put(f"/api/media/{media.handle}/file", data=make_png(color=(1, 2, 3)),
                          content_type="image/png")
        self.assertEqual(resp.status_code, 403)
        resp = APIClient().put(f"/api/media/{media.handle}/file", data=make_png(),
                               content_type="image/png")
        self.assertEqual(resp.status_code, 401)

    def test_put_file_unknown_handle(self):
        resp = self.client.put("/api/media/nope/file", data=make_png(), content_type="image/png")
        self.assertEqual(resp.status_code, 404)


class ThumbnailTests(MediaTestCase):
    def test_thumbnail_png(self):
        media = self.write_media(make_png(width=400, height=200))
        resp = APIClient().get(f"/api/media/{media.handle}/thumbnail/100?jwt={self.token}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "image/jpeg")
        img = Image.open(io.BytesIO(resp.content))
        self.assertEqual(img.size, (100, 50))

    def test_thumbnail_square(self):
        media = self.write_media(make_png(width=400, height=200))
        resp = self.client.get(f"/api/media/{media.handle}/thumbnail/100?square=1")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Image.open(io.BytesIO(resp.content)).size, (100, 100))

    def test_thumbnail_pdf_returns_404(self):
        media = self.write_media(MINIMAL_PDF, mime="application/pdf")
        resp = self.client.get(f"/api/media/{media.handle}/thumbnail/100")
        self.assertEqual(resp.status_code, 404)
        self.assertIn("application/pdf", resp.json()["error"]["message"])
        resp = self.client.get(f"/api/media/{media.handle}/cropped/0/0/50/50")
        self.assertEqual(resp.status_code, 404)
        resp = self.client.get(f"/api/media/{media.handle}/cropped/0/0/50/50/thumbnail/20")
        self.assertEqual(resp.status_code, 404)

    def test_thumbnail_requires_auth(self):
        media = self.write_media(make_png())
        resp = APIClient().get(f"/api/media/{media.handle}/thumbnail/100")
        self.assertEqual(resp.status_code, 401)

    def test_cropped_and_cropped_thumbnail(self):
        media = self.write_media(make_png(width=400, height=200))
        resp = self.client.get(f"/api/media/{media.handle}/cropped/0/0/50/50")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Image.open(io.BytesIO(resp.content)).size, (200, 100))
        resp = self.client.get(f"/api/media/{media.handle}/cropped/0/0/50/50/thumbnail/20")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Image.open(io.BytesIO(resp.content)).size, (20, 10))
        resp = self.client.get(f"/api/media/{media.handle}/cropped/0/0/150/50")
        self.assertEqual(resp.status_code, 422)


class MediaArchiveTests(MediaTestCase):
    def test_create_and_download_archive(self):
        png = make_png()
        media = self.write_media(png)
        self.write_media(png, handle="M2", gramps_id="O0002", write_file=False,
                         path="missing.png")
        resp = self.client.post("/api/media/archive/")
        self.assertEqual(resp.status_code, 201, resp.content)
        data = resp.json()
        self.assertEqual(set(data), {"file_name", "url", "file_size"})
        self.assertEqual(data["url"], f"/api/media/archive/{data['file_name']}")
        zip_path = os.path.join(self.media_root, "export", data["file_name"])
        self.assertTrue(os.path.isfile(zip_path))
        self.assertEqual(data["file_size"], os.path.getsize(zip_path))
        with zipfile.ZipFile(zip_path) as zf:
            self.assertEqual(zf.namelist(), [media.path])

        # Download with ?jwt= like the frontend does; the file is then gone.
        resp = APIClient().get(f"{data['url']}?jwt={self.token}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/zip")
        self.assertIn('attachment; filename="gramps-web-media-export-', resp["Content-Disposition"])
        content = b"".join(resp.streaming_content)
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            self.assertEqual(zf.read(media.path), png)
        self.assertFalse(os.path.exists(zip_path))
        resp = APIClient().get(f"{data['url']}?jwt={self.token}")
        self.assertEqual(resp.status_code, 404)

    def test_download_archive_requires_auth_and_valid_name(self):
        resp = APIClient().get("/api/media/archive/abc.zip")
        self.assertEqual(resp.status_code, 401)
        resp = self.client.get("/api/media/archive/secret.zip")
        self.assertEqual(resp.status_code, 422)

    def test_archive_excludes_private_for_guest(self):
        png = make_png()
        self.write_media(png, private=True)
        self.write_media(make_png(color=(9, 9, 9)), handle="M2", gramps_id="O0002")
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token_for('guest', ROLE_GUEST)}")
        resp = client.post("/api/media/archive/")
        self.assertEqual(resp.status_code, 201)
        with zipfile.ZipFile(os.path.join(self.media_root, "export", resp.json()["file_name"])) as zf:
            self.assertEqual(zf.namelist(), [f"{md5(make_png(color=(9, 9, 9)))}.png"])
        resp = APIClient().post("/api/media/archive/")
        self.assertEqual(resp.status_code, 401)


class MediaArchiveUploadTests(MediaTestCase):
    @staticmethod
    def make_zip(files):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name, data in files.items():
                zf.writestr(name, data)
        return buf.getvalue()

    def test_zip_upload_restores_missing_file_by_checksum(self):
        png = make_png()
        present = self.write_media(make_png(color=(5, 5, 5)))
        missing = self.write_media(png, handle="M2", gramps_id="O0002", write_file=False,
                                   path="photos/old name.png")
        body = self.make_zip({"whatever/renamed.png": png, "other.txt": b"not needed"})
        resp = self.client.post("/api/media/archive/upload/zip", data=body,
                                content_type="application/zip")
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json(), {"missing": 1, "uploaded": 1, "failures": 0})
        self.assertTrue(media_upload.file_exists(missing))
        with open(media_upload.media_file_path(missing), "rb") as f:
            self.assertEqual(f.read(), png)
        self.assertTrue(media_upload.file_exists(present))
        # Temporary zip is removed again.
        self.assertEqual(os.listdir(os.path.join(self.media_root, "export")), [])

    def test_zip_upload_fixes_missing_checksum_by_path(self):
        png = make_png()
        media = self.write_media(png, write_file=False, path="a/b.png", checksum="")
        body = self.make_zip({"a/b.png": png})
        resp = self.client.post("/api/media/archive/upload/zip", data=body,
                                content_type="application/zip")
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json(), {"missing": 1, "uploaded": 1, "failures": 0})
        media.refresh_from_db()
        self.assertEqual(media.checksum, md5(png))
        self.assertTrue(media_upload.file_exists(media))
        self.assertEqual(Transaction.objects.count(), 1)

    def test_zip_upload_nothing_missing(self):
        self.write_media(make_png())
        resp = self.client.post("/api/media/archive/upload/zip",
                                data=self.make_zip({"x.png": make_png()}),
                                content_type="application/zip")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json(), {"missing": 0, "uploaded": 0, "failures": 0})

    def test_zip_upload_no_matching_file(self):
        self.write_media(make_png(), write_file=False)
        resp = self.client.post("/api/media/archive/upload/zip",
                                data=self.make_zip({"x.png": make_png(color=(1, 1, 1))}),
                                content_type="application/zip")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json(), {"missing": 1, "uploaded": 0, "failures": 0})

    def test_zip_upload_bad_input(self):
        resp = self.client.post("/api/media/archive/upload/zip", data=b"",
                                content_type="application/zip")
        self.assertEqual(resp.status_code, 400)
        resp = self.client.post("/api/media/archive/upload/zip", data=b"not a zip",
                                content_type="application/zip")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("ZIP", resp.json()["error"]["message"])
        self.assertEqual(os.listdir(os.path.join(self.media_root, "export")), [])

    def test_zip_upload_requires_edit_permission(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token_for('member', ROLE_MEMBER)}")
        resp = client.post("/api/media/archive/upload/zip", data=self.make_zip({"a": b"b"}),
                           content_type="application/zip")
        self.assertEqual(resp.status_code, 403)
