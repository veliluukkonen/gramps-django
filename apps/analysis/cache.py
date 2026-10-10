"""
Per-request object cache for the analysis algorithms.

The timeline and relationship code walk the tree repeatedly; a small
handle-keyed cache keeps the number of database round trips reasonable
and gives the algorithms a Gramps-like ``get_x_from_handle`` interface.
"""

from apps.core.models import Citation, Event, Family, Note, Person, Place


def ref_handle(ref):
    """Handle of a reference dict (EventRef, ChildRef, PersonRef, ...)."""
    if isinstance(ref, dict):
        return ref.get("ref") or ""
    if isinstance(ref, str):
        return ref
    return ""


def type_string(value, default=""):
    """Gramps types are stored either as strings or as ``{"string": ...}``."""
    if value is None:
        return default
    if isinstance(value, dict):
        return value.get("string", default) or default
    return str(value)


def ref_role(ref):
    """Role of an EventRef (defaults to Primary)."""
    if not isinstance(ref, dict):
        return "Primary"
    return type_string(ref.get("role"), "Primary") or "Primary"


class TreeCache:
    """Lazy, handle-keyed cache of Gramps primary objects."""

    def __init__(self):
        self._people = {}
        self._families = {}
        self._events = {}
        self._places = {}
        self._notes = {}
        self._citations = {}

    def _get(self, store, model, handle):
        if not handle:
            return None
        if handle in store:
            return store[handle]
        obj = model.objects.filter(pk=handle).first()
        store[handle] = obj
        return obj

    def get_person(self, handle):
        return self._get(self._people, Person, handle)

    def get_family(self, handle):
        return self._get(self._families, Family, handle)

    def get_event(self, handle):
        return self._get(self._events, Event, handle)

    def get_place(self, handle):
        return self._get(self._places, Place, handle)

    def get_note(self, handle):
        return self._get(self._notes, Note, handle)

    def get_citation(self, handle):
        return self._get(self._citations, Citation, handle)

    def preload_people(self, handles):
        """Bulk load a set of people into the cache."""
        missing = [h for h in handles if h and h not in self._people]
        if missing:
            found = Person.objects.in_bulk(missing)
            for handle in missing:
                self._people[handle] = found.get(handle)

    def preload_families(self, handles):
        """Bulk load a set of families into the cache."""
        missing = [h for h in handles if h and h not in self._families]
        if missing:
            found = Family.objects.in_bulk(missing)
            for handle in missing:
                self._families[handle] = found.get(handle)

    def preload_events(self, handles):
        """Bulk load a set of events into the cache."""
        missing = [h for h in handles if h and h not in self._events]
        if missing:
            found = Event.objects.in_bulk(missing)
            for handle in missing:
                self._events[handle] = found.get(handle)

    def get_person_events(self, person):
        """Yield (event_ref, event) pairs for a person, preloading events."""
        refs = [r for r in (person.event_ref_list or []) if ref_handle(r)]
        self.preload_events([ref_handle(r) for r in refs])
        for ref in refs:
            event = self.get_event(ref_handle(ref))
            if event is not None:
                yield ref, event

    def get_family_events(self, family):
        """Yield (event_ref, event) pairs for a family, preloading events."""
        refs = [r for r in (family.event_ref_list or []) if ref_handle(r)]
        self.preload_events([ref_handle(r) for r in refs])
        for ref in refs:
            event = self.get_event(ref_handle(ref))
            if event is not None:
                yield ref, event
