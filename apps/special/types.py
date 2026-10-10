"""
Types API: ``/api/types/...``.

Replaces ``gramps_webapi/api/resources/types.py``. The default (built-in)
Gramps type tables are read from the static file ``data/types.json`` that is
produced by ``scripts/generate_gramps_data.py`` from the Gramps core source
tree, so the ``gramps`` package is not needed at runtime. Custom types are the
type strings found in the database that are not among the defaults.
"""

import json
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import (
    Citation,
    Event,
    Family,
    MediaObject,
    Note,
    Person,
    Place,
    Repository,
    Source,
)

DATA_DIR = Path(__file__).resolve().parent / "data"

# Same key lists and order as gramps-web-api.
DEFAULT_RECORD_TYPES = [
    "attribute_types",
    "child_reference_types",
    "event_role_types",
    "event_types",
    "family_relation_types",
    "gender_types",
    "name_origin_types",
    "name_types",
    "note_types",
    "place_types",
    "repository_types",
    "source_attribute_types",
    "source_media_types",
    "url_types",
]

CUSTOM_RECORD_TYPES = [
    "child_reference_types",
    "event_attribute_types",
    "event_role_types",
    "event_types",
    "family_attribute_types",
    "family_relation_types",
    "media_attribute_types",
    "name_origin_types",
    "name_types",
    "note_types",
    "person_attribute_types",
    "place_types",
    "repository_types",
    "source_attribute_types",
    "source_media_types",
    "url_types",
]

# Which default category the values of a custom category are compared against.
_CUSTOM_TO_DEFAULT = {
    "event_attribute_types": "attribute_types",
    "family_attribute_types": "attribute_types",
    "media_attribute_types": "attribute_types",
    "person_attribute_types": "attribute_types",
    "source_attribute_types": "source_attribute_types",
}


@lru_cache(maxsize=1)
def _load_types() -> dict:
    """Load and cache ``data/types.json``."""
    with (DATA_DIR / "types.json").open(encoding="utf-8") as fh:
        return json.load(fh)


def _normalize_language(language: str | None) -> str:
    """Map a language/locale code to one of the generated name lists."""
    available = _load_types()["languages"]
    if not language:
        return "en"
    if language in available:
        return language
    base = language.replace("-", "_").split("_")[0].split(".")[0]
    return base if base in available else "en"


def default_language() -> str:
    """Language used for localized type names (``settings.GRAMPS_LANGUAGE``)."""
    return _normalize_language(getattr(settings, "GRAMPS_LANGUAGE", None))


def parse_bool(value) -> bool:
    """Interpret a query parameter as boolean (``1``, ``true``, ``yes``...)."""
    if value is None:
        return False
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def get_default_types(datatype: str, locale: bool = False) -> list | None:
    """Return the default type names for a category, or None if unknown."""
    info = _load_types()["types"].get(datatype)
    if info is None:
        return None
    if locale:
        names = info["names"]
        return list(names.get(default_language()) or names["en"])
    return list(info["xml"])


def get_default_type_map(datatype: str, locale: bool = False) -> dict | None:
    """Return the integer code -> name map for a default category."""
    info = _load_types()["types"].get(datatype)
    if info is None:
        return None
    if not locale:
        return dict(info["map"])
    names = info["names"].get(default_language()) or info["names"]["en"]
    xml_to_name = dict(zip(info["xml"], names))
    return {code: xml_to_name.get(xml, xml) for code, xml in info["map"].items()}


# --------------------------------------------------------------------------
# Custom types from the database
# --------------------------------------------------------------------------


def _type_str(value) -> str:
    """Return the string form of a stored type value (str or Gramps-like dict)."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("string") or "")
    return ""


def _attr_types(attribute_list) -> set:
    return {_type_str(a.get("type")) for a in attribute_list or [] if isinstance(a, dict)}


def _media_ref_attr_types(media_list) -> set:
    result = set()
    for mref in media_list or []:
        if isinstance(mref, dict):
            result |= _attr_types(mref.get("attribute_list"))
    return result


def _url_types(urls) -> set:
    return {_type_str(u.get("type")) for u in urls or [] if isinstance(u, dict)}


def _role_types(event_ref_list) -> set:
    return {_type_str(r.get("role")) for r in event_ref_list or [] if isinstance(r, dict)}


def collect_db_types() -> dict:
    """
    Scan the database and return ``{custom category: set of type strings}``
    including default values; see :func:`get_custom_types` for filtering.
    Mirrors what ``gramps.gen.db.generic.DbGeneric`` records on commit.
    """
    found = {key: set() for key in CUSTOM_RECORD_TYPES}

    for primary_name, alternate_names, event_refs, urls, attrs, media in (
        Person.objects.values_list(
            "primary_name", "alternate_names", "event_ref_list", "urls",
            "attribute_list", "media_list",
        )
    ):
        found["person_attribute_types"] |= _attr_types(attrs)
        found["event_role_types"] |= _role_types(event_refs)
        found["url_types"] |= _url_types(urls)
        found["media_attribute_types"] |= _media_ref_attr_types(media)
        names = [primary_name] + list(alternate_names or [])
        for name in names:
            if not isinstance(name, dict):
                continue
            found["name_types"].add(_type_str(name.get("type")))
            for surname in name.get("surname_list") or []:
                if isinstance(surname, dict):
                    found["name_origin_types"].add(_type_str(surname.get("origintype")))

    for ftype, child_refs, event_refs, attrs, media in Family.objects.values_list(
        "type", "child_ref_list", "event_ref_list", "attribute_list", "media_list"
    ):
        found["family_relation_types"].add(_type_str(ftype))
        found["family_attribute_types"] |= _attr_types(attrs)
        found["event_role_types"] |= _role_types(event_refs)
        found["media_attribute_types"] |= _media_ref_attr_types(media)
        for ref in child_refs or []:
            if isinstance(ref, dict):
                found["child_reference_types"].add(_type_str(ref.get("frel")))
                found["child_reference_types"].add(_type_str(ref.get("mrel")))

    for etype, attrs, media in Event.objects.values_list("type", "attribute_list", "media_list"):
        found["event_types"].add(_type_str(etype))
        found["event_attribute_types"] |= _attr_types(attrs)
        found["media_attribute_types"] |= _media_ref_attr_types(media)

    for ptype, urls, media in Place.objects.values_list("place_type", "urls", "media_list"):
        found["place_types"].add(_type_str(ptype))
        found["url_types"] |= _url_types(urls)
        found["media_attribute_types"] |= _media_ref_attr_types(media)

    for rtype, urls in Repository.objects.values_list("type", "urls"):
        found["repository_types"].add(_type_str(rtype))
        found["url_types"] |= _url_types(urls)

    for ntype in Note.objects.values_list("type", flat=True):
        found["note_types"].add(_type_str(ntype))

    for reporefs, attrs, media in Source.objects.values_list(
        "reporef_list", "attribute_list", "media_list"
    ):
        found["source_attribute_types"] |= _attr_types(attrs)
        found["media_attribute_types"] |= _media_ref_attr_types(media)
        for ref in reporefs or []:
            if isinstance(ref, dict):
                found["source_media_types"].add(_type_str(ref.get("media_type")))

    for attrs, media in Citation.objects.values_list("attribute_list", "media_list"):
        found["source_attribute_types"] |= _attr_types(attrs)
        found["media_attribute_types"] |= _media_ref_attr_types(media)

    for attrs in MediaObject.objects.values_list("attribute_list", flat=True):
        found["media_attribute_types"] |= _attr_types(attrs)

    return found


def get_custom_types(datatype: str, found: dict | None = None) -> list | None:
    """
    Return the sorted custom (non-default) values for a custom category,
    or None if the category is unknown. ``found`` may be a precomputed
    result of :func:`collect_db_types` to avoid rescanning the database.
    """
    if datatype not in CUSTOM_RECORD_TYPES:
        return None
    if found is None:
        found = collect_db_types()
    defaults = set(get_default_types(_CUSTOM_TO_DEFAULT.get(datatype, datatype)) or [])
    return sorted(v for v in found[datatype] if v and v not in defaults)


def get_all_custom_types() -> dict:
    """Return ``{category: [custom values]}`` for all custom categories."""
    found = collect_db_types()
    return {key: get_custom_types(key, found) for key in CUSTOM_RECORD_TYPES}


def get_all_default_types(locale: bool = False) -> dict:
    """Return ``{category: [names]}`` for all default categories."""
    return {key: get_default_types(key, locale) for key in DEFAULT_RECORD_TYPES}


def _not_found(datatype: str) -> Response:
    return Response(
        {"error": {"message": f"Unknown type category: {datatype}"}}, status=404
    )


# --------------------------------------------------------------------------
# Views
# --------------------------------------------------------------------------


class TypesView(APIView):
    """GET /api/types/ -> ``{"default": {...}, "custom": {...}}`` (``?locale=1``)."""

    permission_classes = [AllowAny]

    def get(self, request):
        locale = parse_bool(request.query_params.get("locale"))
        return Response(
            {"default": get_all_default_types(locale), "custom": get_all_custom_types()}
        )


class DefaultTypesView(APIView):
    """GET /api/types/default/ -> all default type lists (``?locale=1``)."""

    permission_classes = [AllowAny]

    def get(self, request):
        locale = parse_bool(request.query_params.get("locale"))
        return Response(get_all_default_types(locale))


class DefaultTypeView(APIView):
    """GET /api/types/default/<datatype> -> list of names (``?locale=1``)."""

    permission_classes = [AllowAny]

    def get(self, request, datatype):
        locale = parse_bool(request.query_params.get("locale"))
        result = get_default_types(datatype, locale)
        if result is None:
            return _not_found(datatype)
        return Response(result)


class DefaultTypeMapView(APIView):
    """GET /api/types/default/<datatype>/map -> ``{code: name}`` (``?locale=1``)."""

    permission_classes = [AllowAny]

    def get(self, request, datatype):
        locale = parse_bool(request.query_params.get("locale"))
        result = get_default_type_map(datatype, locale)
        if result is None:
            return _not_found(datatype)
        return Response(result)


class CustomTypesView(APIView):
    """GET /api/types/custom/ -> all custom type lists."""

    permission_classes = [AllowAny]

    def get(self, request):
        return Response(get_all_custom_types())


class CustomTypeView(APIView):
    """GET /api/types/custom/<datatype> -> list of custom values."""

    permission_classes = [AllowAny]

    def get(self, request, datatype):
        result = get_custom_types(datatype)
        if result is None:
            return _not_found(datatype)
        return Response(result)
