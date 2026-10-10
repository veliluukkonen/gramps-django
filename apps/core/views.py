"""
DRF ViewSets for Gramps primary objects.

Each ViewSet provides list, retrieve, create, update, destroy operations.
Objects are identified by handle (primary key).

Query parameters on lists: gramps_id, sort, rules, gql (ignored), dates
(events), filemissing (media), page/pagesize, keys/skipkeys/strip,
extend, profile, backlinks, formats (notes), locale (ignored).

Writes (POST/PUT/DELETE) go through apps.core.writes and return the
gramps-web-api style transaction list
``[{"type": "add"|"update"|"delete", "handle", "_class", "old", "new"}]``.
"""

import json
import logging

from django.db import IntegrityError
from rest_framework import status, viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.auth.permissions import (
    PERM_ADD_OBJ,
    PERM_DEL_OBJ,
    PERM_EDIT_OBJ,
    HasGrampsPermission,
)

from .backlinks import get_backlinks, populate_backlinks_for_object
from .extend import get_extended_attributes
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
from .profile import get_profile
from .serializers import (
    CitationSerializer,
    EventSerializer,
    FamilySerializer,
    MediaObjectSerializer,
    NoteSerializer,
    PersonSerializer,
    PlaceSerializer,
    RepositorySerializer,
    SourceSerializer,
    TagSerializer,
)
from .sorting import apply_sort
from .writes import (
    NotFound,
    TransactionBuilder,
    WriteError,
    normalize_class_name,
    run_transaction,
    sort_for_creation,
)

logger = logging.getLogger(__name__)


def error_response(message, status_code=status.HTTP_400_BAD_REQUEST):
    """Error body in the shape the frontend reads (``error.message``)."""
    return Response({"error": {"message": message}}, status=status_code)


def _is_json_request(request):
    content_type = request.content_type or ""
    return content_type.startswith("application/json") or content_type == ""


class WritePermissionMixin:
    """Reads are open (home network); writes need a role with the permission."""

    def get_permissions(self):
        if self.request.method in ("GET", "HEAD", "OPTIONS"):
            return [AllowAny()]
        return [IsAuthenticated(), HasGrampsPermission()]

    @property
    def required_permissions(self):
        method = self.request.method
        if method == "POST":
            return [PERM_ADD_OBJ]
        if method in ("PUT", "PATCH"):
            return [PERM_EDIT_OBJ]
        if method == "DELETE":
            return [PERM_DEL_OBJ]
        return []


class GrampsObjectViewSet(WritePermissionMixin, viewsets.ModelViewSet):
    """
    Base ViewSet for Gramps primary objects.

    Supports:
    - Standard CRUD via handle (PK), transaction-list responses on writes
    - Lookup by gramps_id query parameter
    - sort, rules, extend, profile, backlinks query parameters
    - ETag header based on change timestamp (If-Match on PUT)
    """

    lookup_field = "handle"
    lookup_value_regex = "[^/]+"
    gramps_class = None  # e.g. "Person"; set in subclasses

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _get_model_name(self):
        return self.queryset.model.__name__

    def _parse_csv_param(self, name):
        value = self.request.query_params.get(name, "")
        return set(value.split(",")) if value else set()

    def _augment_data(self, data, instance, request):
        """Add extend, profile, backlinks and formatted to serialized data."""
        extend_keys = self._parse_csv_param("extend")
        profile_args = self._parse_csv_param("profile")
        want_backlinks = request.query_params.get("backlinks") in ("1", "true", "True")
        formats = self._parse_csv_param("formats")

        if extend_keys:
            context = self.get_serializer_context()
            data["extended"] = get_extended_attributes(instance, extend_keys, context)

        if profile_args:
            data["profile"] = get_profile(instance, profile_args)

        if want_backlinks:
            data["backlinks"] = get_backlinks(instance.handle)

        if "html" in formats and isinstance(instance, Note):
            from .html import get_note_html

            link_format = None
            options = request.query_params.get("format_options")
            if options:
                try:
                    link_format = json.loads(options).get("link_format")
                except (TypeError, ValueError, AttributeError):
                    link_format = None
            data["formatted"] = {"html": get_note_html(instance, link_format)}

        return data

    def _compute_etag(self, instance):
        return f'"{instance.change}"'

    def _serialize_many(self, items, request):
        serializer = self.get_serializer(items, many=True)
        return [
            self._augment_data(dict(item_data), instance, request)
            for item_data, instance in zip(serializer.data, items)
        ]

    # ------------------------------------------------------------------
    # queryset
    # ------------------------------------------------------------------

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params

        gramps_id = params.get("gramps_id")
        if gramps_id:
            queryset = queryset.filter(gramps_id=gramps_id)

        rules = params.get("rules")
        if rules:
            from .filters import apply_rules

            try:
                rules_dict = json.loads(rules)
            except (TypeError, ValueError) as exc:
                raise WriteError(f"Invalid rules parameter: {exc}") from exc
            queryset = apply_rules(queryset, self.gramps_class, rules_dict)

        if params.get("filemissing") in ("1", "true", "True") and self.gramps_class == "Media":
            from apps.media.upload import file_exists

            missing = [m.handle for m in queryset if not file_exists(m)]
            queryset = queryset.filter(pk__in=missing)

        sort_param = params.get("sort")
        if sort_param:
            queryset = apply_sort(queryset, sort_param, self._get_model_name())

        return queryset

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        data = self._augment_data(dict(serializer.data), instance, request)
        response = Response(data)
        response["ETag"] = self._compute_etag(instance)
        return response

    def list(self, request, *args, **kwargs):
        try:
            queryset = self.filter_queryset(self.get_queryset())
        except (WriteError, ValueError) as exc:
            return error_response(str(exc))

        # gramps_id lookup returns a single-element list (404 if missing)
        gramps_id = request.query_params.get("gramps_id")
        if gramps_id:
            instance = queryset.first()
            if instance is None:
                return error_response(
                    f"Object with gramps_id '{gramps_id}' not found",
                    status.HTTP_404_NOT_FOUND,
                )
            serializer = self.get_serializer(instance)
            data = self._augment_data(dict(serializer.data), instance, request)
            response = Response([data])
            response["X-Total-Count"] = 1
            response["ETag"] = self._compute_etag(instance)
            return response

        dates = request.query_params.get("dates")
        if dates and self.gramps_class == "Event":
            from .dates import match_dates

            try:
                queryset = match_dates(queryset, dates)
            except ValueError as exc:
                return error_response(str(exc))

        page = self.paginate_queryset(queryset)
        items = page if page is not None else list(queryset)
        augmented = self._serialize_many(items, request)
        if page is not None:
            return self.get_paginated_response(augmented)
        return Response(augmented)

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------

    def _write(self, description, func, success_status=status.HTTP_200_OK):
        try:
            result = run_transaction(self.request.user, description, func)
        except NotFound as exc:
            return error_response(str(exc), status.HTTP_404_NOT_FOUND)
        except (WriteError, ValueError, TypeError, KeyError) as exc:
            return error_response(str(exc))
        except IntegrityError as exc:
            logger.warning("Integrity error on write: %s", exc)
            return error_response(f"Referenced object does not exist or duplicate value: {exc}")
        response = Response(result, status=success_status)
        response["X-Total-Count"] = len(result)
        return response

    def create(self, request, *args, **kwargs):
        if self.gramps_class == "Media" and not _is_json_request(request):
            return self._create_media_from_upload(request)

        data = request.data
        if not isinstance(data, dict):
            return error_response("Expected a JSON object")

        def func(builder):
            builder.add(data, self.gramps_class)

        return self._write(
            f"Add {self.gramps_class}", func, status.HTTP_201_CREATED
        )

    def _create_media_from_upload(self, request):
        from apps.media.upload import save_uploaded_file

        try:
            file_info = save_uploaded_file(request)
        except ValueError as exc:
            return error_response(str(exc))

        obj = {
            "_class": "Media",
            "path": file_info["path"],
            "mime": file_info["mime"],
            "checksum": file_info["checksum"],
            "desc": file_info.get("desc", ""),
        }

        def func(builder):
            builder.add(obj, "Media")

        return self._write("Upload media", func, status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        handle = kwargs.get(self.lookup_field)
        data = request.data
        if not isinstance(data, dict):
            return error_response("Expected a JSON object")

        # Optimistic concurrency: If-Match header against current ETag
        if_match = request.headers.get("If-Match")
        if if_match:
            instance = self.get_queryset().model.objects.filter(pk=handle).first()
            if instance is not None and if_match != self._compute_etag(instance):
                return error_response(
                    "Object was modified by another user",
                    status.HTTP_412_PRECONDITION_FAILED,
                )

        def func(builder):
            builder.update(data, self.gramps_class, handle)

        return self._write(f"Update {self.gramps_class}", func)

    def partial_update(self, request, *args, **kwargs):
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        handle = kwargs.get(self.lookup_field)

        def func(builder):
            builder.delete(self.gramps_class, handle)

        return self._write(f"Delete {self.gramps_class}", func)

    # Kept for callers that still expect them (e.g. management code)
    def perform_create(self, serializer):  # pragma: no cover - unused
        instance = serializer.save()
        populate_backlinks_for_object(instance, self.gramps_class)


class PersonViewSet(GrampsObjectViewSet):
    queryset = Person.objects.all()
    serializer_class = PersonSerializer
    gramps_class = "Person"


class FamilyViewSet(GrampsObjectViewSet):
    queryset = Family.objects.all()
    serializer_class = FamilySerializer
    gramps_class = "Family"


class EventViewSet(GrampsObjectViewSet):
    queryset = Event.objects.all()
    serializer_class = EventSerializer
    gramps_class = "Event"


class PlaceViewSet(GrampsObjectViewSet):
    queryset = Place.objects.all()
    serializer_class = PlaceSerializer
    gramps_class = "Place"


class SourceViewSet(GrampsObjectViewSet):
    queryset = Source.objects.all()
    serializer_class = SourceSerializer
    gramps_class = "Source"


class CitationViewSet(GrampsObjectViewSet):
    queryset = Citation.objects.all()
    serializer_class = CitationSerializer
    gramps_class = "Citation"


class RepositoryViewSet(GrampsObjectViewSet):
    queryset = Repository.objects.all()
    serializer_class = RepositorySerializer
    gramps_class = "Repository"


class MediaObjectViewSet(GrampsObjectViewSet):
    queryset = MediaObject.objects.all()
    serializer_class = MediaObjectSerializer
    gramps_class = "Media"


class NoteViewSet(GrampsObjectViewSet):
    queryset = Note.objects.all()
    serializer_class = NoteSerializer
    gramps_class = "Note"


class TagViewSet(GrampsObjectViewSet):
    queryset = Tag.objects.all()
    serializer_class = TagSerializer
    gramps_class = "Tag"


class CreateObjectsView(WritePermissionMixin, APIView):
    """
    POST /api/objects/ — create several objects in one transaction.

    Body: a JSON list of objects, each with ``_class``. Handles may be
    pre-assigned by the client (uuid) so objects can reference each other.
    Returns the transaction list with status 201.
    """

    def post(self, request):
        data = request.data
        if not isinstance(data, list) or not data:
            return error_response("Expected a non-empty JSON list of objects")
        for item in data:
            if not isinstance(item, dict) or not item.get("_class"):
                return error_response("Every object needs a _class")

        def func(builder):
            for obj in sort_for_creation(data):
                builder.add(obj, normalize_class_name(obj.get("_class")))

        try:
            result = run_transaction(request.user, "Add multiple objects", func)
        except (WriteError, ValueError, TypeError, KeyError) as exc:
            return error_response(str(exc))
        except IntegrityError as exc:
            return error_response(f"Referenced object does not exist or duplicate value: {exc}")
        response = Response(result, status=status.HTTP_201_CREATED)
        response["X-Total-Count"] = len(result)
        return response


__all__ = [
    "GrampsObjectViewSet", "PersonViewSet", "FamilyViewSet", "EventViewSet",
    "PlaceViewSet", "SourceViewSet", "CitationViewSet", "RepositoryViewSet",
    "MediaObjectViewSet", "NoteViewSet", "TagViewSet", "CreateObjectsView",
    "TransactionBuilder",
]
