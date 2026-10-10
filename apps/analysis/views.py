"""
Analysis API endpoints: timelines, relationships and DNA.

Read endpoints are open (home-network deployment), consistent with the
object views in apps.core.
"""

from django.conf import settings
from rest_framework import status
from rest_framework.parsers import BaseParser, FormParser, JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import Family, Person

from .cache import TreeCache
from .dna import get_dna_matches, get_ydna_data, parse_raw_dna_match_string
from .relations import get_calculator, get_one_relationship
from .similar import DEFAULT_LIMIT, MAX_LIMIT, find_similar_people
from .timeline import EVENT_CATEGORIES, RELATIVES, Timeline, TimelineError


def error_response(message, status_code=status.HTTP_400_BAD_REQUEST):
    """Error body in the shape the frontend reads (``error.message``)."""
    return Response({"error": {"message": message}}, status=status_code)


def _locale(request):
    locale = request.query_params.get("locale") or getattr(settings, "GRAMPS_LANGUAGE", "en")
    return (locale or "en")[:5]


def _bool(request, name, default):
    value = request.query_params.get(name)
    if value is None or value == "":
        return default
    return value.lower() in ("1", "true", "yes", "on")


def _int(request, name, default, minimum=None, maximum=None):
    value = request.query_params.get(name)
    if value is None or value == "":
        return default
    try:
        number = int(value)
    except ValueError:
        raise TimelineError(f"{name} must be an integer")
    if minimum is not None and number < minimum:
        raise TimelineError(f"{name} must be at least {minimum}")
    if maximum is not None and number > maximum:
        raise TimelineError(f"{name} must be at most {maximum}")
    return number


def _list(request, name):
    value = request.query_params.get(name)
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _apply_key_filters(request, items):
    """Support the keys / skipkeys / strip query parameters."""
    keys = _list(request, "keys")
    skipkeys = _list(request, "skipkeys")
    strip = _bool(request, "strip", False)
    if not keys and not skipkeys and not strip:
        return items
    result = []
    for item in items:
        if keys:
            item = {k: v for k, v in item.items() if k in keys}
        elif skipkeys:
            item = {k: v for k, v in item.items() if k not in skipkeys}
        if strip:
            item = {k: v for k, v in item.items() if v not in (None, "", [], {})}
        result.append(item)
    return result


def _timeline_response(request, timeline, page, pagesize):
    payload = timeline.profile(page=page, pagesize=pagesize)
    response = Response(_apply_key_filters(request, payload))
    response["X-Total-Count"] = len(timeline.timeline)
    return response


def _event_filters(request):
    events = _list(request, "events") + _list(request, "event_classes")
    for key in _list(request, "event_classes"):
        if key not in EVENT_CATEGORIES:
            raise TimelineError(f"{key} is not a valid event category")
    return events


class PersonTimelineView(APIView):
    """GET /api/people/<handle>/timeline"""

    permission_classes = [AllowAny]

    def get(self, request, handle):
        if not Person.objects.filter(pk=handle).exists():
            return error_response(f"Person {handle} not found", status.HTTP_404_NOT_FOUND)
        try:
            relatives = _list(request, "relatives")
            for relative in relatives:
                if relative not in RELATIVES:
                    raise TimelineError(f"{relative} is not a valid relative type")
            relative_events = _list(request, "relative_events") + _list(
                request, "relative_event_classes"
            )
            timeline = Timeline(
                TreeCache(),
                dates=request.query_params.get("dates") or None,
                events=_event_filters(request),
                ratings=_bool(request, "ratings", False),
                relatives=relatives,
                relative_events=relative_events,
                discard_empty=_bool(request, "discard_empty", True),
                omit_anchor=_bool(request, "omit_anchor", True),
                precision=_int(request, "precision", 1, 1, 3),
                locale=_locale(request),
            )
            timeline.add_person(
                handle,
                anchor=True,
                start=_bool(request, "first", True),
                end=_bool(request, "last", True),
                ancestors=_int(request, "ancestors", 1, 1, 5),
                offspring=_int(request, "offspring", 1, 1, 5),
            )
            page = _int(request, "page", 0, 0)
            pagesize = _int(request, "pagesize", 20, 1)
        except TimelineError as exc:
            return error_response(str(exc), status.HTTP_422_UNPROCESSABLE_ENTITY)
        except LookupError as exc:
            return error_response(f"Object {exc} not found", status.HTTP_404_NOT_FOUND)
        return _timeline_response(request, timeline, page, pagesize)


class FamilyTimelineView(APIView):
    """GET /api/families/<handle>/timeline"""

    permission_classes = [AllowAny]

    def get(self, request, handle):
        if not Family.objects.filter(pk=handle).exists():
            return error_response(f"Family {handle} not found", status.HTTP_404_NOT_FOUND)
        try:
            timeline = Timeline(
                TreeCache(),
                dates=request.query_params.get("dates") or None,
                events=_event_filters(request),
                ratings=_bool(request, "ratings", False),
                discard_empty=_bool(request, "discard_empty", True),
                locale=_locale(request),
            )
            timeline.add_family(handle)
            page = _int(request, "page", 0, 0)
            pagesize = _int(request, "pagesize", 20, 1)
        except TimelineError as exc:
            return error_response(str(exc), status.HTTP_422_UNPROCESSABLE_ENTITY)
        except LookupError as exc:
            return error_response(f"Object {exc} not found", status.HTTP_404_NOT_FOUND)
        return _timeline_response(request, timeline, page, pagesize)


class TimelinePeopleView(APIView):
    """GET /api/timelines/people/?handles=...&anchor=..."""

    permission_classes = [AllowAny]

    def get(self, request):
        try:
            timeline = Timeline(
                TreeCache(),
                dates=request.query_params.get("dates") or None,
                events=_event_filters(request),
                ratings=_bool(request, "ratings", False),
                discard_empty=_bool(request, "discard_empty", True),
                omit_anchor=_bool(request, "omit_anchor", True),
                precision=_int(request, "precision", 1, 1, 3),
                locale=_locale(request),
            )
            anchor = request.query_params.get("anchor")
            if anchor:
                timeline.add_person(
                    anchor,
                    anchor=True,
                    start=_bool(request, "first", True),
                    end=_bool(request, "last", True),
                )
                # Relatives are listed explicitly here, so search deeper than
                # the default one generation when labelling them.
                timeline.depth = max(timeline.depth, 5)
            handles = _list(request, "handles")
            if not handles and not request.query_params.get("handles"):
                handles = list(Person.objects.values_list("handle", flat=True))
            for handle in handles:
                if anchor:
                    timeline.add_relative(handle)
                else:
                    timeline.add_person(handle)
            page = _int(request, "page", 0, 0)
            pagesize = _int(request, "pagesize", 20, 1)
        except TimelineError as exc:
            return error_response(str(exc), status.HTTP_422_UNPROCESSABLE_ENTITY)
        except LookupError as exc:
            return error_response(f"Object {exc} not found", status.HTTP_404_NOT_FOUND)
        return _timeline_response(request, timeline, page, pagesize)


class TimelineFamiliesView(APIView):
    """GET /api/timelines/families/?handles=..."""

    permission_classes = [AllowAny]

    def get(self, request):
        try:
            timeline = Timeline(
                TreeCache(),
                dates=request.query_params.get("dates") or None,
                events=_event_filters(request),
                ratings=_bool(request, "ratings", False),
                discard_empty=_bool(request, "discard_empty", True),
                locale=_locale(request),
            )
            handles = _list(request, "handles")
            if not handles and not request.query_params.get("handles"):
                handles = list(Family.objects.values_list("handle", flat=True))
            for handle in handles:
                timeline.add_family(handle)
            page = _int(request, "page", 0, 0)
            pagesize = _int(request, "pagesize", 20, 1)
        except TimelineError as exc:
            return error_response(str(exc), status.HTTP_422_UNPROCESSABLE_ENTITY)
        except LookupError as exc:
            return error_response(f"Object {exc} not found", status.HTTP_404_NOT_FOUND)
        return _timeline_response(request, timeline, page, pagesize)


class RelationView(APIView):
    """GET /api/relations/<handle1>/<handle2>?depth=&locale="""

    permission_classes = [AllowAny]

    def get(self, request, handle1, handle2):
        db = TreeCache()
        person1 = db.get_person(handle1)
        if person1 is None:
            return error_response(f"Person {handle1} not found", status.HTTP_404_NOT_FOUND)
        person2 = db.get_person(handle2)
        if person2 is None:
            return error_response(f"Person {handle2} not found", status.HTTP_404_NOT_FOUND)
        try:
            depth = _int(request, "depth", 15, 2)
        except TimelineError as exc:
            return error_response(str(exc), status.HTTP_422_UNPROCESSABLE_ENTITY)
        rel_string, dist_orig, dist_other = get_one_relationship(
            db, person1, person2, depth=depth, lang=_locale(request)
        )
        return Response(
            {
                "relationship_string": rel_string,
                "distance_common_origin": dist_orig,
                "distance_common_other": dist_other,
                # aliases used in some frontend code paths
                "distance_orig": dist_orig,
                "distance_other": dist_other,
            }
        )


class RelationsView(APIView):
    """GET /api/relations/<handle1>/<handle2>/all?depth=&locale="""

    permission_classes = [AllowAny]

    def get(self, request, handle1, handle2):
        db = TreeCache()
        person1 = db.get_person(handle1)
        if person1 is None:
            return error_response(f"Person {handle1} not found", status.HTTP_404_NOT_FOUND)
        person2 = db.get_person(handle2)
        if person2 is None:
            return error_response(f"Person {handle2} not found", status.HTTP_404_NOT_FOUND)
        try:
            depth = _int(request, "depth", 15, 2)
        except TimelineError as exc:
            return error_response(str(exc), status.HTTP_422_UNPROCESSABLE_ENTITY)
        calc = get_calculator(db, _locale(request), depth)
        strings, ancestors = calc.get_all_relationships(person1, person2)
        result = [
            {"relationship_string": s, "common_ancestors": a}
            for s, a in zip(strings, ancestors)
        ]
        return Response(result or [{}])


class PersonDnaMatchesView(APIView):
    """GET /api/people/<handle>/dna/matches?locale=&raw="""

    permission_classes = [AllowAny]

    def get(self, request, handle):
        person = Person.objects.filter(pk=handle).first()
        if person is None:
            return error_response(f"Person {handle} not found", status.HTTP_404_NOT_FOUND)
        matches = get_dna_matches(
            person,
            lang=_locale(request),
            include_raw_data=_bool(request, "raw", False),
        )
        return Response(matches)


class PersonYDnaView(APIView):
    """GET /api/people/<handle>/ydna?raw="""

    permission_classes = [AllowAny]

    def get(self, request, handle):
        person = Person.objects.filter(pk=handle).first()
        if person is None:
            return error_response("Person not found", status.HTTP_404_NOT_FOUND)
        return Response(get_ydna_data(person, include_raw_data=_bool(request, "raw", False)))


class PlainTextParser(BaseParser):
    """Accept a raw text/plain body."""

    media_type = "text/plain"

    def parse(self, stream, media_type=None, parser_context=None):
        return stream.read().decode("utf-8", errors="replace")


class DnaMatchParserView(APIView):
    """
    POST /api/parsers/dna-match

    Body: JSON ``{"string": "<raw match table>"}`` or raw ``text/plain``.
    Returns the parsed list of segments.
    """

    permission_classes = [AllowAny]
    parser_classes = [JSONParser, PlainTextParser, FormParser]

    def post(self, request):
        data = request.data
        if isinstance(data, dict):
            raw = data.get("string")
        else:
            raw = data
        if raw is None:
            return error_response("Missing 'string' in request body", status.HTTP_422_UNPROCESSABLE_ENTITY)
        if not isinstance(raw, str):
            raw = str(raw)
        return Response(parse_raw_dna_match_string(raw))


class SimilarPeopleView(APIView):
    """
    GET /api/people/similar/?first_name=&surname=&birth_year=&gender=&limit=

    Possible duplicates for a person being added. Returns the candidate list
    only when the number of matches is at most ``limit`` (default 20); the
    total number of matches is always in the ``X-Total-Count`` header.
    """

    permission_classes = [AllowAny]

    def get(self, request):
        first_name = request.query_params.get("first_name", "")
        surname = request.query_params.get("surname", "")
        try:
            birth_year = _int(request, "birth_year", None)
            gender = _int(request, "gender", None)
            limit = _int(request, "limit", DEFAULT_LIMIT, minimum=1, maximum=MAX_LIMIT)
        except TimelineError as exc:
            return error_response(str(exc))
        if not first_name.strip() and not surname.strip():
            return error_response("first_name or surname is required")
        total, results = find_similar_people(
            first_name=first_name,
            surname=surname,
            birth_year=birth_year,
            gender=gender,
            limit=limit,
        )
        response = Response(results)
        response["X-Total-Count"] = total
        return response
