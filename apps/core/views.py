"""
DRF ViewSets for Gramps primary objects.

Each ViewSet provides list, retrieve, create, update, destroy operations.
Objects are identified by handle (primary key).
Supports: gramps_id lookup, sort, extend, profile, backlinks, keys/skipkeys/strip.
"""

from rest_framework import status, viewsets
from rest_framework.response import Response

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


class GrampsObjectViewSet(viewsets.ModelViewSet):
    """
    Base ViewSet for Gramps primary objects.

    Supports:
    - Standard CRUD via handle (PK)
    - Lookup by gramps_id query parameter
    - sort, extend, profile, backlinks query parameters
    - ETag header based on change timestamp
    """

    lookup_field = "handle"

    def _get_model_name(self):
        return self.queryset.model.__name__

    def _parse_extend(self, request):
        """Parse extend query parameter into a set of keys."""
        extend = request.query_params.get("extend", "")
        if not extend:
            return set()
        return set(extend.split(","))

    def _parse_profile(self, request):
        """Parse profile query parameter into a set of keys."""
        profile = request.query_params.get("profile", "")
        if not profile:
            return set()
        return set(profile.split(","))

    def _augment_data(self, data, instance, request):
        """Add extend, profile, and backlinks to serialized data."""
        extend_keys = self._parse_extend(request)
        profile_args = self._parse_profile(request)
        want_backlinks = request.query_params.get("backlinks") in ("1", "true", "True")

        if extend_keys:
            context = self.get_serializer_context()
            data["extended"] = get_extended_attributes(instance, extend_keys, context)

        if profile_args:
            data["profile"] = get_profile(instance, profile_args)

        if want_backlinks:
            data["backlinks"] = get_backlinks(instance.handle)

        return data

    def get_queryset(self):
        queryset = super().get_queryset()

        # Filter by gramps_id
        gramps_id = self.request.query_params.get("gramps_id")
        if gramps_id:
            queryset = queryset.filter(gramps_id=gramps_id)

        # Apply sort
        sort_param = self.request.query_params.get("sort")
        if sort_param:
            queryset = apply_sort(queryset, sort_param, self._get_model_name())

        return queryset

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        data = self._augment_data(dict(serializer.data), instance, request)
        response = Response(data)
        response["ETag"] = self._compute_etag(instance)
        return response

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())

        # If gramps_id query param is used, return list with single object
        gramps_id = request.query_params.get("gramps_id")
        if gramps_id:
            instance = queryset.first()
            if instance is None:
                return Response(
                    {"error": f"Object with gramps_id '{gramps_id}' not found"},
                    status=status.HTTP_404_NOT_FOUND,
                )
            serializer = self.get_serializer(instance)
            data = self._augment_data(dict(serializer.data), instance, request)
            response = Response([data])
            response["X-Total-Count"] = 1
            response["ETag"] = self._compute_etag(instance)
            return response

        page = self.paginate_queryset(queryset)
        items = page if page is not None else list(queryset)

        serializer = self.get_serializer(items, many=True)

        # Augment each item
        augmented = []
        for item_data, instance in zip(serializer.data, items):
            augmented.append(self._augment_data(dict(item_data), instance, request))

        if page is not None:
            return self.get_paginated_response(augmented)
        return Response(augmented)

    def perform_create(self, serializer):
        instance = serializer.save()
        obj_type = self._get_model_name()
        if obj_type == "MediaObject":
            obj_type = "Media"
        populate_backlinks_for_object(instance, obj_type)

    def perform_update(self, serializer):
        instance = serializer.save()
        obj_type = self._get_model_name()
        if obj_type == "MediaObject":
            obj_type = "Media"
        populate_backlinks_for_object(instance, obj_type)

    def update(self, request, *args, **kwargs):
        instance = self.get_object()

        # Optimistic concurrency: check ETag
        if_match = request.headers.get("If-Match")
        if if_match and if_match != self._compute_etag(instance):
            return Response(
                {"error": "ETag mismatch — object was modified by another user"},
                status=status.HTTP_412_PRECONDITION_FAILED,
            )

        return super().update(request, *args, **kwargs)

    def _compute_etag(self, instance):
        return f'"{hash(instance.change)}"'


class PersonViewSet(GrampsObjectViewSet):
    queryset = Person.objects.all()
    serializer_class = PersonSerializer


class FamilyViewSet(GrampsObjectViewSet):
    queryset = Family.objects.all()
    serializer_class = FamilySerializer


class EventViewSet(GrampsObjectViewSet):
    queryset = Event.objects.all()
    serializer_class = EventSerializer


class PlaceViewSet(GrampsObjectViewSet):
    queryset = Place.objects.all()
    serializer_class = PlaceSerializer


class SourceViewSet(GrampsObjectViewSet):
    queryset = Source.objects.all()
    serializer_class = SourceSerializer


class CitationViewSet(GrampsObjectViewSet):
    queryset = Citation.objects.all()
    serializer_class = CitationSerializer


class RepositoryViewSet(GrampsObjectViewSet):
    queryset = Repository.objects.all()
    serializer_class = RepositorySerializer


class MediaObjectViewSet(GrampsObjectViewSet):
    queryset = MediaObject.objects.all()
    serializer_class = MediaObjectSerializer


class NoteViewSet(GrampsObjectViewSet):
    queryset = Note.objects.all()
    serializer_class = NoteSerializer


class TagViewSet(GrampsObjectViewSet):
    queryset = Tag.objects.all()
    serializer_class = TagSerializer
