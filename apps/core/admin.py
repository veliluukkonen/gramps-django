from django.contrib import admin

from .models import (
    Citation,
    Event,
    Family,
    MediaObject,
    Note,
    Person,
    Place,
    Repository,
    Source,
    Tag,
)


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ["handle", "gramps_id", "gender", "private", "change"]
    search_fields = ["gramps_id", "handle"]


@admin.register(Family)
class FamilyAdmin(admin.ModelAdmin):
    list_display = ["handle", "gramps_id", "type", "father_handle", "mother_handle"]
    search_fields = ["gramps_id", "handle"]


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ["handle", "gramps_id", "type", "description"]
    search_fields = ["gramps_id", "handle", "type"]


@admin.register(Place)
class PlaceAdmin(admin.ModelAdmin):
    list_display = ["handle", "gramps_id", "title", "place_type"]
    search_fields = ["gramps_id", "handle", "title"]


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ["handle", "gramps_id", "title", "author"]
    search_fields = ["gramps_id", "handle", "title"]


@admin.register(Citation)
class CitationAdmin(admin.ModelAdmin):
    list_display = ["handle", "gramps_id", "source_handle", "page", "confidence"]
    search_fields = ["gramps_id", "handle"]


@admin.register(Repository)
class RepositoryAdmin(admin.ModelAdmin):
    list_display = ["handle", "gramps_id", "name", "type"]
    search_fields = ["gramps_id", "handle", "name"]


@admin.register(MediaObject)
class MediaObjectAdmin(admin.ModelAdmin):
    list_display = ["handle", "gramps_id", "desc", "mime", "path"]
    search_fields = ["gramps_id", "handle", "desc"]


@admin.register(Note)
class NoteAdmin(admin.ModelAdmin):
    list_display = ["handle", "gramps_id", "type", "format"]
    search_fields = ["gramps_id", "handle"]


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ["handle", "name", "color", "priority"]
    search_fields = ["name", "handle"]
