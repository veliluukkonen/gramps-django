"""
Normalization of Gramps type strings on write.

The frontend sends type values in the UI language (e.g. "Syntymä" for an
event of type Birth, "Päähenkilö" for the role Primary). gramps-web-api
stores them through ``GrampsType``, which maps a localized default name back
to the standard (XML) value. Without that mapping a localized birth event is
not recognized as a birth (birth_ref_index, family pages, charts...).

Unknown strings are custom types and are kept as they are.
"""

from functools import lru_cache


@lru_cache(maxsize=1)
def _reverse_maps() -> dict:
    """{category: {localized or English name: XML value}} for every language."""
    # Local import: apps.special imports apps.core.
    from apps.special.types import _load_types

    result = {}
    for category, info in _load_types()["types"].items():
        xml = info["xml"]
        mapping = {}
        # English names first, then the other languages; XML values win.
        languages = sorted(info["names"], key=lambda lang: lang != "en")
        for lang in languages:
            for name, value in zip(info["names"][lang], xml):
                mapping.setdefault(name, value)
        for value in xml:
            mapping[value] = value
        result[category] = mapping
    return result


def normalize_type(value, category):
    """Map a (possibly localized) type value to its standard XML string."""
    mapping = _reverse_maps().get(category, {})
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, dict) and isinstance(value.get("string"), str):
        return {**value, "string": mapping.get(value["string"], value["string"])}
    return value


def _norm_key(obj, key, category):
    if isinstance(obj, dict) and key in obj:
        obj[key] = normalize_type(obj[key], category)


def _norm_list(items, key, category):
    for item in items or []:
        _norm_key(item, key, category)


def _norm_attributes(attribute_list, category="attribute_types"):
    _norm_list(attribute_list, "type", category)


def _norm_media_refs(media_list):
    for mref in media_list or []:
        if isinstance(mref, dict):
            _norm_attributes(mref.get("attribute_list"))


def _norm_event_refs(event_ref_list):
    for ref in event_ref_list or []:
        if isinstance(ref, dict):
            _norm_key(ref, "role", "event_role_types")
            _norm_attributes(ref.get("attribute_list"))


def _norm_name(name):
    if not isinstance(name, dict):
        return
    _norm_key(name, "type", "name_types")
    _norm_list(name.get("surname_list"), "origintype", "name_origin_types")


def normalize_types(data, class_name):
    """
    Normalize all type fields of an object dict in place (model field
    names, i.e. after ``prepare_data``) and return it.
    """
    _norm_attributes(data.get("attribute_list"),
                     "source_attribute_types" if class_name in ("Source", "Citation")
                     else "attribute_types")
    _norm_media_refs(data.get("media_list"))
    _norm_list(data.get("urls"), "type", "url_types")

    if class_name == "Person":
        _norm_name(data.get("primary_name"))
        for name in data.get("alternate_names") or []:
            _norm_name(name)
        _norm_event_refs(data.get("event_ref_list"))
    elif class_name == "Family":
        _norm_key(data, "type", "family_relation_types")
        _norm_event_refs(data.get("event_ref_list"))
        for ref in data.get("child_ref_list") or []:
            _norm_key(ref, "frel", "child_reference_types")
            _norm_key(ref, "mrel", "child_reference_types")
    elif class_name == "Event":
        _norm_key(data, "type", "event_types")
    elif class_name == "Place":
        _norm_key(data, "place_type", "place_types")
    elif class_name == "Note":
        _norm_key(data, "type", "note_types")
    elif class_name == "Repository":
        _norm_key(data, "type", "repository_types")
    elif class_name == "Source":
        _norm_list(data.get("reporef_list"), "media_type", "source_media_types")
    return data
