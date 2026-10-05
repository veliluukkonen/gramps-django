"""Development settings."""

import os
from .base import *  # noqa: F401, F403

DEBUG = True
ALLOWED_HOSTS = ["*"]

# Development database — override base.py DATABASE_URL with explicit
# credentials from the environment.  This makes it easy to use
# docker-compose without having to build the URL by hand.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "gramps"),
        "USER": os.environ.get("POSTGRES_USER", "gramps"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "gramps_dev"),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}