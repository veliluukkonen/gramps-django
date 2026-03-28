from django.contrib import admin

from .models import GrampsUser


@admin.register(GrampsUser)
class GrampsUserAdmin(admin.ModelAdmin):
    list_display = ["username", "email", "role", "is_active", "date_joined"]
    list_filter = ["role", "is_active"]
    search_fields = ["username", "email", "full_name"]
    ordering = ["username"]
