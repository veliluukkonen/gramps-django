"""
Gramps filter rules for the list endpoints.

The frontend sends ``?rules=<JSON>`` with the structure used by
gramps-web-api::

    {"rules": [{"name": "HasTag", "values": ["Blog"], "regex": false}, ...],
     "function": "and" | "or" | "one",
     "invert": false}

``apply_rules(queryset, class_name, rules)`` evaluates the rules against
the queryset and returns a queryset restricted to the matching objects, so
the caller can still sort and paginate it.  Rule semantics follow the
Gramps rule classes in ``gramps.gen.filters.rules``.

Rules that map to simple column lookups are expressed as ``Q`` objects;
everything else is evaluated in Python on the model instances (the data
sets are small).
"""

import json
import re
from collections import deque

from django.db.models import Q

from .dates import dates_match, is_empty_date, is_valid_date, parse_date
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

CLASS_MODELS = {
    "Person": Person,
    "Family": Family,
    "Event": Event,
    "Place": Place,
    "Source": Source,
    "Citation": Citation,
    "Repository": Repository,
    "Media": MediaObject,
    "MediaObject": MediaObject,
    "Note": Note,
    "Tag": Tag,
}

COUNT_OPS = {
    "less than": lambda count, number: count < number,
    "greater than": lambda count, number: count > number,
    "equal to": lambda count, number: count == number,
}

FUNCTIONS = ("and", "or", "one")


# ---------------------------------------------------------------------------
# Small helpers for the JSON structures
# ---------------------------------------------------------------------------


def type_str(value):
    """Return a Gramps type as a plain string.

    Types are stored either as strings ("Birth") or as GrampsType dicts
    (``{"_class": "EventType", "string": "Birth", "value": 12}``).
    """
    if value is None:
        return ""
    if isinstance(value, dict):
        return str(value.get("string") or "")
    return str(value)


def as_list(value):
    return value if isinstance(value, list) else []


def as_dict(value):
    return value if isinstance(value, dict) else {}


def ref_handle(ref):
    """Handle of a reference entry (dict with ``ref`` or a bare handle)."""
    if isinstance(ref, dict):
        return ref.get("ref") or ""
    return ref or ""


def role_is_primary(ref):
    role = type_str(as_dict(ref).get("role", "Primary"))
    return role in ("Primary", "")


def nick_name(person):
    """Port of ``Person.get_nick_name``."""
    primary = as_dict(person.primary_name)
    if primary.get("nick"):
        return primary["nick"]
    for name in as_list(person.alternate_names):
        if as_dict(name).get("nick"):
            return name["nick"]
    for attr in as_list(person.attribute_list):
        if type_str(as_dict(attr).get("type")) == "Nickname" and as_dict(attr).get("value"):
            return attr["value"]
    return ""


def name_surname(name):
    """Primary surname of a Name dict (port of ``Name.get_surname``)."""
    surnames = as_list(as_dict(name).get("surname_list"))
    for surname in surnames:
        if as_dict(surname).get("primary"):
            return str(as_dict(surname).get("surname") or "")
    if surnames:
        return str(as_dict(surnames[0]).get("surname") or "")
    return ""


# ---------------------------------------------------------------------------
# Database access with per-request caching
# ---------------------------------------------------------------------------


class _Db:
    """Lazy, cached access to related objects by handle."""

    def __init__(self):
        self._people = {}
        self._families = {}
        self._events = {}
        self._places = {}
        self._all_loaded = False
        self._tags = None

    def _get(self, cache, model, handle):
        if not handle:
            return None
        if handle not in cache:
            cache[handle] = model.objects.filter(pk=handle).first()
        return cache[handle]

    def person(self, handle):
        return self._get(self._people, Person, handle)

    def family(self, handle):
        return self._get(self._families, Family, handle)

    def event(self, handle):
        return self._get(self._events, Event, handle)

    def place(self, handle):
        return self._get(self._places, Place, handle)

    def load_people_and_families(self):
        """Preload everything needed for graph walks (ancestors etc.)."""
        if not self._all_loaded:
            for person in Person.objects.all():
                self._people[person.handle] = person
            for family in Family.objects.all():
                self._families[family.handle] = family
            self._all_loaded = True

    def person_by_gramps_id(self, gramps_id):
        if not gramps_id:
            return None
        return Person.objects.filter(gramps_id=gramps_id).first()

    def tag_handles(self, name, regex):
        """Handles of the tags whose name matches ``name``."""
        if self._tags is None:
            self._tags = list(Tag.objects.values_list("handle", "name"))
        if regex:
            pattern = compile_regex(name)
            return {handle for handle, tag_name in self._tags if pattern.search(tag_name)}
        return {handle for handle, tag_name in self._tags if tag_name == name}

    def place_title(self, handle):
        """Display title of a place: its name followed by the enclosing places."""
        place = self.place(handle)
        if place is None:
            return ""
        names = []
        seen = set()
        while place is not None and place.handle not in seen:
            seen.add(place.handle)
            name = as_dict(place.name).get("value") or ""
            if not name and not names:
                name = place.title or ""
            if name:
                names.append(str(name))
            parent_handle = ""
            for pref in as_list(place.placeref_list):
                parent_handle = ref_handle(pref)
                if parent_handle:
                    break
            place = self.place(parent_handle) if parent_handle else None
        if not names:
            return ""
        return ", ".join(names)


_MATCH_ALL = re.compile("")


def compile_regex(pattern):
    """Compile a user supplied regular expression (case insensitive).

    A bare ``*`` (sent by the frontend to mean "anything") matches
    everything, like the Gramps fallback for invalid patterns.
    """
    if pattern == "*":
        return _MATCH_ALL
    try:
        return re.compile(pattern, re.I)
    except re.error as exc:
        raise ValueError(f"Invalid regular expression '{pattern}': {exc}") from exc


# ---------------------------------------------------------------------------
# Rule base classes
# ---------------------------------------------------------------------------


class Rule:
    """Base class: ``values`` padded to ``nvalues``, optional ``q``."""

    nvalues = 0
    defaults = None  # used when values are missing entirely
    q = None  # a django Q object when the rule can run in the database

    def __init__(self, name, values, regex, db):
        self.name = name
        self.db = db
        self.regex = bool(regex)
        values = list(values or [])
        if not values and self.defaults is not None:
            values = list(self.defaults)
        values = ["" if value is None else value for value in values]
        values += [""] * (self.nvalues - len(values))
        self.values = values
        self._patterns = {}
        if self.regex:
            for index, value in enumerate(self.values[: self.nvalues]):
                if isinstance(value, str) and value:
                    self._patterns[index] = compile_regex(value)
        self.prepare()

    def prepare(self):
        """Hook for one-time preparation (like ``Rule.prepare`` in Gramps)."""

    def value(self, index):
        return self.values[index] if index < len(self.values) else ""

    def str_value(self, index):
        return str(self.value(index) or "")

    def int_value(self, index, label="value"):
        raw = self.value(index)
        try:
            return int(raw)
        except (TypeError, ValueError):
            raise ValueError(
                f"Rule {self.name}: {label} must be an integer, got {raw!r}"
            ) from None

    def match_text(self, index, text):
        """Port of ``Rule.match_substring`` / ``Rule.match_regex``."""
        value = self.str_value(index)
        if not value:
            return True
        if value == "*":
            return True
        text = "" if text is None else str(text)
        if self.regex:
            return self._patterns[index].search(text) is not None
        return text.upper().find(value.upper()) != -1

    def match_type(self, index, type_value):
        """Compare a Gramps type; exact match, or regex when requested."""
        value = self.str_value(index)
        if not value:
            return True
        if self.regex:
            return self._patterns[index].search(type_str(type_value)) is not None
        return type_str(type_value) == value

    def match(self, obj):
        raise NotImplementedError


class CountRule(Rule):
    """``[count, "less than"|"greater than"|"equal to"]`` on a list field."""

    nvalues = 2
    defaults = ("0", "greater than")
    list_attr = None

    def prepare(self):
        self.number = self.int_value(0, "count")
        op = self.str_value(1) or "equal to"
        if op not in COUNT_OPS:
            raise ValueError(
                f"Rule {self.name}: unknown comparison '{op}' "
                "(use 'less than', 'greater than' or 'equal to')"
            )
        self.compare = COUNT_OPS[op]

    def count(self, obj):
        return len(as_list(getattr(obj, self.list_attr, None)))

    def match(self, obj):
        return self.compare(self.count(obj), self.number)


def count_rule(list_attr):
    return type(f"Count_{list_attr}", (CountRule,), {"list_attr": list_attr})


class QRule(Rule):
    """Rule fully expressed as a ``Q``; ``match`` evaluates it in Python."""

    def match(self, obj):
        return type(obj).objects.filter(self.q, pk=obj.pk).exists()


def q_rule(**lookups):
    return type("QRule", (QRule,), {"q": Q(**lookups)})


class HasIdOf(Rule):
    nvalues = 1

    def prepare(self):
        gramps_id = self.str_value(0)
        if self.regex and gramps_id:
            compile_regex(gramps_id)
            self.q = Q(gramps_id__iregex=gramps_id) if gramps_id != "*" else Q()
        else:
            self.q = Q(gramps_id=gramps_id)

    def match(self, obj):
        gramps_id = obj.gramps_id or ""
        if self.regex:
            return self.match_text(0, gramps_id)
        return gramps_id == self.str_value(0)


class HasTag(Rule):
    nvalues = 1

    def prepare(self):
        self.tag_handles = self.db.tag_handles(self.str_value(0), self.regex)

    def match(self, obj):
        if not self.tag_handles:
            return False
        return any(handle in self.tag_handles for handle in as_list(obj.tag_list))


class HasAttribute(Rule):
    """``[attribute type, value]``; value "*" or "" matches any value."""

    nvalues = 2

    def match(self, obj):
        attr_type = self.str_value(0)
        if not attr_type:
            return False
        for attr in as_list(obj.attribute_list):
            attr = as_dict(attr)
            if type_str(attr.get("type")) == attr_type and self.match_text(1, attr.get("value")):
                return True
        return False


class IsPrivate(Rule):
    q = Q(private=True)

    def match(self, obj):
        return bool(obj.private)


class IsPublic(Rule):
    q = Q(private=False)

    def match(self, obj):
        return not obj.private


class EventDataMixin:
    """Shared matching of ``[date, place, description]`` against an event."""

    def prepare_event_data(self, date_index):
        date_text = self.str_value(date_index)
        self.date = parse_date(date_text) if date_text else None

    def event_matches(self, event, date_index, place_index, desc_index):
        if event is None:
            return False
        if not self.match_text(desc_index, event.description):
            return False
        if self.date is not None and not dates_match(event.date, self.date):
            return False
        if self.str_value(place_index):
            if not event.place_id:
                return False
            if not self.match_text(place_index, self.db.place_title(event.place_id)):
                return False
        return True


# ---------------------------------------------------------------------------
# Person rules
# ---------------------------------------------------------------------------


class HasAlternateName(Rule):
    def match(self, person):
        return bool(as_list(person.alternate_names))


class HasNickname(Rule):
    def match(self, person):
        return bool(nick_name(person))


class HaveAltFamilies(Rule):
    def match(self, person):
        for family_handle in as_list(person.parent_family_list):
            family = self.db.family(family_handle)
            if family is None:
                continue
            for child_ref in as_list(family.child_ref_list):
                child_ref = as_dict(child_ref)
                if child_ref.get("ref") == person.handle:
                    if "Adopted" in (type_str(child_ref.get("frel")), type_str(child_ref.get("mrel"))):
                        return True
                    break
        return False


class HaveChildren(Rule):
    def match(self, person):
        for family_handle in as_list(person.family_list):
            family = self.db.family(family_handle)
            if family is not None and as_list(family.child_ref_list):
                return True
        return False


class IncompleteNames(Rule):
    def match(self, person):
        for name in [as_dict(person.primary_name)] + [as_dict(n) for n in as_list(person.alternate_names)]:
            if str(name.get("first_name") or "").strip() == "":
                return True
            surnames = as_list(name.get("surname_list"))
            if not surnames:
                return True
            for surname in surnames:
                if str(as_dict(surname).get("surname") or "").strip() == "":
                    return True
        return False


class NeverMarried(Rule):
    def match(self, person):
        return len(as_list(person.family_list)) == 0


class MultipleMarriages(Rule):
    def match(self, person):
        return len(as_list(person.family_list)) > 1


class _NoEventDate(Rule):
    index_attr = None

    def match(self, person):
        refs = as_list(person.event_ref_list)
        index = getattr(person, self.index_attr)
        if 0 <= index < len(refs):
            ref = refs[index]
            if not ref:
                return True
            event = self.db.event(ref_handle(ref))
            if event is None:
                return True
            return not is_valid_date(event.date)
        return True


class NoBirthdate(_NoEventDate):
    index_attr = "birth_ref_index"


class NoDeathdate(_NoEventDate):
    index_attr = "death_ref_index"


def _event_incomplete(event):
    return event is not None and (not event.place_id or is_empty_date(event.date))


class PersonWithIncompleteEvent(Rule):
    def match(self, person):
        for ref in as_list(person.event_ref_list):
            if _event_incomplete(self.db.event(ref_handle(ref))):
                return True
        return False


class FamilyWithIncompleteEvent(Rule):
    def match(self, person):
        for family_handle in as_list(person.family_list):
            family = self.db.family(family_handle)
            if family is None:
                continue
            for ref in as_list(family.event_ref_list):
                if _event_incomplete(self.db.event(ref_handle(ref))):
                    return True
        return False


class MissingParent(Rule):
    def match(self, person):
        families = as_list(person.parent_family_list)
        if not families:
            return True
        for family_handle in families:
            family = self.db.family(family_handle)
            if family is not None and (not family.father_handle_id or not family.mother_handle_id):
                return True
        return False


class Disconnected(Rule):
    def match(self, person):
        return not (as_list(person.parent_family_list) or as_list(person.family_list))


class _HasEventOfType(EventDataMixin, Rule):
    """HasBirth / HasDeath: ``[date, place, description]``."""

    nvalues = 3
    event_type = None

    def prepare(self):
        self.prepare_event_data(0)

    def match(self, person):
        for ref in as_list(person.event_ref_list):
            if not ref or not role_is_primary(ref):
                continue
            event = self.db.event(ref_handle(ref))
            if event is None or type_str(event.type) != self.event_type:
                continue
            if self.event_matches(event, 0, 1, 2):
                return True
        return False


class HasBirth(_HasEventOfType):
    event_type = "Birth"


class HasDeath(_HasEventOfType):
    event_type = "Death"


class PersonHasEvent(EventDataMixin, Rule):
    """``[event type, date, place, description, main participants]``."""

    nvalues = 5

    def prepare(self):
        self.prepare_event_data(1)

    def match(self, person):
        for ref in as_list(person.event_ref_list):
            if not ref:
                continue
            if self.str_value(4) and not role_is_primary(ref):
                continue
            event = self.db.event(ref_handle(ref))
            if event is None or not self.match_type(0, event.type):
                continue
            if self.event_matches(event, 1, 2, 3):
                return True
        return False


class HasAssociationType(Rule):
    nvalues = 1

    def match(self, person):
        wanted = self.str_value(0)
        return any(type_str(as_dict(ref).get("rel")) == wanted for ref in as_list(person.person_ref_list))


class HasRelationship(Rule):
    """``[number of relationships, relationship type, number of children]``."""

    nvalues = 3

    def match(self, person):
        families = as_list(person.family_list)
        total_children = 0
        type_found = False
        wanted_type = self.str_value(1)
        for family_handle in families:
            family = self.db.family(family_handle)
            if family is None:
                continue
            total_children += len(as_list(family.child_ref_list))
            if wanted_type and type_str(family.type) == wanted_type:
                type_found = True
        if self.str_value(0) and self.int_value(0, "number of relationships") != len(families):
            return False
        if self.str_value(2) and self.int_value(2, "number of children") != total_children:
            return False
        if wanted_type:
            return type_found
        return True


class HasNameOf(Rule):
    """Eleven values: given, full family name, title, suffix, call, nick,
    prefix, single surname, connector, patronymic, family nick."""

    nvalues = 11

    def match(self, person):
        names = [as_dict(person.primary_name)] + [as_dict(n) for n in as_list(person.alternate_names)]
        return any(self._match_name(name) for name in names)

    def _match_name(self, name):
        checks = (
            (0, name.get("first_name")),
            (1, name_surname(name)),
            (2, name.get("title")),
            (3, name.get("suffix")),
            (4, name.get("call")),
            (5, name.get("nick")),
            (10, name.get("famnick")),
        )
        for index, text in checks:
            if self.str_value(index) and not self.match_text(index, text):
                return False
        surnames = [as_dict(s) for s in as_list(name.get("surname_list"))]
        if not surnames and any(self.str_value(i) for i in (6, 7, 8, 9)):
            return False
        if not surnames:
            return True
        return any(self._match_surname(surname) for surname in surnames)

    def _match_surname(self, surname):
        if self.str_value(6) and not self.match_text(6, surname.get("prefix")):
            return False
        if self.str_value(7) and not self.match_text(7, surname.get("surname")):
            return False
        if self.str_value(8) and not self.match_text(8, surname.get("connector")):
            return False
        if self.str_value(9):
            if type_str(surname.get("origintype")) != "Patronymic":
                return False
            if not self.match_text(9, surname.get("surname")):
                return False
        return True


class _GraphRule(Rule):
    """Base for rules that precompute a set of person handles."""

    def prepare(self):
        self.db.load_people_and_families()
        self.selected = set()
        self.build()

    def build(self):
        raise NotImplementedError

    def root(self, index):
        return self.db.person_by_gramps_id(self.str_value(index))

    def parents_of(self, person, first_family_only=False):
        """Handles of the parents of ``person`` (father, mother)."""
        result = []
        families = as_list(person.parent_family_list)
        if first_family_only:
            families = families[:1]
        for family_handle in families:
            family = self.db.family(family_handle)
            if family is None:
                continue
            if family.father_handle_id:
                result.append(family.father_handle_id)
            if family.mother_handle_id:
                result.append(family.mother_handle_id)
        return result

    def children_of(self, person):
        result = []
        for family_handle in as_list(person.family_list):
            family = self.db.family(family_handle)
            if family is None:
                continue
            for child_ref in as_list(family.child_ref_list):
                handle = ref_handle(child_ref)
                if handle:
                    result.append(handle)
        return result

    def spouses_of(self, person):
        result = []
        for family_handle in as_list(person.family_list):
            family = self.db.family(family_handle)
            if family is None:
                continue
            for handle in (family.father_handle_id, family.mother_handle_id):
                if handle and handle != person.handle:
                    result.append(handle)
        return result

    def match(self, person):
        return person.handle in self.selected


class IsLessThanNthGenerationAncestorOf(_GraphRule):
    """``[gramps_id, generations]``; the root person is generation 1."""

    nvalues = 2

    def build(self):
        root = self.root(0)
        if root is None:
            return
        max_gen = self.int_value(1, "number of generations")
        queue = deque([(root.handle, 1)])
        while queue:
            handle, gen = queue.popleft()
            if handle in self.selected:
                continue
            self.selected.add(handle)
            gen += 1
            if gen <= max_gen:
                person = self.db.person(handle)
                if person is not None:
                    for parent in self.parents_of(person, first_family_only=True):
                        queue.append((parent, gen))


class IsLessThanNthGenerationDescendantOf(_GraphRule):
    """``[gramps_id, generations]``; the root is excluded, children are 1."""

    nvalues = 2

    def build(self):
        root = self.root(0)
        if root is None:
            return
        max_gen = self.int_value(1, "number of generations")
        queue = deque([(root.handle, 0)])
        while queue:
            handle, gen = queue.popleft()
            if gen and handle in self.selected:
                continue
            if gen:
                self.selected.add(handle)
                if gen >= max_gen:
                    continue
            person = self.db.person(handle)
            if person is not None:
                for child in self.children_of(person):
                    queue.append((child, gen + 1))


class IsAncestorOf(_GraphRule):
    """``[gramps_id, inclusive]`` (inclusive defaults to 1)."""

    nvalues = 2

    def build(self):
        root = self.root(0)
        if root is None:
            return
        inclusive = self.str_value(1) == "" or self.int_value(1, "inclusive") != 0
        queue = deque([root.handle])
        first = True
        while queue:
            handle = queue.popleft()
            if handle in self.selected:
                continue
            if not first or inclusive:
                self.selected.add(handle)
            first = False
            person = self.db.person(handle)
            if person is not None:
                queue.extend(self.parents_of(person, first_family_only=True))
        if not inclusive:
            self.selected.discard(root.handle)


class IsDescendantOf(_GraphRule):
    """``[gramps_id, inclusive]`` (inclusive defaults to 1)."""

    nvalues = 2

    def build(self):
        root = self.root(0)
        if root is None:
            return
        inclusive = self.str_value(1) == "" or self.int_value(1, "inclusive") != 0
        queue = deque([root.handle])
        seen = set()
        while queue:
            handle = queue.popleft()
            if handle in seen:
                continue
            seen.add(handle)
            self.selected.add(handle)
            person = self.db.person(handle)
            if person is not None:
                queue.extend(self.children_of(person))
        if not inclusive:
            self.selected.discard(root.handle)


class DegreesOfSeparation(_GraphRule):
    """``[gramps_id, degrees]``: people within N parent/child/spouse links."""

    nvalues = 2

    def build(self):
        root = self.root(0)
        if root is None:
            return
        max_degree = self.int_value(1, "degrees")
        queue = deque([(root.handle, 0)])
        while queue:
            handle, degree = queue.popleft()
            if handle in self.selected:
                continue
            self.selected.add(handle)
            if degree >= max_degree:
                continue
            person = self.db.person(handle)
            if person is None:
                continue
            for neighbour in self.parents_of(person) + self.spouses_of(person) + self.children_of(person):
                if neighbour not in self.selected:
                    queue.append((neighbour, degree + 1))


class RelationshipPathBetween(_GraphRule):
    """``[gramps_id, gramps_id]``: port of the Gramps rule.

    Walks the ancestors of both people, finds the nearest common
    ancestors and selects everyone on the descent lines to the two people.
    """

    nvalues = 2

    def _ancestors(self, root_handle):
        """Return ``{handle: rank}`` of the ancestors (first parent family)."""
        ranks = {}
        queue = deque([(root_handle, 0)])
        while queue:
            handle, rank = queue.popleft()
            person = self.db.person(handle)
            if person is None or handle in ranks:
                continue
            ranks[handle] = rank
            for parent in self.parents_of(person, first_family_only=True):
                queue.append((parent, rank + 1))
        return ranks

    def _descendants(self, root_handle):
        """All descendants of ``root_handle`` (root excluded)."""
        result = set()
        queue = deque([root_handle])
        while queue:
            handle = queue.popleft()
            person = self.db.person(handle)
            if person is None:
                continue
            for child in self.children_of(person):
                if child not in result:
                    result.add(child)
                    queue.append(child)
        return result

    def build(self):
        root1 = self.root(0)
        root2 = self.root(1)
        if root1 is None or root2 is None:
            return
        first = self._ancestors(root1.handle)
        second = self._ancestors(root2.handle)
        common = []
        best = None
        for handle in set(first) & set(second):
            rank = first[handle]
            if best is None or rank < best:
                best = rank
                common = [handle]
            elif rank == best:
                common.append(handle)
        path1 = {root1.handle}
        path2 = {root2.handle}
        for handle in common:
            descendants = self._descendants(handle)
            path1.update(descendants & set(first))
            path2.update(descendants & set(second))
        self.selected.update(path1, path2, common)


# ---------------------------------------------------------------------------
# Family / Event / Place / Source / Citation / Repository / Media / Note rules
# ---------------------------------------------------------------------------


class HasRelType(Rule):
    nvalues = 1

    def prepare(self):
        value = self.str_value(0)
        if value and not self.regex:
            self.q = Q(type=value)

    def match(self, family):
        if not self.str_value(0):
            return True
        return self.match_type(0, family.type)


class FamilyHasEvent(EventDataMixin, Rule):
    """``[event type, date, place, description, main participants]``."""

    nvalues = 5

    def prepare(self):
        self.prepare_event_data(1)

    def match(self, family):
        for ref in as_list(family.event_ref_list):
            if not ref:
                continue
            event = self.db.event(ref_handle(ref))
            if event is None or not self.match_type(0, event.type):
                continue
            if self.event_matches(event, 1, 2, 3):
                return True
        return False


class HasChildIdOf(Rule):
    nvalues = 1

    def match(self, family):
        wanted = self.str_value(0)
        for child_ref in as_list(family.child_ref_list):
            child = self.db.person(ref_handle(child_ref))
            if child is None:
                continue
            if self.regex:
                if self.match_text(0, child.gramps_id):
                    return True
            elif (child.gramps_id or "") == wanted:
                return True
        return False


class HasTypeRule(Rule):
    """``[type]`` compared with the ``type`` column (Event, Note, ...)."""

    nvalues = 1

    def prepare(self):
        value = self.str_value(0)
        if value and not self.regex:
            self.q = Q(type=value)

    def match(self, obj):
        if not self.str_value(0):
            return False
        return self.match_type(0, obj.type)


class EventHasData(EventDataMixin, Rule):
    """``[event type, date, place, description]``."""

    nvalues = 4

    def prepare(self):
        self.prepare_event_data(1)

    def match(self, event):
        if self.str_value(0) and not self.match_type(0, event.type):
            return False
        return self.event_matches(event, 1, 2, 3)


class PlaceHasData(Rule):
    """``[name, place type, code]``."""

    nvalues = 3

    def match(self, place):
        if self.str_value(0):
            names = [as_dict(place.name)] + [as_dict(n) for n in as_list(place.alt_names)]
            if not any(self.match_text(0, name.get("value")) for name in names):
                return False
        if self.str_value(1) and type_str(place.place_type) != self.str_value(1):
            return False
        return self.match_text(2, place.code)


class PlaceHasTitle(Rule):
    nvalues = 1

    def match(self, place):
        return self.match_text(0, self.db.place_title(place.handle))


class HasNoLatOrLon(Rule):
    def match(self, place):
        return not (place.lat and place.long)


class HasSourceData(Rule):
    """``[title, author, abbreviation, publication]``."""

    nvalues = 4

    def match(self, source):
        return (
            self.match_text(0, source.title)
            and self.match_text(1, source.author)
            and self.match_text(2, source.abbrev)
            and self.match_text(3, source.pubinfo)
        )


class HasCitation(Rule):
    """``[volume/page, date, confidence]``."""

    nvalues = 3

    def prepare(self):
        date_text = self.str_value(1)
        self.date = parse_date(date_text) if date_text else None

    def match(self, citation):
        if not self.match_text(0, citation.page):
            return False
        if self.date is not None and not dates_match(citation.date, self.date):
            return False
        if self.str_value(2):
            return citation.confidence == self.int_value(2, "confidence")
        return True


class HasSourceIdOf(Rule):
    nvalues = 1

    def match(self, citation):
        source = citation.source_handle
        if source is None:
            return False
        if self.regex:
            return self.match_text(0, source.gramps_id)
        return (source.gramps_id or "") == self.str_value(0)


class HasRepo(Rule):
    """``[name, type, address, url]``."""

    nvalues = 4

    def match(self, repo):
        if not self.match_text(0, repo.name):
            return False
        if self.str_value(1) and not self.match_type(1, repo.type):
            return False
        if self.str_value(2):
            found = False
            for addr in as_list(repo.address_list):
                addr = as_dict(addr)
                text = ", ".join(
                    str(addr.get(key) or "")
                    for key in ("street", "locality", "city", "county", "state", "country", "postal", "phone")
                )
                if self.match_text(2, text):
                    found = True
                    break
            if not found:
                return False
        if self.str_value(3):
            found = False
            for url in as_list(repo.urls):
                url = as_dict(url)
                text = ", ".join(str(url.get(key) or "") for key in ("path", "desc"))
                if self.match_text(3, text):
                    found = True
                    break
            if not found:
                return False
        return True


class HasMedia(Rule):
    """``[title, mime type, path, date]``."""

    nvalues = 4

    def prepare(self):
        date_text = self.str_value(3)
        self.date = parse_date(date_text) if date_text else None

    def match(self, media):
        if not self.match_text(0, media.desc):
            return False
        if not self.match_text(1, media.mime):
            return False
        if not self.match_text(2, media.path):
            return False
        if self.date is not None and not dates_match(media.date, self.date):
            return False
        return True


def note_string(note):
    return str(as_dict(note.text).get("string") or "")


class NoteHasNote(Rule):
    """``[text, note type]``."""

    nvalues = 2

    def match(self, note):
        if not self.match_text(0, note_string(note)):
            return False
        if self.str_value(1) and not self.match_type(1, note.type):
            return False
        return True


class NoteMatchesSubstringOf(Rule):
    nvalues = 1

    def match(self, note):
        value = self.str_value(0)
        return value.upper() in note_string(note).upper() if value else True


class NoteMatchesRegexpOf(Rule):
    nvalues = 1

    def prepare(self):
        self.pattern = compile_regex(self.str_value(0))

    def match(self, note):
        return self.pattern.search(note_string(note)) is not None


class Everyone(Rule):
    q = Q()

    def match(self, obj):
        return True


# ---------------------------------------------------------------------------
# Rule registry
# ---------------------------------------------------------------------------

_COMMON = {
    "HasIdOf": HasIdOf,
    "RegExpIdOf": HasIdOf,
    "HasTag": HasTag,
    "HasNote": count_rule("note_list"),
}

HasGallery = count_rule("media_list")
HasSourceCount = count_rule("citation_list")

RULES = {
    "Person": {
        **_COMMON,
        "Everyone": Everyone,
        "IsFemale": q_rule(gender=Person.FEMALE),
        "IsMale": q_rule(gender=Person.MALE),
        "HasUnknownGender": q_rule(gender=Person.UNKNOWN),
        "HasOtherGender": q_rule(gender=Person.OTHER),
        "HasAlternateName": HasAlternateName,
        "HasNickname": HasNickname,
        "HaveAltFamilies": HaveAltFamilies,
        "HaveChildren": HaveChildren,
        "IncompleteNames": IncompleteNames,
        "NeverMarried": NeverMarried,
        "MultipleMarriages": MultipleMarriages,
        "NoBirthdate": NoBirthdate,
        "NoDeathdate": NoDeathdate,
        "PersonWithIncompleteEvent": PersonWithIncompleteEvent,
        "FamilyWithIncompleteEvent": FamilyWithIncompleteEvent,
        "PeoplePrivate": IsPrivate,
        "PeoplePublic": IsPublic,
        "MissingParent": MissingParent,
        "Disconnected": Disconnected,
        "HasBirth": HasBirth,
        "HasDeath": HasDeath,
        "HasEvent": PersonHasEvent,
        "HavePhotos": HasGallery,
        "HasGallery": HasGallery,
        "HasSourceCount": HasSourceCount,
        "HasAddress": count_rule("address_list"),
        "HasAssociation": count_rule("person_ref_list"),
        "HasAttribute": HasAttribute,
        "HasAssociationType": HasAssociationType,
        "HasRelationship": HasRelationship,
        "HasNameOf": HasNameOf,
        "IsLessThanNthGenerationAncestorOf": IsLessThanNthGenerationAncestorOf,
        "IsLessThanNthGenerationDescendantOf": IsLessThanNthGenerationDescendantOf,
        "IsAncestorOf": IsAncestorOf,
        "IsDescendantOf": IsDescendantOf,
        "DegreesOfSeparation": DegreesOfSeparation,
        "RelationshipPathBetween": RelationshipPathBetween,
    },
    "Family": {
        **_COMMON,
        "AllFamilies": Everyone,
        "HasRelType": HasRelType,
        "HasGallery": HasGallery,
        "HasSourceCount": HasSourceCount,
        "FamilyPrivate": IsPrivate,
        "HasEvent": FamilyHasEvent,
        "HasAttribute": HasAttribute,
        "ChildHasIdOf": HasChildIdOf,
    },
    "Event": {
        **_COMMON,
        "AllEvents": Everyone,
        "HasType": HasTypeRule,
        "HasData": EventHasData,
        "HasGallery": HasGallery,
        "HasSourceCount": HasSourceCount,
        "HasAttribute": HasAttribute,
        "EventPrivate": IsPrivate,
    },
    "Place": {
        **_COMMON,
        "AllPlaces": Everyone,
        "HasData": PlaceHasData,
        "HasTitle": PlaceHasTitle,
        "HasGallery": HasGallery,
        "HasSourceCount": HasSourceCount,
        "HasNoLatOrLon": HasNoLatOrLon,
        "PlacePrivate": IsPrivate,
    },
    "Source": {
        **_COMMON,
        "AllSources": Everyone,
        "HasGallery": HasGallery,
        "HasRepository": count_rule("reporef_list"),
        "HasSource": HasSourceData,
        "HasAttribute": HasAttribute,
        "SourcePrivate": IsPrivate,
    },
    "Citation": {
        **_COMMON,
        "AllCitations": Everyone,
        "HasGallery": HasGallery,
        "HasCitation": HasCitation,
        "HasSourceIdOf": HasSourceIdOf,
        "HasAttribute": HasAttribute,
        "CitationPrivate": IsPrivate,
    },
    "Repository": {
        **_COMMON,
        "AllRepos": Everyone,
        "HasRepo": HasRepo,
        "HasType": HasTypeRule,
        "RepoPrivate": IsPrivate,
    },
    "Media": {
        **_COMMON,
        "AllMedia": Everyone,
        "HasMedia": HasMedia,
        "HasAttribute": HasAttribute,
        "HasSourceCount": HasSourceCount,
        "MediaPrivate": IsPrivate,
    },
    "Note": {
        **_COMMON,
        "AllNotes": Everyone,
        "HasType": HasTypeRule,
        "HasNote": NoteHasNote,
        "MatchesSubstringOf": NoteMatchesSubstringOf,
        "MatchesRegexpOf": NoteMatchesRegexpOf,
        "NotePrivate": IsPrivate,
    },
    "Tag": {},
}
RULES["MediaObject"] = RULES["Media"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _parse_rules(rules):
    """Normalise the ``rules`` argument; raise ValueError when malformed."""
    if isinstance(rules, (str, bytes)):
        try:
            rules = json.loads(rules)
        except ValueError as exc:
            raise ValueError(f"Invalid rules JSON: {exc}") from exc
    if isinstance(rules, list):
        rules = {"rules": rules}
    if not isinstance(rules, dict):
        raise ValueError("Rules must be a JSON object")
    rule_list = rules.get("rules")
    if not isinstance(rule_list, list) or not rule_list:
        raise ValueError("Rules must contain a non-empty 'rules' list")
    function = rules.get("function") or "and"
    if function not in FUNCTIONS:
        raise ValueError(f"Unknown filter function '{function}' (use and, or, one)")
    invert = rules.get("invert", False)
    if isinstance(invert, str):
        invert = invert.lower() in ("1", "true", "yes")
    for index, rule in enumerate(rule_list):
        if not isinstance(rule, dict) or not rule.get("name"):
            raise ValueError(f"Rule #{index + 1} must be an object with a 'name'")
        if "values" in rule and rule["values"] is not None and not isinstance(rule["values"], list):
            raise ValueError(f"Rule {rule['name']}: 'values' must be a list")
    return rule_list, function, bool(invert)


def build_rules(class_name, rule_list, db=None):
    """Instantiate the rule objects for ``class_name``."""
    if class_name not in RULES:
        raise ValueError(f"Unknown object class '{class_name}'")
    db = db or _Db()
    registry = RULES[class_name]
    rules = []
    for rule in rule_list:
        name = str(rule["name"])
        rule_class = registry.get(name)
        if rule_class is None:
            raise ValueError(f"Unknown filter rule '{name}' for {class_name}")
        rules.append(rule_class(name, rule.get("values") or [], rule.get("regex", False), db))
    return rules


def apply_rules(queryset, class_name, rules):
    """Filter ``queryset`` by a Gramps rules definition.

    ``class_name`` is one of Person, Family, Event, Place, Source,
    Citation, Repository, Media, Note, Tag; ``rules`` is the parsed JSON
    dict (or the JSON string).  Returns a queryset.  Raises ``ValueError``
    for unknown rules, bad values or malformed input.
    """
    class_name = "Media" if class_name == "MediaObject" else class_name
    if class_name not in CLASS_MODELS:
        raise ValueError(f"Unknown object class '{class_name}'")
    model = CLASS_MODELS[class_name]
    if queryset is None:
        queryset = model.objects.all()

    rule_list, function, invert = _parse_rules(rules)
    compiled = build_rules(class_name, rule_list)

    # Fast path: everything is a Q and combined with "and".
    if function == "and" and all(rule.q is not None for rule in compiled):
        combined = Q()
        for rule in compiled:
            combined &= rule.q
        if invert:
            return queryset.exclude(pk__in=queryset.filter(combined).values("pk"))
        return queryset.filter(combined)

    objects = None
    matches = []
    for rule in compiled:
        if rule.q is not None:
            matches.append(set(queryset.filter(rule.q).values_list("pk", flat=True)))
        else:
            if objects is None:
                objects = list(queryset)
            matches.append({obj.pk for obj in objects if rule.match(obj)})

    if function == "and":
        selected = set.intersection(*matches)
    elif function == "or":
        selected = set.union(*matches)
    else:  # "one": exactly one rule matches
        counts = {}
        for handles in matches:
            for handle in handles:
                counts[handle] = counts.get(handle, 0) + 1
        selected = {handle for handle, count in counts.items() if count == 1}

    if invert:
        return queryset.exclude(pk__in=selected)
    return queryset.filter(pk__in=selected)
