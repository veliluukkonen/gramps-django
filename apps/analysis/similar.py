"""
Similar-person lookup used by the "new person" form to warn about
possible duplicates while the user is typing.

Matching is prefix based so the candidate set shrinks with every typed
character: the surname (primary or any alternate name) must start with the
typed surname, the first name must start with the typed first name, and
the birth year, when given, must be within ``YEAR_TOLERANCE`` years (people
without a known birth year are kept).
"""

import unicodedata

from apps.core.models import Event, Person
from apps.core.profile import get_person_profile

DEFAULT_LIMIT = 20
MAX_LIMIT = 200
YEAR_TOLERANCE = 3


def normalize(text):
    """Lower-case, strip accents and surrounding whitespace."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.lower().split())


def _surnames(name):
    if not isinstance(name, dict):
        return []
    out = []
    for s in name.get("surname_list") or []:
        if isinstance(s, dict) and s.get("surname"):
            out.append(normalize(s.get("surname")))
    return out


def _first_names(name):
    if not isinstance(name, dict):
        return ""
    return normalize(name.get("first_name"))


def _birth_year(person, event_years):
    """Birth year via birth_ref_index, else the first Birth event ref."""
    refs = person.event_ref_list or []
    candidates = []
    if 0 <= person.birth_ref_index < len(refs):
        candidates.append(refs[person.birth_ref_index])
    candidates.extend(refs)
    for ref in candidates:
        handle = ref.get("ref") if isinstance(ref, dict) else None
        info = event_years.get(handle)
        if info and info[0] == "Birth" and info[1]:
            return info[1]
    return None


def _event_year_map():
    """{event handle: (type, year)} for birth events only."""
    out = {}
    for handle, etype, date in Event.objects.filter(type="Birth").values_list(
        "handle", "type", "date"
    ):
        year = None
        if isinstance(date, dict):
            dateval = date.get("dateval") or []
            if len(dateval) >= 3 and isinstance(dateval[2], int) and dateval[2]:
                year = dateval[2]
        out[handle] = (etype, year)
    return out


def find_similar_people(first_name="", surname="", birth_year=None, gender=None, limit=DEFAULT_LIMIT):
    """
    Return ``(total, candidates)``.

    ``candidates`` is a list of ``{handle, gramps_id, birth_year, profile}``
    sorted by name, but only when ``total <= limit``; otherwise it is empty
    so the caller can ask the user to narrow the search.
    """
    first_q = normalize(first_name)
    surname_q = normalize(surname)
    if not first_q and not surname_q:
        return 0, []

    event_years = _event_year_map()
    matches = []
    for person in Person.objects.all().only(
        "handle", "gramps_id", "gender", "primary_name", "alternate_names",
        "event_ref_list", "birth_ref_index",
    ):
        names = [person.primary_name] + list(person.alternate_names or [])
        if surname_q and not any(
            s.startswith(surname_q) for name in names for s in _surnames(name)
        ):
            continue
        if first_q and not any(
            _first_names(name).startswith(first_q) for name in names
        ):
            continue
        year = _birth_year(person, event_years)
        if birth_year is not None and year is not None and abs(year - birth_year) > YEAR_TOLERANCE:
            continue
        matches.append((person, year))

    total = len(matches)
    if total > limit:
        return total, []

    results = []
    for person, year in matches:
        profile = get_person_profile(person, {"self"})
        results.append({
            "handle": person.handle,
            "gramps_id": person.gramps_id,
            "gender": person.gender,
            "gender_match": gender is None or person.gender == gender,
            "birth_year": year,
            "profile": profile,
        })
    results.sort(key=lambda r: (r["profile"].get("name_surname", ""), r["profile"].get("name_given", "")))
    return total, results
