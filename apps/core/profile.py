"""
Profile parameter implementation.

Computes display-friendly summaries for Gramps objects.
Profile data includes formatted names, dates, and nested relationships.
"""

from .models import (
    Citation,
    Event,
    Family,
    MediaObject,
    Note,
    Person,
    Place,
    Source,
)


def _format_date(date_obj):
    """Format a Gramps date JSON object to a display string."""
    if not date_obj or not isinstance(date_obj, dict):
        return ""

    text = date_obj.get("text", "")
    if text:
        return text

    dateval = date_obj.get("dateval")
    if not dateval:
        return ""

    # dateval is [day, month, year, slash] or longer for ranges
    if len(dateval) >= 3:
        day, month, year = dateval[0], dateval[1], dateval[2]
        parts = []
        if year and year != 0:
            parts.append(str(year))
        if month and month != 0:
            parts.append(f"{month:02d}")
        if day and day != 0:
            parts.append(f"{day:02d}")
        if parts:
            # Format as YYYY-MM-DD
            return "-".join(parts)

    return ""


def _get_person_name_display(person):
    """Get formatted display name from a Person instance."""
    name = person.primary_name if isinstance(person.primary_name, dict) else {}
    if not name:
        return "", "", "", ""

    first_name = name.get("first_name", "")
    suffix = name.get("suffix", "")
    surname_list = name.get("surname_list", [])

    surname = ""
    for s in surname_list:
        if isinstance(s, dict) and s.get("primary", False):
            prefix = s.get("prefix", "")
            sn = s.get("surname", "")
            surname = f"{prefix} {sn}".strip() if prefix else sn
            break
    if not surname and surname_list:
        s = surname_list[0]
        if isinstance(s, dict):
            surname = s.get("surname", "")

    display = f"{first_name} {surname}".strip()
    if suffix:
        display = f"{display}, {suffix}"

    return display, first_name, surname, suffix


def _get_gender_str(gender):
    """Convert gender integer to M/F/U string."""
    return {0: "F", 1: "M", 2: "U", 3: "O"}.get(gender, "U")


def _get_event_by_ref_index(person, index):
    """Get event from person's event_ref_list by index."""
    if index < 0:
        return None
    refs = person.event_ref_list or []
    if index >= len(refs):
        return None
    ref = refs[index]
    handle = ref.get("ref") if isinstance(ref, dict) else None
    if not handle:
        return None
    try:
        return Event.objects.get(pk=handle)
    except Event.DoesNotExist:
        return None


def _get_place_name(place_handle):
    """Get place display name from handle."""
    if not place_handle:
        return "", ""
    try:
        place = Place.objects.get(pk=place_handle)
    except Place.DoesNotExist:
        return "", ""

    name = ""
    if isinstance(place.name, dict):
        name = place.name.get("value", "")
    title = place.title or name

    return title, name


def get_event_profile(event, profile_args=None):
    """
    Build profile dict for an Event.

    Returns: {type, date, place, place_name, summary}
    """
    if event is None:
        return {}

    profile_args = profile_args or set()
    place_display, place_name = _get_place_name(event.place_id)

    profile = {
        "type": event.type or "",
        "date": _format_date(event.date),
        "place": place_display,
        "place_name": place_name,
        "summary": event.description or "",
    }

    if "all" in profile_args or "ratings" in profile_args:
        citations = event.citation_list or []
        profile["citations"] = len(citations)
        profile["confidence"] = _max_confidence(citations)

    return profile


def _max_confidence(citation_handles):
    """Get max confidence from a list of citation handles."""
    if not citation_handles:
        return 0
    confidences = Citation.objects.filter(
        pk__in=citation_handles
    ).values_list("confidence", flat=True)
    return max(confidences) if confidences else 0


def get_person_profile(person, profile_args=None):
    """
    Build profile dict for a Person.

    profile_args: set of profile keys (self, all, families, events, age, ratings, references)
    """
    if person is None:
        return {}

    profile_args = profile_args or {"self"}
    name_display, name_given, name_surname, name_suffix = _get_person_name_display(person)

    # Birth/death events
    birth_event = _get_event_by_ref_index(person, person.birth_ref_index)
    death_event = _get_event_by_ref_index(person, person.death_ref_index)

    profile = {
        "handle": person.handle,
        "gramps_id": person.gramps_id,
        "sex": _get_gender_str(person.gender),
        "name_display": name_display,
        "name_given": name_given,
        "name_surname": name_surname,
        "name_suffix": name_suffix,
        "birth": get_event_profile(birth_event, profile_args),
        "death": get_event_profile(death_event, profile_args),
    }

    all_args = "all" in profile_args

    # Events
    if all_args or "events" in profile_args:
        events = []
        for ref in (person.event_ref_list or []):
            handle = ref.get("ref") if isinstance(ref, dict) else None
            if not handle:
                continue
            try:
                evt = Event.objects.get(pk=handle)
                ep = get_event_profile(evt, profile_args)
                ep["role"] = ref.get("role", "Primary")
                events.append(ep)
            except Event.DoesNotExist:
                continue
        profile["events"] = events

    # Families
    if all_args or "families" in profile_args:
        families = []
        for fam_handle in (person.family_list or []):
            try:
                fam = Family.objects.get(pk=fam_handle)
                families.append(get_family_profile(fam, {"self"}))
            except Family.DoesNotExist:
                continue
        profile["families"] = families

        # Parent families
        parent_families = person.parent_family_list or []
        if parent_families:
            try:
                primary_fam = Family.objects.get(pk=parent_families[0])
                profile["primary_parent_family"] = get_family_profile(
                    primary_fam, {"self"}
                )
            except Family.DoesNotExist:
                profile["primary_parent_family"] = {}

            other = []
            for fh in parent_families[1:]:
                try:
                    fam = Family.objects.get(pk=fh)
                    other.append(get_family_profile(fam, {"self"}))
                except Family.DoesNotExist:
                    continue
            profile["other_parent_families"] = other
        else:
            profile["primary_parent_family"] = {}
            profile["other_parent_families"] = []

    return profile


def get_family_profile(family, profile_args=None):
    """
    Build profile dict for a Family.

    Returns: {handle, gramps_id, family_surname, relationship,
              father, mother, children, marriage, divorce}
    """
    if family is None:
        return {}

    profile_args = profile_args or {"self"}
    all_args = "all" in profile_args

    # Father/mother profiles
    father_profile = {}
    mother_profile = {}
    family_surname = ""

    if family.father_handle_id:
        try:
            father = Person.objects.get(pk=family.father_handle_id)
            father_profile = get_person_profile(father, {"self"})
            family_surname = father_profile.get("name_surname", "")
        except Person.DoesNotExist:
            pass

    if family.mother_handle_id:
        try:
            mother = Person.objects.get(pk=family.mother_handle_id)
            mother_profile = get_person_profile(mother, {"self"})
            if not family_surname:
                family_surname = mother_profile.get("name_surname", "")
        except Person.DoesNotExist:
            pass

    # Find marriage/divorce events
    marriage_profile = {}
    divorce_profile = {}
    event_profiles = []

    for ref in (family.event_ref_list or []):
        handle = ref.get("ref") if isinstance(ref, dict) else None
        if not handle:
            continue
        try:
            evt = Event.objects.get(pk=handle)
            ep = get_event_profile(evt, profile_args)
            event_profiles.append(ep)
            if evt.type == "Marriage" and not marriage_profile:
                marriage_profile = ep
            elif evt.type == "Divorce" and not divorce_profile:
                divorce_profile = ep
        except Event.DoesNotExist:
            continue

    # Children profiles
    children = []
    if all_args or "families" in profile_args or "self" in profile_args:
        for child_ref in (family.child_ref_list or []):
            child_handle = child_ref.get("ref") if isinstance(child_ref, dict) else None
            if not child_handle:
                continue
            try:
                child = Person.objects.get(pk=child_handle)
                children.append(get_person_profile(child, {"self"}))
            except Person.DoesNotExist:
                continue

    profile = {
        "handle": family.handle,
        "gramps_id": family.gramps_id,
        "family_surname": family_surname,
        "relationship": family.type or "",
        "father": father_profile,
        "mother": mother_profile,
        "children": children,
        "marriage": marriage_profile,
        "divorce": divorce_profile,
    }

    if all_args or "events" in profile_args:
        profile["events"] = event_profiles

    return profile


def get_place_profile(place, profile_args=None):
    """Build profile dict for a Place."""
    if place is None:
        return {}

    name_value = ""
    if isinstance(place.name, dict):
        name_value = place.name.get("value", "")

    alt_names = []
    alt_place_names = []
    for an in (place.alt_names or []):
        if isinstance(an, dict):
            alt_names.append(an.get("value", ""))
            alt_place_names.append({
                "value": an.get("value", ""),
                "date": _format_date(an.get("date")),
            })

    # Parse lat/long
    lat = None
    long = None
    try:
        lat = float(place.lat) if place.lat else None
    except (ValueError, TypeError):
        pass
    try:
        long = float(place.long) if place.long else None
    except (ValueError, TypeError):
        pass

    # Parent places
    parent_places = []
    direct_parent_places = []
    for pref in (place.placeref_list or []):
        ref_handle = pref.get("ref") if isinstance(pref, dict) else None
        if not ref_handle:
            continue
        try:
            parent = Place.objects.get(pk=ref_handle)
            parent_places.append(get_place_profile(parent))
            direct_parent_places.append({
                "ref": ref_handle,
                "date": _format_date(pref.get("date")),
            })
        except Place.DoesNotExist:
            continue

    return {
        "gramps_id": place.gramps_id,
        "type": place.place_type or "",
        "name": name_value,
        "alternate_names": alt_names,
        "alternate_place_names": alt_place_names,
        "lat": lat,
        "long": long,
        "parent_places": parent_places,
        "direct_parent_places": direct_parent_places,
    }


def get_source_profile(source, profile_args=None):
    """Build profile dict for a Source."""
    if source is None:
        return {}
    return {
        "gramps_id": source.gramps_id,
        "title": source.title or "",
        "author": source.author or "",
        "pubinfo": source.pubinfo or "",
    }


def get_citation_profile(citation, profile_args=None):
    """Build profile dict for a Citation."""
    if citation is None:
        return {}

    source_profile = {}
    if citation.source_handle_id:
        try:
            source = Source.objects.get(pk=citation.source_handle_id)
            source_profile = get_source_profile(source)
        except Source.DoesNotExist:
            pass

    return {
        "gramps_id": citation.gramps_id,
        "date": _format_date(citation.date),
        "page": citation.page or "",
        "source": source_profile,
    }


def get_media_profile(media_obj, profile_args=None):
    """Build profile dict for a Media object."""
    if media_obj is None:
        return {}
    return {
        "gramps_id": media_obj.gramps_id,
        "date": _format_date(media_obj.date),
    }


def get_repository_profile(repo, profile_args=None):
    """Build profile dict for a Repository."""
    if repo is None:
        return {}
    return {
        "gramps_id": repo.gramps_id,
        "name": repo.name or "",
        "type": repo.type or "",
    }


def get_note_profile(note, profile_args=None):
    """Build profile dict for a Note."""
    if note is None:
        return {}
    return {
        "gramps_id": note.gramps_id,
        "type": note.type or "",
    }


# Model name → profile function mapping
PROFILE_FUNCTIONS = {
    "Person": get_person_profile,
    "Family": get_family_profile,
    "Event": get_event_profile,
    "Place": get_place_profile,
    "Source": get_source_profile,
    "Citation": get_citation_profile,
    "MediaObject": get_media_profile,
    "Repository": get_repository_profile,
    "Note": get_note_profile,
}


def get_profile(obj, profile_args):
    """Get profile for any Gramps object."""
    model_name = obj.__class__.__name__
    func = PROFILE_FUNCTIONS.get(model_name)
    if func:
        return func(obj, profile_args)
    return {}
