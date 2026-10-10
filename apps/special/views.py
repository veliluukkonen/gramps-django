"""
Special API endpoints: metadata, search, translations.
"""

from django.db.models import Q
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

# Translation views live in apps.special.translations; re-exported here so
# existing imports (``views.TranslationsListView``) keep working.
from .translations import TranslationsDetailView, TranslationsListView  # noqa: F401

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

GRAMPS_DJANGO_VERSION = "3.3.0"


class MetadataView(APIView):
    """
    GET /api/metadata/

    Returns database metadata, object counts, version info.
    Compatible with gramps-web frontend expectations.
    """

    permission_classes = [AllowAny]

    def get(self, request):
        from django.conf import settings

        from apps.tree.models import Config

        language = settings.GRAMPS_LANGUAGE
        tree_name = Config.get("tree_name", settings.TREE_NAME)
        researcher = Config.get("researcher") or {}
        counts = {
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
        }
        total_objects = sum(v for k, v in counts.items() if k != "tags")
        data = {
            "database": {
                "id": "gramps-django",
                "name": tree_name,
                "type": "postgresql",
                "version": "16",
                "module": "django",
                "schema": "21.0.0",
                "actual_schema": "21.0.0",
            },
            "default_person": Config.get("default_person"),
            "gramps": {
                "version": "5.2.0",
            },
            "gramps_webapi": {
                "schema": "3.3.0",
                "version": GRAMPS_DJANGO_VERSION,
            },
            "locale": {
                "lang": language,
                "language": _LANGUAGE_NAMES.get(language, language),
                "description": _LANGUAGE_NAMES.get(language, language),
                "incomplete_translation": False,
            },
            "object_counts": counts,
            "researcher": {
                key: researcher.get(key, "")
                for key in (
                    "name", "addr", "city", "country", "county", "email",
                    "locality", "phone", "postal", "state", "street",
                )
            },
            "search": {
                # Search is live SQL; report the full object count so the
                # frontend does not ask for a reindex.
                "sifts": {"version": "0.0.0", "count": total_objects},
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


_LANGUAGE_NAMES = {
    "fi": "Finnish",
    "en": "English",
    "sv": "Swedish",
    "de": "German",
}


class SearchView(APIView):
    """
    GET /api/search/?query=...

    Full-text search across all Gramps objects using PostgreSQL.
    """

    permission_classes = [AllowAny]

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

        # Wildcard query returns all (sorted by change)
        is_wildcard = query.strip("*") == ""

        # Sort parameter
        sort_param = request.query_params.get("sort", "")

        results = []

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

            if is_wildcard:
                matches = model.objects.all().order_by("-change")[:100]
            else:
                # Strip trailing wildcard for icontains search
                clean_query = query.rstrip("*")
                matches = search_func(clean_query)

            for obj in matches:
                result = {
                    "handle": obj.handle,
                    "object_type": type_name,
                    "score": 1.0,
                    "change": obj.change,
                    "object": {
                        "handle": obj.handle,
                        "gramps_id": obj.gramps_id or "",
                    },
                }
                if profile_args:
                    result["object"]["profile"] = profile_func(obj, profile_args)
                results.append(result)

        # Sort
        if "-change" in sort_param:
            results.sort(key=lambda r: r["change"], reverse=True)
        elif "change" in sort_param:
            results.sort(key=lambda r: r["change"])
        else:
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
    """Search people by name fields using JSON text search."""
    return Person.objects.filter(
        Q(gramps_id__icontains=query)
        | Q(primary_name__first_name__icontains=query)
        | Q(primary_name__icontains=query)
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

