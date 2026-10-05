"""E-mail notifications for registration and password reset."""

import logging

from django.conf import settings
from django.core.mail import send_mail

from .permissions import ROLE_OWNER

logger = logging.getLogger(__name__)


def base_url(request):
    """Public base URL for links: BASE_URL setting or derived from the request."""
    if settings.BASE_URL:
        return settings.BASE_URL
    return request.build_absolute_uri("/").rstrip("/")


def send_password_reset_email(user, reset_url):
    subject = "Gramps: salasanan vaihto / password reset"
    message = (
        f"Hei {user.full_name or user.username},\n\n"
        "Käyttäjätunnuksellesi pyydettiin salasanan vaihtoa. "
        "Vaihda salasana tästä linkistä (voimassa 1 tunnin):\n\n"
        f"{reset_url}\n\n"
        "Jos et pyytänyt vaihtoa, voit jättää tämän viestin huomiotta.\n\n"
        "---\n"
        f"A password reset was requested for user '{user.username}'. "
        "Use the link above within one hour to set a new password. "
        "If you did not request this, you can ignore this message.\n"
    )
    send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [user.email])


def notify_owners_of_new_user(new_user, manage_url):
    """Tell owners/admins that a new account is waiting to be enabled."""
    from .models import GrampsUser

    recipients = list(
        GrampsUser.objects.filter(role__gte=ROLE_OWNER, is_active=True)
        .exclude(email="")
        .values_list("email", flat=True)
    )
    if not recipients:
        logger.warning("New user %s registered but no owner has an e-mail address", new_user.username)
        return
    subject = "Gramps: uusi käyttäjä rekisteröitynyt / new user registered"
    message = (
        f"Uusi käyttäjä on rekisteröitynyt:\n\n"
        f"  Käyttäjätunnus: {new_user.username}\n"
        f"  Nimi: {new_user.full_name}\n"
        f"  Sähköposti: {new_user.email}\n\n"
        "Tili on poissa käytöstä, kunnes annat sille roolin käyttäjähallinnassa:\n"
        f"{manage_url}\n\n"
        "---\n"
        f"A new user '{new_user.username}' has registered. The account is disabled "
        "until you assign a role in user management.\n"
    )
    try:
        send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, recipients)
    except Exception:  # noqa: BLE001 — registration must not fail on mail errors
        logger.exception("Failed to send new-user notification")
