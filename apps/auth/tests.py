"""Tests for the user management and first-run endpoints."""

from rest_framework.test import APITestCase

from .models import GrampsUser
from .permissions import ROLE_ADMIN, ROLE_EDITOR, ROLE_GUEST, ROLE_MEMBER, ROLE_OWNER
from .views import _build_tokens


class AuthTestCase(APITestCase):
    def login(self, user):
        token = _build_tokens(user)["access_token"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return token


class FirstRunTests(AuthTestCase):
    def test_first_run_flow(self):
        res = self.client.post("/api/token/create_owner/", {}, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        token = res.data["access_token"]
        self.assertTrue(token)

        # the limited token is not a valid access token
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(self.client.get("/api/users/-/").status_code, 401)

        res = self.client.post(
            "/api/users/firstowner/create_owner/",
            {"password": "secret123", "email": "o@example.com", "full_name": "First Owner"},
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        user = GrampsUser.objects.get(username="firstowner")
        self.assertEqual(user.role, ROLE_ADMIN)
        self.assertEqual(user.full_name, "First Owner")
        self.assertTrue(user.check_password("secret123"))

        # once a user exists both endpoints are closed
        res = self.client.post("/api/users/second/create_owner/", {"password": "secret123"}, format="json")
        self.assertEqual(res.status_code, 403)
        self.client.credentials()
        res = self.client.post("/api/token/create_owner/", {}, format="json")
        self.assertEqual(res.status_code, 403)
        self.assertIn("error", res.data)

        # login works and the owner can store config (first-run flow)
        res = self.client.post("/api/token/", {"username": "firstowner", "password": "secret123"}, format="json")
        self.assertEqual(res.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access_token']}")
        res = self.client.put("/api/config/EMAIL_HOST/", {"value": "smtp"}, format="json")
        self.assertEqual(res.status_code, 200)

    def test_create_owner_token_checks(self):
        res = self.client.post("/api/users/x/create_owner/", {"password": "secret123"}, format="json")
        self.assertEqual(res.status_code, 401)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer not-a-token")
        res = self.client.post("/api/users/x/create_owner/", {"password": "secret123"}, format="json")
        self.assertEqual(res.status_code, 401)
        # a normal access token has the wrong scope/type
        user = GrampsUser.objects.create_user("tmp", "secret123", role=ROLE_OWNER)
        access = _build_tokens(user)["access_token"]
        user.delete()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        res = self.client.post("/api/users/x/create_owner/", {"password": "secret123"}, format="json")
        self.assertEqual(res.status_code, 401)
        self.client.credentials()
        token = self.client.post("/api/token/create_owner/", {}, format="json").data["access_token"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        res = self.client.post("/api/users/-/create_owner/", {"password": "secret123"}, format="json")
        self.assertEqual(res.status_code, 404)
        res = self.client.post("/api/users/x/create_owner/", {"password": "123"}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(GrampsUser.objects.count(), 0)


class UserManagementTests(AuthTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = GrampsUser.objects.create_user("owner", "secret123", role=ROLE_OWNER, full_name="Own Er")
        cls.admin = GrampsUser.objects.create_user("admin", "secret123", role=ROLE_ADMIN)
        cls.member = GrampsUser.objects.create_user("member", "secret123", role=ROLE_MEMBER, email="m@example.com")

    def test_list_shape(self):
        self.login(self.owner)
        res = self.client.get("/api/users/")
        self.assertEqual(res.status_code, 200)
        names = {u["name"] for u in res.data}
        self.assertEqual(names, {"owner", "admin", "member"})
        for u in res.data:
            self.assertEqual(set(u), {"name", "username", "email", "full_name", "role", "tree"})
        self.login(self.member)
        self.assertEqual(self.client.get("/api/users/").status_code, 403)

    def test_own_user(self):
        self.login(self.member)
        res = self.client.get("/api/users/-/")
        self.assertEqual(res.data["name"], "member")
        res = self.client.put("/api/users/-/", {"email": "new@example.com"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(GrampsUser.objects.get(username="member").email, "new@example.com")
        # cannot raise own role
        res = self.client.put("/api/users/-/", {"role": ROLE_OWNER}, format="json")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(GrampsUser.objects.get(username="member").role, ROLE_MEMBER)
        self.assertEqual(self.client.delete("/api/users/-/").status_code, 404)

    def test_create_user_via_username_path(self):
        self.login(self.owner)
        payload = {"role": ROLE_EDITOR, "email": "e@example.com", "full_name": "Ed Itor", "password": "secret123"}
        res = self.client.post("/api/users/editor/", payload, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        user = GrampsUser.objects.get(username="editor")
        self.assertEqual(user.role, ROLE_EDITOR)
        self.assertTrue(user.check_password("secret123"))
        res = self.client.post("/api/users/editor/", payload, format="json")
        self.assertEqual(res.status_code, 409)
        # owner may not create admins
        res = self.client.post("/api/users/boss/", {**payload, "role": ROLE_ADMIN}, format="json")
        self.assertEqual(res.status_code, 403)
        self.login(self.admin)
        res = self.client.post("/api/users/boss/", {**payload, "role": ROLE_ADMIN}, format="json")
        self.assertEqual(res.status_code, 201)
        self.login(self.member)
        res = self.client.post("/api/users/other/", payload, format="json")
        self.assertEqual(res.status_code, 403)

    def test_create_users_via_list_endpoint(self):
        self.login(self.owner)
        res = self.client.post(
            "/api/users/", {"username": "u1", "password": "secret123", "role": ROLE_GUEST}, format="json"
        )
        self.assertEqual(res.status_code, 201, res.data)
        res = self.client.post("/api/users/", [{"name": "u2", "role": 0}, {"name": "u3", "role": 1}], format="json")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertTrue(GrampsUser.objects.filter(username__in=["u2", "u3"]).count() == 2)
        self.assertFalse(GrampsUser.objects.get(username="u2").has_usable_password())

    def test_update_and_delete_other_user(self):
        self.login(self.owner)
        res = self.client.put("/api/users/member/", {"role": ROLE_EDITOR, "full_name": "Mem Ber"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        user = GrampsUser.objects.get(username="member")
        self.assertEqual((user.role, user.full_name), (ROLE_EDITOR, "Mem Ber"))
        res = self.client.put("/api/users/member/", {"role": ROLE_ADMIN}, format="json")
        self.assertEqual(res.status_code, 403)
        res = self.client.put("/api/users/member/", {"name_new": "member2"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(GrampsUser.objects.filter(username="member2").exists())
        res = self.client.delete("/api/users/member2/")
        self.assertEqual(res.status_code, 200)
        self.assertFalse(GrampsUser.objects.filter(username="member2").exists())

    def test_password_change(self):
        self.login(self.member)
        res = self.client.post("/api/users/-/password/change", {"new_password": "newpass123"}, format="json")
        self.assertEqual(res.status_code, 400)
        res = self.client.post(
            "/api/users/-/password/change",
            {"old_password": "wrong", "new_password": "newpass123"}, format="json",
        )
        self.assertEqual(res.status_code, 403)
        res = self.client.post(
            "/api/users/-/password/change",
            {"old_password": "secret123", "new_password": "newpass123"}, format="json",
        )
        self.assertEqual(res.status_code, 201)
        self.assertTrue(GrampsUser.objects.get(username="member").check_password("newpass123"))
        # other users: needs EditOtherUser, no old password
        res = self.client.post("/api/users/owner/password/change", {"new_password": "hacked123"}, format="json")
        self.assertEqual(res.status_code, 403)
        self.login(self.owner)
        res = self.client.post("/api/users/member/password/change", {"new_password": "reset1234"}, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertTrue(GrampsUser.objects.get(username="member").check_password("reset1234"))
        self.assertEqual(
            self.client.post("/api/users/nobody/password/change", {"new_password": "reset1234"}, format="json").status_code,
            404,
        )
