"""
Timeline computation (port of gramps_webapi/api/resources/timeline.py).

A timeline item is a tuple ``(event, person, relationship, role)``.  A
timeline may have an anchor person; relationships are then calculated with
respect to them.
"""

from apps.core.models import Event
from apps.core.profile import (
    _max_confidence,
    get_person_profile,
    get_place_profile,
)

from .cache import TreeCache, ref_handle, ref_role
from .dates import (
    add_years,
    display_date,
    get_sortval,
    is_after,
    is_before,
    parse_ymd_string,
    span_string,
)
from .relations import get_calculator

DEATH_INDICATORS = ["Death", "Burial", "Cremation", "Cause Of Death", "Probate"]
DEATH_INDICATOR_NAMES = [
    "Funeral", "Interment", "Reinterment", "Inurnment", "Memorial",
    "Visitation", "Wake", "Shiva",
]

BIRTH_FALLBACKS = ["Stillbirth", "Baptism", "Christening"]
DEATH_FALLBACKS = ["Stillbirth", "Burial", "Cremation", "Cause Of Death", "Probate"]
MARRIAGE_FALLBACKS = ["Engagement", "Alternate Marriage"]
DIVORCE_FALLBACKS = ["Annulment", "Divorce Filing"]
PRIMARY_EVENT_ROLES = ["Primary", "Family"]

RELATIVES = ["father", "mother", "brother", "sister", "wife", "husband", "son", "daughter"]

EVENT_CATEGORIES = [
    "vital", "family", "religious", "vocational", "academic", "travel",
    "legal", "residence", "other", "custom",
]

# Event categories, as in gramps.gen.lib.eventtype.EventType._MENU
EVENT_CATEGORY_MAP = {
    "vital": ["Birth", "Baptism", "Death", "Stillbirth", "Burial", "Cremation", "Adopted"],
    "family": [
        "Engagement", "Marriage", "Divorce", "Annulment", "Marriage Settlement",
        "Marriage License", "Marriage Contract", "Marriage Banns",
        "Divorce Filing", "Alternate Marriage",
    ],
    "religious": [
        "Christening", "Adult Christening", "Confirmation", "First Communion",
        "Blessing", "Bar Mitzvah", "Bas Mitzvah", "Religion",
    ],
    "vocational": ["Occupation", "Retirement", "Elected", "Military Service", "Ordination"],
    "academic": ["Education", "Degree", "Graduation"],
    "travel": ["Emigration", "Immigration", "Naturalization"],
    "legal": ["Probate", "Will"],
    "residence": ["Residence", "Census", "Property"],
    "other": ["Cause Of Death", "Medical Information", "Nobility Title", "Number of Marriages"],
}

STANDARD_EVENT_TYPES = {
    "Unknown", "Custom", "Adopted", "Birth", "Death", "Adult Christening",
    "Baptism", "Bar Mitzvah", "Bas Mitzvah", "Blessing", "Burial",
    "Cause Of Death", "Census", "Christening", "Confirmation", "Cremation",
    "Degree", "Education", "Elected", "Emigration", "First Communion",
    "Immigration", "Graduation", "Medical Information", "Military Service",
    "Naturalization", "Nobility Title", "Number of Marriages", "Occupation",
    "Ordination", "Probate", "Property", "Religion", "Residence", "Retirement",
    "Will", "Marriage", "Marriage Settlement", "Marriage License",
    "Marriage Contract", "Marriage Banns", "Engagement", "Divorce",
    "Divorce Filing", "Annulment", "Alternate Marriage", "Stillbirth",
}

# Finnish names of standard event types and roles (from the Gramps fi.po).
FI_EVENT_TYPES = {
    "Unknown": "Tuntematon", "Custom": "Mukautettu", "Adopted": "Adoptoitu",
    "Birth": "Syntymä", "Death": "Kuolema", "Adult Christening": "Aikuiskaste",
    "Baptism": "Kaste", "Bar Mitzvah": "Bar Mitzvah", "Bas Mitzvah": "Bat Mitzvah",
    "Blessing": "Siunaus", "Burial": "Hautaus", "Cause Of Death": "Kuolinsyy",
    "Census": "Väestönlaskenta", "Christening": "Ristiäiset",
    "Confirmation": "Konfirmaatio", "Cremation": "Tuhkaus", "Degree": "Oppiarvo",
    "Education": "Koulutus", "Elected": "Valittu", "Emigration": "Maastamuutto",
    "First Communion": "Ensimmäinen ehtoollinen", "Immigration": "Maahanmuutto",
    "Graduation": "Valmistuminen", "Medical Information": "Hoitotiedot",
    "Military Service": "Asepalvelus", "Naturalization": "Kansalaistaminen",
    "Nobility Title": "Aateliarvo", "Number of Marriages": "Avioliittojen lukumäärä",
    "Occupation": "Ammatti", "Ordination": "Papiksi vihkiminen",
    "Probate": "Testamentti", "Property": "Omaisuus", "Religion": "Uskonto",
    "Residence": "Asuinpaikka", "Retirement": "Eläkkeelle siirtyminen",
    "Will": "Jälkisäädös", "Marriage": "Avioliitto",
    "Marriage Settlement": "Avioehto", "Marriage License": "Avioliittotodistus",
    "Marriage Contract": "Avioliittosopimus", "Marriage Banns": "Avioliittokuulutus",
    "Engagement": "Kihlaus", "Divorce": "Avioero", "Divorce Filing": "Avioerohakemus",
    "Annulment": "Avioliiton mitätöinti", "Alternate Marriage": "Avoliitto",
    "Stillbirth": "Kuolleena syntynyt", "Funeral": "Hautajaiset",
}

FI_ROLES = {
    "Primary": "Päähenkilö", "Family": "Perhe", "Witness": "Todistaja",
    "Celebrant": "Juhlittava", "Clergy": "Pappi", "Aide": "Avustaja",
    "Bride": "Morsian", "Groom": "Sulhanen", "Informant": "Tiedottaja",
    "Unknown": "Tuntematon", "Custom": "Mukautettu",
}

# Finnish names for the (English) relationship labels; the Finnish
# calculator already returns Finnish strings, these cover the capitalised
# English words gramps-web-api passes through gettext.
FI_RELATIONSHIPS = {
    "Father": "Isä", "Mother": "Äiti", "Husband": "Aviomies", "Wife": "Vaimo",
    "Son": "Poika", "Daughter": "Tytär", "Brother": "Veli", "Sister": "Sisar",
}

MAX_AGE_PROB_ALIVE = 110


def _lang(locale):
    return (locale or "en")[:2].lower()


def translate_event_type(event_type, lang):
    if lang == "fi":
        return FI_EVENT_TYPES.get(event_type, event_type)
    return event_type


def translate_role(role, lang):
    if lang == "fi":
        return FI_ROLES.get(role, role)
    return role


def get_birth_or_fallback(db, person):
    """Birth event of a person, or a birth fallback event (baptism, ...)."""
    refs = person.event_ref_list or []
    index = person.birth_ref_index if person.birth_ref_index is not None else -1
    if 0 <= index < len(refs):
        event = db.get_event(ref_handle(refs[index]))
        if event is not None:
            return event
    for ref, event in db.get_person_events(person):
        if event.type in BIRTH_FALLBACKS and ref_role(ref) == "Primary":
            return event
    return None


def get_death_or_fallback(db, person):
    """Death event of a person, or a death fallback event (burial, ...)."""
    refs = person.event_ref_list or []
    index = person.death_ref_index if person.death_ref_index is not None else -1
    if 0 <= index < len(refs):
        event = db.get_event(ref_handle(refs[index]))
        if event is not None:
            return event
    for ref, event in db.get_person_events(person):
        if event.type in DEATH_FALLBACKS and ref_role(ref) == "Primary":
            return event
    return None


def _family_event_or_fallback(db, family, main_type, fallbacks):
    fallback = None
    for ref, event in db.get_family_events(family):
        if event.type == main_type:
            return event
        if fallback is None and event.type in fallbacks and ref_role(ref) in PRIMARY_EVENT_ROLES:
            fallback = event
    return fallback


def get_marriage_or_fallback(db, family):
    return _family_event_or_fallback(db, family, "Marriage", MARRIAGE_FALLBACKS)


def get_divorce_or_fallback(db, family):
    return _family_event_or_fallback(db, family, "Divorce", DIVORCE_FALLBACKS)


class TimelineError(ValueError):
    """Invalid timeline arguments (maps to HTTP 422)."""


class Timeline:
    """Timeline class (port of the gramps-web-api Timeline)."""

    def __init__(
        self,
        db=None,
        dates=None,
        events=None,
        ratings=False,
        relatives=None,
        relative_events=None,
        discard_empty=True,
        omit_anchor=True,
        precision=1,
        locale="en",
    ):
        self.db = db or TreeCache()
        self.timeline = []
        self.dates = dates
        self.start_date = None
        self.end_date = None
        self.ratings = ratings
        self.discard_empty = discard_empty
        self.precision = precision
        self.locale = locale or "en"
        self.lang = _lang(locale)
        self.anchor_person = None
        self.omit_anchor = omit_anchor
        self.depth = 1
        self.event_filters = events or []
        self.relative_event_filters = relative_events or []
        self.relative_filters = relatives or []
        self._custom_types = None
        self.eligible_events = self._prepare_eligible_events(self.event_filters)
        self.eligible_relative_events = self._prepare_eligible_events(
            self.relative_event_filters
        )
        self.birth_dates = {}
        self._ratings = {}

        if dates and "-" in dates:
            start, end = dates.split("-", 1)
            self.start_date = parse_ymd_string(start) if "/" in start else None
            self.end_date = parse_ymd_string(end) if "/" in end else None
            if (start and "/" in start and self.start_date is None) or (
                end and "/" in end and self.end_date is None
            ):
                raise TimelineError("Invalid date range")

    # --------------------------------------------------------------- filters
    def _get_custom_types(self):
        if self._custom_types is None:
            types = Event.objects.values_list("type", flat=True).distinct()
            self._custom_types = {t for t in types if t and t not in STANDARD_EVENT_TYPES}
        return self._custom_types

    def _prepare_eligible_events(self, event_filters):
        eligible = {"Birth", "Death"}
        for key in event_filters:
            if key in STANDARD_EVENT_TYPES:
                eligible.add(key)
                continue
            if key in EVENT_CATEGORIES:
                continue
            if key in self._get_custom_types():
                eligible.add(key)
                continue
            raise TimelineError(f"{key} is not a valid event or event category")
        for category, types in EVENT_CATEGORY_MAP.items():
            if category in event_filters:
                eligible.update(types)
        if "custom" in event_filters:
            eligible.update(self._get_custom_types())
        return eligible

    def get_age(self, start_date, date):
        """Return calculated age or empty string otherwise."""
        if not start_date:
            return ""
        return span_string(start_date, date, precision=self.precision, lang=self.lang)

    def is_death_indicator(self, event):
        if event.type in DEATH_INDICATORS:
            return True
        return event.type in DEATH_INDICATOR_NAMES or (
            self.lang == "fi" and event.type in {FI_EVENT_TYPES.get(n) for n in DEATH_INDICATOR_NAMES}
        )

    def is_eligible(self, event, relative):
        if relative:
            if not self.relative_event_filters:
                return True
            return event.type in self.eligible_relative_events
        if not self.event_filters:
            return True
        return event.type in self.eligible_events

    # ------------------------------------------------------------- building
    def add_event(self, item, relative=False):
        """Add event to timeline if needed."""
        event = item[0]
        if self.discard_empty and get_sortval(event.date) == 0:
            return
        if self.end_date and is_before(self.end_date, event.date):
            return
        if self.start_date and is_after(self.start_date, event.date):
            return
        for existing in self.timeline:
            if existing[0].handle == event.handle:
                return
        if self.is_eligible(event, relative):
            if self.ratings:
                citations = event.citation_list or []
                self._ratings[event.handle] = (len(citations), _max_confidence(citations))
            self.timeline.append(item)

    def add_person(self, handle, anchor=False, start=True, end=True, ancestors=1, offspring=1):
        """Add events for a person to the timeline."""
        if self.anchor_person and handle == self.anchor_person.handle:
            return
        person = self.db.get_person(handle)
        if person is None:
            raise LookupError(handle)
        if person.handle not in self.birth_dates:
            event = get_birth_or_fallback(self.db, person)
            if event:
                self.birth_dates[person.handle] = event.date
        for ref, event in self.db.get_person_events(person):
            self.add_event((event, person, "self", ref_role(ref)))
        if anchor and not self.anchor_person:
            self.anchor_person = person
            self.depth = max(ancestors, offspring) + 1
            if self.start_date is None and self.end_date is None and self.timeline:
                if start or end:
                    self.timeline.sort(key=lambda x: get_sortval(x[0].date))
                    if start:
                        self.start_date = self.timeline[0][0].date
                    if end:
                        last_event = self.timeline[-1][0]
                        if self.is_death_indicator(last_event):
                            self.end_date = last_event.date
                        else:
                            self.end_date = self._probable_death_date(person, last_event)
            for family_handle in person.parent_family_list or []:
                self.add_family(family_handle, ancestors=ancestors)
            for family_handle in person.family_list or []:
                self.add_family(
                    family_handle, anchor=person, ancestors=ancestors, offspring=offspring
                )
        else:
            for family_handle in person.family_list or []:
                self.add_family(family_handle, anchor=person, events_only=True)

    def _probable_death_date(self, person, last_event):
        """Rough replacement for Gramps' probably_alive_range death estimate."""
        birth = self.birth_dates.get(person.handle)
        if birth:
            return add_years(birth, MAX_AGE_PROB_ALIVE)
        return add_years(last_event.date, MAX_AGE_PROB_ALIVE)

    def _relationship(self, person):
        """
        Return (english relationship, localized relationship) between the
        anchor person and ``person``.
        """
        calc_en = get_calculator(self.db, "en", self.depth)
        english = calc_en.get_one_relationship(self.anchor_person, person)
        if self.lang == "en":
            return english, english
        calc = get_calculator(self.db, self.lang, self.depth)
        return english, calc.get_one_relationship(self.anchor_person, person)

    def add_relative(self, handle, ancestors=1, offspring=1):
        """Add events for a relative of the anchor person."""
        person = self.db.get_person(handle)
        if person is None:
            raise LookupError(handle)
        relationship_en, relationship = self._relationship(person)
        if self.relative_filters:
            if not any(rel in relationship_en for rel in self.relative_filters):
                return

        if self.relative_event_filters:
            for ref, event in self.db.get_person_events(person):
                self.add_event((event, person, relationship, ref_role(ref)), relative=True)

        event = get_birth_or_fallback(self.db, person)
        if event:
            self.add_event((event, person, relationship, "Primary"), relative=True)
            if person.handle not in self.birth_dates:
                self.birth_dates[person.handle] = event.date

        event = get_death_or_fallback(self.db, person)
        if event:
            self.add_event((event, person, relationship, "Primary"), relative=True)

        for family_handle in person.family_list or []:
            family = self.db.get_family(family_handle)
            if family is None:
                continue
            event = get_marriage_or_fallback(self.db, family)
            if event:
                self.add_event((event, person, relationship, "Family"), relative=True)
            event = get_divorce_or_fallback(self.db, family)
            if event:
                self.add_event((event, person, relationship, "Family"), relative=True)
            if offspring > 1:
                for child_ref in family.child_ref_list or []:
                    child_handle = ref_handle(child_ref)
                    if child_handle and child_handle != self.anchor_person.handle:
                        self.add_relative(child_handle, offspring=offspring - 1)

        if ancestors > 1:
            if "father" in relationship_en or "mother" in relationship_en:
                for family_handle in person.parent_family_list or []:
                    self.add_family(
                        family_handle, include_children=False, ancestors=ancestors - 1
                    )

    def add_family(
        self, handle, anchor=None, include_children=True, ancestors=1, offspring=1,
        events_only=False,
    ):
        """Add events for all family members to the timeline."""
        family = self.db.get_family(handle)
        if family is None:
            raise LookupError(handle)
        if anchor:
            for ref, event in self.db.get_family_events(family):
                self.add_event((event, anchor, "self", ref_role(ref)))
            if events_only:
                return
        if self.anchor_person:
            anchor_handle = self.anchor_person.handle
            if family.father_handle_id and family.father_handle_id != anchor_handle:
                self.add_relative(family.father_handle_id, ancestors=ancestors)
            if family.mother_handle_id and family.mother_handle_id != anchor_handle:
                self.add_relative(family.mother_handle_id, ancestors=ancestors)
            if include_children:
                for child_ref in family.child_ref_list or []:
                    child_handle = ref_handle(child_ref)
                    if child_handle and child_handle != anchor_handle:
                        self.add_relative(child_handle, offspring=offspring)
        else:
            if family.father_handle_id:
                self.add_person(family.father_handle_id)
            if family.mother_handle_id:
                self.add_person(family.mother_handle_id)
            for child_ref in family.child_ref_list or []:
                child_handle = ref_handle(child_ref)
                if child_handle:
                    self.add_person(child_handle)

    # -------------------------------------------------------------- output
    def _label(self, event, person_object, relationship):
        label = translate_event_type(event.type, self.lang)
        if (
            person_object
            and self.anchor_person
            and self.anchor_person.handle != person_object.handle
            and relationship not in ("self", "", None)
        ):
            if self.lang == "fi":
                rel = relationship[:1].upper() + relationship[1:]
                rel = FI_RELATIONSHIPS.get(rel, rel)
            else:
                rel = relationship.title()
            label = f"{label} ({rel})"
        return label

    def _place_profile(self, event):
        place = self.db.get_place(event.place_id) if event.place_id else None
        if place is None:
            return {}
        profile = get_place_profile(place)
        profile["display_name"] = place.title or profile.get("name", "")
        profile["handle"] = event.place_id
        return profile

    def profile(self, page=0, pagesize=20):
        """Return a profile for the timeline."""
        profiles = []
        self.timeline.sort(key=lambda x: get_sortval(x[0].date))
        events = self.timeline
        if page > 0:
            offset = (page - 1) * pagesize
            events = events[offset: offset + pagesize]
        for event, person_object, relationship, role in events:
            label = self._label(event, person_object, relationship)
            age = ""
            person = {}
            if person_object is not None:
                person_age = ""
                get_person = True
                if self.anchor_person:
                    anchor_handle = self.anchor_person.handle
                    if anchor_handle in self.birth_dates:
                        age = self.get_age(self.birth_dates[anchor_handle], event.date)
                    if anchor_handle == person_object.handle:
                        person_age = age
                        if self.omit_anchor:
                            get_person = False
                if get_person:
                    person = get_person_profile(person_object, {"self"})
                    if not person_age and person_object.handle in self.birth_dates:
                        person_age = self.get_age(
                            self.birth_dates[person_object.handle], event.date
                        )
                        if not age:
                            age = person_age
                    person["age"] = person_age
            profile = {
                "date": display_date(event.date, self.lang),
                "description": event.description or "",
                "gramps_id": event.gramps_id or "",
                "handle": event.handle,
                "label": label,
                "media": [ref_handle(m) for m in (event.media_list or []) if ref_handle(m)],
                "person": person,
                "place": self._place_profile(event),
                "age": age,
                "span": age,
                "type": event.type or "",
                "role": translate_role(role, self.lang),
            }
            profile["person"]["relationship"] = str(relationship)
            if self.ratings:
                count, confidence = self._ratings.get(event.handle, (0, 0))
                profile["citations"] = count
                profile["confidence"] = confidence
            profiles.append(profile)
        return profiles
