"""Shared test helpers for the tree app."""

import os

from rest_framework.test import APITestCase

from apps.auth.models import GrampsUser
from apps.auth.permissions import ROLE_ADMIN, ROLE_GUEST, ROLE_MEMBER, ROLE_OWNER
from apps.auth.views import _build_tokens
from apps.core.writes import run_transaction

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
EXAMPLE_GRAMPS = os.path.join(FIXTURES, "data.gramps")


class TreeTestCase(APITestCase):
    """APITestCase with an owner, a member and a guest user."""

    @classmethod
    def setUpTestData(cls):
        cls.owner = GrampsUser.objects.create_user(
            "owner", "secret123", role=ROLE_OWNER, full_name="Tree Owner", email="owner@example.com"
        )
        cls.admin = GrampsUser.objects.create_user("admin", "secret123", role=ROLE_ADMIN)
        cls.member = GrampsUser.objects.create_user("member", "secret123", role=ROLE_MEMBER)
        cls.guest = GrampsUser.objects.create_user("guest", "secret123", role=ROLE_GUEST)

    def login(self, user):
        token = _build_tokens(user)["access_token"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return token

    def logout(self):
        self.client.credentials()

    @staticmethod
    def add_objects(user, objects, description="Add"):
        """Create objects via TransactionBuilder; returns the transaction list."""

        def func(builder):
            for obj in objects:
                builder.add(obj)

        return run_transaction(user, description, func)

    @staticmethod
    def person(handle, first, surname, gender=1, **extra):
        return {
            "_class": "Person",
            "handle": handle,
            "gender": gender,
            "primary_name": {
                "first_name": first,
                "surname_list": [{"surname": surname, "primary": True, "prefix": "", "connector": "", "origintype": ""}],
                "type": "Birth Name",
            },
            **extra,
        }
