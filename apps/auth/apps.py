from django.apps import AppConfig


class GrampsAuthConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.auth"
    label = "gramps_auth"
    verbose_name = "Gramps Auth"
