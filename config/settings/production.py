"""Production settings for Docker deployment."""

import os

from .base import *  # noqa: F401, F403

DEBUG = os.environ.get("DJANGO_DEBUG", "False").lower() in ("1", "true", "yes")
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "*").split(",")
