"""
Normalization of Gramps Name objects.

gramps-web-api always returns complete ``Name`` objects (every field present,
``surname_list`` at least an empty list) because they go through the Gramps
library. The frontend relies on that: e.g. the name list crashes on a name
without ``surname_list``. Names are stored here as the client sends them, so
they are completed with the Gramps defaults on read and on write.
"""

# Gramps Name defaults (gramps.gen.lib.Name), dates excluded: a missing date
# is treated as empty everywhere.
_NAME_DEFAULTS = {
    "_class": "Name",
    "private": False,
    "first_name": "",
    "suffix": "",
    "title": "",
    "call": "",
    "nick": "",
    "famnick": "",
    "group_as": "",
    "sort_as": 0,
    "display_as": 0,
    "type": "Birth Name",
}

_NAME_LIST_KEYS = ("surname_list", "citation_list", "note_list")

_SURNAME_DEFAULTS = {
    "_class": "Surname",
    "surname": "",
    "prefix": "",
    "connector": "",
    "origintype": "",
}


def _normalize_surname(surname):
    if not isinstance(surname, dict):
        return surname
    result = {**_SURNAME_DEFAULTS, **surname}
    for key, default in _SURNAME_DEFAULTS.items():
        if result[key] is None:
            result[key] = default
    return result


def normalize_name(name):
    """Return a copy of a Name dict with all missing Gramps fields filled in."""
    if not isinstance(name, dict):
        name = {}
    result = {**_NAME_DEFAULTS, **name}
    for key, default in _NAME_DEFAULTS.items():
        if result[key] is None:
            result[key] = default
    for key in _NAME_LIST_KEYS:
        if not isinstance(result.get(key), list):
            result[key] = []
    result["surname_list"] = [_normalize_surname(s) for s in result["surname_list"]]
    return result


def normalize_name_list(names):
    """Normalize a list of Name dicts (``alternate_names``)."""
    if not isinstance(names, list):
        return []
    return [normalize_name(n) for n in names]
