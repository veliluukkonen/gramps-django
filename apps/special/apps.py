from django.apps import AppConfig


class SpecialConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.special"
    label = "gramps_special"
    verbose_name = "Gramps Special"
