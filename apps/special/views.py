"""
Special API endpoints: metadata, search, translations.
"""

from django.db.models import Q
from rest_framework.permissions import AllowAny, IsAuthenticated
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
    Tag,
)
from apps.core.profile import (
    get_citation_profile,
    get_event_profile,
    get_family_profile,
    get_media_profile,
    get_note_profile,
    get_person_profile,
    get_place_profile,
    get_repository_profile,
    get_source_profile,
)

GRAMPS_DJANGO_VERSION = "0.1.0"


class MetadataView(APIView):
    """
    GET /api/metadata/

    Returns database metadata, object counts, version info.
    Compatible with gramps-web frontend expectations.
    """

    permission_classes = [AllowAny]

    def get(self, request):
        data = {
            "database": {
                "id": "gramps-django",
                "name": "Gramps Django",
                "type": "postgresql",
                "version": "16",
                "module": "django",
                "schema": "1",
                "actual_schema": "1",
            },
            "default_person": None,
            "gramps": {
                "version": "5.2.0",
            },
            "gramps_webapi": {
                "schema": "1",
                "version": GRAMPS_DJANGO_VERSION,
            },
            "locale": {
                "lang": "fi_FI",
                "language": "Finnish",
                "description": "Finnish",
                "incomplete_translation": False,
            },
            "object_counts": {
                "people": Person.objects.count(),
                "families": Family.objects.count(),
                "sources": Source.objects.count(),
                "citations": Citation.objects.count(),
                "events": Event.objects.count(),
                "media": MediaObject.objects.count(),
                "places": Place.objects.count(),
                "repositories": Repository.objects.count(),
                "notes": Note.objects.count(),
                "tags": Tag.objects.count(),
            },
            "researcher": {
                "name": "",
                "addr": "",
                "city": "",
                "country": "",
                "county": "",
                "email": "",
                "locality": "",
                "phone": "",
                "postal": "",
                "state": "",
                "street": "",
            },
            "search": {
                "sifts": {
                    "version": "0.0.0",
                    "count": 0,
                },
            },
            "server": {
                "multi_tree": False,
                "task_queue": False,
                "ocr": False,
                "ocr_languages": [],
                "semantic_search": False,
                "chat": False,
            },
        }

        return Response(data)


class SearchView(APIView):
    """
    GET /api/search/?query=...

    Full-text search across all Gramps objects using PostgreSQL.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        query = request.query_params.get("query", "").strip()
        if not query:
            return Response([])

        page = int(request.query_params.get("page", 1))
        pagesize = int(request.query_params.get("pagesize", 20))
        profile_param = request.query_params.get("profile", "")
        profile_args = set(profile_param.split(",")) if profile_param else set()

        # Filter by object type if specified
        type_filter = request.query_params.get("type", "")
        allowed_types = set(type_filter.split(",")) if type_filter else None

        results = []

        # Search across all object types
        search_configs = [
            ("person", Person, _search_people, get_person_profile),
            ("family", Family, _search_families, get_family_profile),
            ("event", Event, _search_events, get_event_profile),
            ("place", Place, _search_places, get_place_profile),
            ("source", Source, _search_sources, get_source_profile),
            ("citation", Citation, _search_citations, get_citation_profile),
            ("repository", Repository, _search_repositories, get_repository_profile),
            ("media", MediaObject, _search_media, get_media_profile),
            ("note", Note, _search_notes, get_note_profile),
        ]

        for type_name, model, search_func, profile_func in search_configs:
            if allowed_types and type_name not in allowed_types:
                continue

            matches = search_func(query)
            for obj in matches:
                result = {
                    "handle": obj.handle,
                    "object_type": type_name,
                    "score": 1.0,
                    "change": obj.change,
                }
                if profile_args:
                    result["object"] = {"handle": obj.handle}
                    result["object"]["profile"] = profile_func(obj, profile_args)
                results.append(result)

        # Sort by change (most recent first)
        results.sort(key=lambda r: r["change"], reverse=True)

        # Paginate
        total = len(results)
        start = (page - 1) * pagesize
        end = start + pagesize
        page_results = results[start:end]

        response = Response(page_results)
        response["X-Total-Count"] = total
        return response


def _search_people(query):
    """Search people by name fields."""
    return Person.objects.filter(
        Q(gramps_id__icontains=query)
        | Q(primary_name__first_name__icontains=query)
        | Q(primary_name__surname_list__contains=[{"surname": query}])
    )[:100]


def _search_families(query):
    return Family.objects.filter(
        Q(gramps_id__icontains=query) | Q(type__icontains=query)
    )[:100]


def _search_events(query):
    return Event.objects.filter(
        Q(gramps_id__icontains=query)
        | Q(type__icontains=query)
        | Q(description__icontains=query)
    )[:100]


def _search_places(query):
    return Place.objects.filter(
        Q(gramps_id__icontains=query)
        | Q(title__icontains=query)
        | Q(name__value__icontains=query)
    )[:100]


def _search_sources(query):
    return Source.objects.filter(
        Q(gramps_id__icontains=query)
        | Q(title__icontains=query)
        | Q(author__icontains=query)
    )[:100]


def _search_citations(query):
    return Citation.objects.filter(
        Q(gramps_id__icontains=query) | Q(page__icontains=query)
    )[:100]


def _search_repositories(query):
    return Repository.objects.filter(
        Q(gramps_id__icontains=query) | Q(name__icontains=query)
    )[:100]


def _search_media(query):
    return MediaObject.objects.filter(
        Q(gramps_id__icontains=query) | Q(desc__icontains=query)
    )[:100]


def _search_notes(query):
    return Note.objects.filter(
        Q(gramps_id__icontains=query) | Q(text__string__icontains=query)
    )[:100]


class TranslationsListView(APIView):
    """
    GET /api/translations/

    Returns list of available languages.
    """

    permission_classes = [AllowAny]

    def get(self, request):
        # Basic set of languages — can be expanded later
        languages = [
            {"language": "en", "default": "English", "current": "English", "native": "English"},
            {"language": "fi", "default": "Finnish", "current": "Suomi", "native": "Suomi"},
            {"language": "sv", "default": "Swedish", "current": "Svenska", "native": "Svenska"},
            {"language": "de", "default": "German", "current": "Deutsch", "native": "Deutsch"},
            {"language": "fr", "default": "French", "current": "Français", "native": "Français"},
        ]
        return Response(languages)


class TranslationsDetailView(APIView):
    """
    GET /api/translations/<language>?strings=["str1","str2"]
    POST /api/translations/<language> body: {"strings": [...]}

    Returns translations for given strings.
    Currently returns originals as-is (translation engine not yet implemented).
    """

    permission_classes = [AllowAny]

    def get(self, request, language):
        import json

        strings_param = request.query_params.get("strings", "[]")
        try:
            strings = json.loads(strings_param)
        except (json.JSONDecodeError, TypeError):
            strings = []
        return Response(self._translate(strings, language))

    def post(self, request, language):
        strings = request.data.get("strings", [])
        return Response(self._translate(strings, language))

    def _translate(self, strings, language):
        # Placeholder — returns originals. Replace with real translation later.
        return [
            {"original": s, "translation": s}
            for s in strings
        ]
