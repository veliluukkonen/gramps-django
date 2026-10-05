"""Single-use JWT for password reset links."""

import hashlib

from django.conf import settings
from rest_framework_simplejwt.tokens import Token


def _password_fingerprint(user):
    """Short hash of the current password hash; changes when the password does."""
    return hashlib.sha256(user.password.encode("utf-8")).hexdigest()[:16]


class PasswordResetToken(Token):
    """
    Token sent in the password reset e-mail.

    Carries a fingerprint of the user's current password hash, so the
    token becomes invalid as soon as the password has been changed.
    """

    token_type = "password_reset"
    lifetime = settings.PASSWORD_RESET_TOKEN_LIFETIME

    @classmethod
    def for_user(cls, user):
        token = super().for_user(user)
        token["pwd"] = _password_fingerprint(user)
        return token

    def matches_user(self, user):
        return self.get("pwd") == _password_fingerprint(user)
