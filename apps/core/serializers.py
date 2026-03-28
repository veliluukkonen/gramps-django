"""
DRF serializers for Gramps objects.

These produce JSON output compatible with the Gramps Web API.
JSONField values are passed through as-is since they already
contain the correct nested structure.
"""

from rest_framework import serializers

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


class GrampsBaseSerializer(serializers.ModelSerializer):
    """
    Base serializer for Gramps primary objects.

    Handles:
    - Gramps null conventions (None → "" for handles, None → [] for lists)
    - keys/skipkeys/strip query param filtering
    - _class field injection
    """

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["_class"] = self._gramps_class_name()

        request = self.context.get("request")
        if request:
            data = self._apply_key_filters(data, request)
            if request.query_params.get("strip"):
                data = self._strip_empty(data)

        return data

    def _gramps_class_name(self):
        model = self.Meta.model
        if model == MediaObject:
            return "Media"
        return model.__name__

    def _apply_key_filters(self, data, request):
        keys = request.query_params.get("keys")
        skipkeys = request.query_params.get("skipkeys")
        if keys:
            allowed = set(keys.split(","))
            allowed.add("handle")  # always include handle
            data = {k: v for k, v in data.items() if k in allowed}
        elif skipkeys:
            excluded = set(skipkeys.split(","))
            data = {k: v for k, v in data.items() if k not in excluded}
        return data

    def _strip_empty(self, data):
        return {
            k: v
            for k, v in data.items()
            if v is not None and v != "" and v != [] and v != {}
        }


class PersonSerializer(GrampsBaseSerializer):
    class Meta:
        model = Person
        fields = [
            "handle", "gramps_id", "change", "private", "tag_list",
            "gender", "primary_name", "alternate_names",
            "event_ref_list", "family_list", "parent_family_list",
            "person_ref_list", "media_list", "address_list",
            "attribute_list", "citation_list", "note_list",
            "urls", "lds_ord_list", "birth_ref_index", "death_ref_index",
        ]


class FamilySerializer(GrampsBaseSerializer):
    # Return FK handles as plain strings
    father_handle = serializers.CharField(
        source="father_handle_id", default="", allow_null=True, allow_blank=True
    )
    mother_handle = serializers.CharField(
        source="mother_handle_id", default="", allow_null=True, allow_blank=True
    )

    class Meta:
        model = Family
        fields = [
            "handle", "gramps_id", "change", "private", "tag_list",
            "father_handle", "mother_handle", "type",
            "child_ref_list", "event_ref_list", "media_list",
            "attribute_list", "citation_list", "note_list",
            "lds_ord_list",
        ]


class EventSerializer(GrampsBaseSerializer):
    # Return place FK as plain string handle
    place = serializers.CharField(
        source="place_id", default="", allow_null=True, allow_blank=True
    )

    class Meta:
        model = Event
        fields = [
            "handle", "gramps_id", "change", "private", "tag_list",
            "type", "date", "place", "description",
            "citation_list", "media_list", "note_list", "attribute_list",
        ]


class PlaceSerializer(GrampsBaseSerializer):
    class Meta:
        model = Place
        fields = [
            "handle", "gramps_id", "change", "private", "tag_list",
            "name", "title", "alt_names", "place_type", "code",
            "lat", "long", "placeref_list", "alt_loc",
            "urls", "media_list", "citation_list", "note_list",
        ]


class SourceSerializer(GrampsBaseSerializer):
    class Meta:
        model = Source
        fields = [
            "handle", "gramps_id", "change", "private", "tag_list",
            "title", "author", "pubinfo", "abbrev",
            "reporef_list", "media_list", "note_list", "attribute_list",
        ]


class CitationSerializer(GrampsBaseSerializer):
    # Return source FK as plain string handle
    source_handle = serializers.CharField(
        source="source_handle_id", default="", allow_null=True, allow_blank=True
    )

    class Meta:
        model = Citation
        fields = [
            "handle", "gramps_id", "change", "private", "tag_list",
            "source_handle", "page", "date", "confidence",
            "media_list", "note_list", "attribute_list",
        ]


class RepositorySerializer(GrampsBaseSerializer):
    class Meta:
        model = Repository
        fields = [
            "handle", "gramps_id", "change", "private", "tag_list",
            "name", "type", "address_list", "urls", "note_list",
        ]


class MediaObjectSerializer(GrampsBaseSerializer):
    class Meta:
        model = MediaObject
        fields = [
            "handle", "gramps_id", "change", "private", "tag_list",
            "path", "mime", "desc", "checksum", "date",
            "attribute_list", "citation_list", "note_list",
        ]


class NoteSerializer(GrampsBaseSerializer):
    class Meta:
        model = Note
        fields = [
            "handle", "gramps_id", "change", "private", "tag_list",
            "text", "format", "type",
        ]


class TagSerializer(serializers.ModelSerializer):
    """Tag serializer — Tag has no gramps_id or private fields."""

    _class = serializers.SerializerMethodField()

    class Meta:
        model = Tag
        fields = ["_class", "handle", "name", "color", "priority", "change"]

    def get__class(self, obj):
        return "Tag"

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        if request:
            keys = request.query_params.get("keys")
            skipkeys = request.query_params.get("skipkeys")
            if keys:
                allowed = set(keys.split(","))
                allowed.add("handle")
                data = {k: v for k, v in data.items() if k in allowed}
            elif skipkeys:
                excluded = set(skipkeys.split(","))
                data = {k: v for k, v in data.items() if k not in excluded}
            if request.query_params.get("strip"):
                data = {
                    k: v for k, v in data.items()
                    if v is not None and v != "" and v != [] and v != {}
                }
        return data
