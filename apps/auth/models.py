"""
Custom User model for Gramps authentication.

Uses email as optional, username as primary identifier.
Role field determines permissions via the PERMISSIONS mapping.
"""

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager
from django.db import models

from .permissions import ROLE_ADMIN, ROLE_CHOICES, ROLE_GUEST


class GrampsUserManager(BaseUserManager):
    def create_user(self, username, password=None, **extra_fields):
        if not username:
            raise ValueError("Username is required")
        user = self.model(username=username, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, username, password=None, **extra_fields):
        extra_fields.setdefault("role", ROLE_ADMIN)
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self.create_user(username, password, **extra_fields)


class GrampsUser(AbstractBaseUser):
    """
    Gramps user with role-based permissions.

    Compatible with gramps-web-api user model:
    - username: unique identifier
    - role: integer role level (Guest=0 through Admin=5)
    - email: optional
    - full_name: optional display name
    """

    username = models.CharField(max_length=150, unique=True)
    email = models.EmailField(blank=True, default="")
    full_name = models.CharField(max_length=200, blank=True, default="")
    role = models.IntegerField(choices=ROLE_CHOICES, default=ROLE_GUEST)
    tree = models.CharField(
        max_length=100, blank=True, default="",
        help_text="Tree identifier for multi-tree support",
    )

    # Django admin compatibility
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    is_superuser = models.BooleanField(default=False)

    date_joined = models.DateTimeField(auto_now_add=True)

    objects = GrampsUserManager()

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = []

    class Meta:
        db_table = "gramps_user"

    def __str__(self):
        return self.username

    def has_perm(self, perm, obj=None):
        return self.is_superuser

    def has_module_perms(self, app_label):
        return self.is_superuser
