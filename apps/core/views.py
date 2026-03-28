"""
DRF ViewSets for Gramps primary objects.

Each ViewSet provides list, retrieve, create, update, destroy operations.
Objects are identified by handle (primary key).
Also supports lookup by gramps_id via query parameter.
"""

from rest_framework import status, viewsets
from rest_framework.response import Response

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


class GrampsObjectViewSet(viewsets.ModelViewSet):
    """
    Base ViewSet for Gramps primary objects.

    Supports:
    - Standard CRUD via handle (PK)
    - Lookup by gramps_id query parameter
    - ETag header based on change timestamp
    """

    lookup_field = "handle"

    def get_queryset(self):
        queryset = super().get_queryset()
        gramps_id = self.request.query_params.get("gramps_id")
        if gramps_id:
            queryset = queryset.filter(gramps_id=gramps_id)
        return queryset

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        response = Response(serializer.data)
        response["ETag"] = self._compute_etag(instance)
        return response

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())

        # If gramps_id query param is used, return single object (not list)
        gramps_id = request.query_params.get("gramps_id")
        if gramps_id:
            instance = queryset.first()
            if instance is None:
                return Response(
                    {"error": f"Object with gramps_id '{gramps_id}' not found"},
                    status=status.HTTP_404_NOT_FOUND,
                )
            serializer = self.get_serializer(instance)
            # Frontend expects a list even for single gramps_id lookup
            response = Response([serializer.data])
            response["X-Total-Count"] = 1
            response["ETag"] = self._compute_etag(instance)
            return response

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

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
    lookup_field = "handle"
