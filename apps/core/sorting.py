"""
Sort implementation for Gramps objects.

Supports sort query parameter with object-type-specific keys.
Prefix with - for descending order.
"""

from django.db.models import F, Value
from django.db.models.functions import Lower


# Sort key → ORM expression mappings per model
PERSON_SORT_KEYS = {
    "gramps_id": "gramps_id",
    "change": "change",
    "private": "private",
    "gender": "gender",
    # JSON field lookups for name sorting
    "name": "primary_name__surname_list__0__surname",
    "surname": "primary_name__surname_list__0__surname",
    # birth/death sort by event date sortval — requires annotation or raw
    "birth_ref_index": "birth_ref_index",
    "death_ref_index": "death_ref_index",
}

FAMILY_SORT_KEYS = {
    "gramps_id": "gramps_id",
    "change": "change",
    "private": "private",
    "type": "type",
}

EVENT_SORT_KEYS = {
    "gramps_id": "gramps_id",
    "change": "change",
    "private": "private",
    "type": "type",
    "description": "description",
    "date": "date__sortval",
}

PLACE_SORT_KEYS = {
    "gramps_id": "gramps_id",
    "change": "change",
    "private": "private",
    "title": "title",
    "type": "place_type",
    "place_type": "place_type",
}

SOURCE_SORT_KEYS = {
    "gramps_id": "gramps_id",
    "change": "change",
    "private": "private",
    "title": "title",
    "author": "author",
}

CITATION_SORT_KEYS = {
    "gramps_id": "gramps_id",
    "change": "change",
    "private": "private",
    "confidence": "confidence",
    "date": "date__sortval",
    "page": "page",
}

REPOSITORY_SORT_KEYS = {
    "gramps_id": "gramps_id",
    "change": "change",
    "private": "private",
    "name": "name",
    "type": "type",
}

MEDIA_SORT_KEYS = {
    "gramps_id": "gramps_id",
    "change": "change",
    "private": "private",
    "title": "desc",
    "desc": "desc",
    "path": "path",
    "mime": "mime",
    "date": "date__sortval",
}

NOTE_SORT_KEYS = {
    "gramps_id": "gramps_id",
    "change": "change",
    "private": "private",
    "type": "type",
}

TAG_SORT_KEYS = {
    "name": "name",
    "change": "change",
    "color": "color",
    "priority": "priority",
}

# Model name → sort keys mapping
SORT_KEYS_MAP = {
    "Person": PERSON_SORT_KEYS,
    "Family": FAMILY_SORT_KEYS,
    "Event": EVENT_SORT_KEYS,
    "Place": PLACE_SORT_KEYS,
    "Source": SOURCE_SORT_KEYS,
    "Citation": CITATION_SORT_KEYS,
    "Repository": REPOSITORY_SORT_KEYS,
    "MediaObject": MEDIA_SORT_KEYS,
    "Note": NOTE_SORT_KEYS,
    "Tag": TAG_SORT_KEYS,
}


def apply_sort(queryset, sort_param, model_name):
    """
    Apply sort parameter to a queryset.

    sort_param: comma-separated sort keys, prefix with - for descending
    model_name: name of the model class
    """
    if not sort_param:
        return queryset

    sort_keys = SORT_KEYS_MAP.get(model_name, {})
    order_by = []

    for key in sort_param.split(","):
        key = key.strip()
        if not key:
            continue

        descending = key.startswith("-")
        if descending:
            key = key[1:]

        orm_field = sort_keys.get(key)
        if not orm_field:
            continue

        if descending:
            order_by.append(F(orm_field).desc(nulls_last=True))
        else:
            order_by.append(F(orm_field).asc(nulls_last=True))

    if order_by:
        return queryset.order_by(*order_by)
    return queryset
