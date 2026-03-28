"""
Extend parameter implementation.

Resolves handle references to full objects and adds them
under an 'extended' key in the response.
"""

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


def _safe_get(model, handle):
    """Get object by handle, return None if not found."""
    if not handle:
        return None
    try:
        return model.objects.get(pk=handle)
    except model.DoesNotExist:
        return None


def _serialize_obj(obj, serializer_class, context):
    """Serialize a model instance to dict."""
    if obj is None:
        return {}
    from . import serializers as ser
    serializer = serializer_class(obj, context=context)
    return serializer.data


def _get_serializer_for_model(model):
    """Return the appropriate serializer class for a model."""
    from . import serializers as ser
    mapping = {
        Person: ser.PersonSerializer,
        Family: ser.FamilySerializer,
        Event: ser.EventSerializer,
        Place: ser.PlaceSerializer,
        Source: ser.SourceSerializer,
        Citation: ser.CitationSerializer,
        Repository: ser.RepositorySerializer,
        MediaObject: ser.MediaObjectSerializer,
        Note: ser.NoteSerializer,
        Tag: ser.TagSerializer,
    }
    return mapping.get(model)


def _resolve_handle_list(handles, model, context):
    """Resolve a list of handles to serialized objects."""
    if not handles:
        return []
    objs = model.objects.filter(pk__in=handles)
    serializer_class = _get_serializer_for_model(model)
    return [_serialize_obj(obj, serializer_class, context) for obj in objs]


def _resolve_ref_list(ref_list, model, context):
    """Resolve a list of reference objects (with 'ref' key) to full objects."""
    if not ref_list:
        return []
    handles = [ref.get("ref") for ref in ref_list if ref.get("ref")]
    return _resolve_handle_list(handles, model, context)


def get_extended_attributes(obj, extend_keys, context):
    """
    Compute the 'extended' dictionary for a Gramps object.

    extend_keys: set of extend key strings (or {'all'})
    context: serializer context (contains request)

    Returns dict with resolved objects.
    """
    if not extend_keys:
        return {}

    all_keys = "all" in extend_keys
    extended = {}
    data = obj if isinstance(obj, dict) else None

    # Helper to check if key is requested
    def want(key):
        return all_keys or key in extend_keys

    # Get raw field values
    if data is None:
        # Working with model instance
        model_name = obj.__class__.__name__

        if want("citation_list") and hasattr(obj, "citation_list"):
            extended["citations"] = _resolve_handle_list(
                obj.citation_list, Citation, context
            )

        if want("event_ref_list") and hasattr(obj, "event_ref_list"):
            extended["events"] = _resolve_ref_list(
                obj.event_ref_list, Event, context
            )

        if want("media_list") and hasattr(obj, "media_list"):
            extended["media"] = _resolve_ref_list(
                obj.media_list, MediaObject, context
            )

        if want("note_list") and hasattr(obj, "note_list"):
            extended["notes"] = _resolve_handle_list(
                obj.note_list, Note, context
            )

        if want("tag_list") and hasattr(obj, "tag_list"):
            extended["tags"] = _resolve_handle_list(
                obj.tag_list, Tag, context
            )

        if want("person_ref_list") and hasattr(obj, "person_ref_list"):
            extended["people"] = _resolve_ref_list(
                obj.person_ref_list, Person, context
            )

        if want("reporef_list") and hasattr(obj, "reporef_list"):
            extended["repositories"] = _resolve_ref_list(
                obj.reporef_list, Repository, context
            )

        if want("child_ref_list") and hasattr(obj, "child_ref_list"):
            extended["children"] = _resolve_ref_list(
                obj.child_ref_list, Person, context
            )

        # Person-specific extends
        if model_name == "Person":
            if want("family_list"):
                extended["families"] = _resolve_handle_list(
                    obj.family_list, Family, context
                )
            if want("parent_family_list"):
                extended["parent_families"] = _resolve_handle_list(
                    obj.parent_family_list, Family, context
                )
            if want("primary_parent_family"):
                parent_families = obj.parent_family_list or []
                if parent_families:
                    fam = _safe_get(Family, parent_families[0])
                    serializer_class = _get_serializer_for_model(Family)
                    extended["primary_parent_family"] = _serialize_obj(
                        fam, serializer_class, context
                    )
                else:
                    extended["primary_parent_family"] = {}

        # Family-specific extends
        if model_name == "Family":
            if want("father_handle"):
                father = _safe_get(Person, obj.father_handle_id)
                serializer_class = _get_serializer_for_model(Person)
                extended["father"] = _serialize_obj(
                    father, serializer_class, context
                )
            if want("mother_handle"):
                mother = _safe_get(Person, obj.mother_handle_id)
                serializer_class = _get_serializer_for_model(Person)
                extended["mother"] = _serialize_obj(
                    mother, serializer_class, context
                )

        # Event-specific extends
        if model_name == "Event":
            if want("place"):
                place = _safe_get(Place, obj.place_id)
                serializer_class = _get_serializer_for_model(Place)
                extended["place"] = _serialize_obj(
                    place, serializer_class, context
                )

        # Citation-specific extends
        if model_name == "Citation":
            if want("source_handle"):
                source = _safe_get(Source, obj.source_handle_id)
                serializer_class = _get_serializer_for_model(Source)
                extended["source"] = _serialize_obj(
                    source, serializer_class, context
                )

    return extended
