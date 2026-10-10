"""
Gramps date helpers.

Dates are stored as JSON dicts in the Gramps JSON form::

    {"calendar": 0, "modifier": 0, "quality": 0,
     "dateval": [day, month, year, slash(, day2, month2, year2, slash2)],
     "text": "", "sortval": 0, "newyear": 0}

This module provides

* ``parse_date(text)`` -- a small port of the Gramps date parser that
  understands the strings used by filter rules ("between 1900 and 1950",
  "from 1900 to 1950", "before 1900", "after 1900", "about 1900", "1900",
  "1900-05-02", ...),
* ``get_start_stop_range(date)`` / ``dates_match(a, b, comparison)`` --
  ports of ``Date.get_start_stop_range`` and ``Date.match`` from Gramps,
* ``match_dates(objects, dates_param)`` -- the semantics of the ``dates``
  query parameter of gramps-web-api (``*/MM/DD`` anniversaries, ranges).

All comparisons use the day/month/year components of ``dateval``; the
``sortval`` field is never used because imported sort values are not
Julian day numbers.
"""

import calendar as _calendar
import datetime
import re

# Date modifiers (gramps.gen.lib.date.Date.MOD_*)
MOD_NONE = 0
MOD_BEFORE = 1
MOD_AFTER = 2
MOD_ABOUT = 3
MOD_RANGE = 4
MOD_SPAN = 5
MOD_TEXTONLY = 6
MOD_FROM = 7
MOD_TO = 8

# Date qualities (gramps.gen.lib.date.Date.QUAL_*)
QUAL_NONE = 0
QUAL_ESTIMATED = 1
QUAL_CALCULATED = 2

# Gramps config defaults: behavior.date-before-range etc. (years)
BEFORE_RANGE = 50
AFTER_RANGE = 50
ABOUT_RANGE = 50

EMPTY = (0, 0, 0)


# ---------------------------------------------------------------------------
# Construction and access
# ---------------------------------------------------------------------------


def make_date(start=EMPTY, stop=None, modifier=MOD_NONE, quality=QUAL_NONE, text=""):
    """Build a date dict in the stored JSON form.

    ``start`` and ``stop`` are ``(year, month, day)`` tuples.
    """
    year, month, day = start
    dateval = [day, month, year, False]
    if stop is not None:
        year2, month2, day2 = stop
        dateval += [day2, month2, year2, False]
    return {
        "calendar": 0,
        "modifier": modifier,
        "quality": quality,
        "dateval": dateval,
        "text": text,
        "sortval": year * 512 + month * 32 + day,
        "newyear": 0,
    }


def _int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def get_start(date):
    """Return the ``(year, month, day)`` start of a date dict."""
    if not isinstance(date, dict):
        return EMPTY
    dateval = date.get("dateval") or []
    if len(dateval) < 3:
        return EMPTY
    return (_int(dateval[2]), _int(dateval[1]), _int(dateval[0]))


def get_stop(date):
    """Return the ``(year, month, day)`` stop of a range/span, or EMPTY."""
    if not isinstance(date, dict):
        return EMPTY
    dateval = date.get("dateval") or []
    if len(dateval) < 7:
        return EMPTY
    return (_int(dateval[6]), _int(dateval[5]), _int(dateval[4]))


def get_modifier(date):
    return _int(date.get("modifier")) if isinstance(date, dict) else MOD_NONE


def get_quality(date):
    return _int(date.get("quality")) if isinstance(date, dict) else QUAL_NONE


def is_textonly(date):
    return isinstance(date, dict) and get_modifier(date) == MOD_TEXTONLY


def is_valid_date(date):
    """Equivalent of ``Date.is_valid()``: not text-only and has a start."""
    if not isinstance(date, dict) or not date:
        return False
    if is_textonly(date):
        return False
    return get_start(date) != EMPTY


def is_empty_date(date):
    """Equivalent of ``Date.is_empty()``: no information at all."""
    if not isinstance(date, dict) or not date:
        return True
    if is_textonly(date) and date.get("text"):
        return False
    return get_start(date) == EMPTY and get_stop(date) == EMPTY


# ---------------------------------------------------------------------------
# Range arithmetic (port of Date.get_start_stop_range / Date.match)
# ---------------------------------------------------------------------------


def _clamp_day(year, month, day):
    """Clamp a day to the length of the month, for calendar arithmetic."""
    month = min(max(month, 1), 12)
    try:
        max_day = _calendar.monthrange(year, month)[1]
    except ValueError:
        max_day = 31
    return year, month, min(max(day, 1), max_day)


def date_offset(ymd, days):
    """Return ``(year, month, day)`` shifted by ``days`` (Gregorian)."""
    year, month, day = _clamp_day(*ymd)
    if 1 <= year <= 9998:
        shifted = datetime.date(year, month, day) + datetime.timedelta(days=days)
        return (shifted.year, shifted.month, shifted.day)
    # Out of range for datetime: do a rough shift without crossing months.
    return (year, month, min(max(day + days, 1), 31))


def get_start_stop_range(date):
    """Return ``(startmin, stopmax)`` as ``(year, month, day)`` tuples.

    Port of ``Date.get_start_stop_range``. Missing months/days are widened
    to the full year/month; before/after/about dates are widened by the
    Gramps default ranges (50 years).
    """
    start = get_start(date)
    stop = get_stop(date)
    if stop == EMPTY:
        stop = start

    stopmax = list(stop)
    if stopmax[0] == 0:
        stopmax[0] = start[0]
    if stopmax[1] == 0:
        stopmax[1] = 12
    if stopmax[2] == 0:
        stopmax[2] = 31
    startmin = list(start)
    if startmin[1] == 0:
        startmin[1] = 1
    if startmin[2] == 0:
        startmin[2] = 1

    modifier = get_modifier(date)
    quality = get_quality(date)
    if modifier in (MOD_BEFORE, MOD_TO):
        if modifier == MOD_BEFORE:
            stopmax = list(date_offset(startmin, -1))
        startmin = [stopmax[0] - BEFORE_RANGE, stopmax[1], stopmax[2]]
    elif modifier in (MOD_AFTER, MOD_FROM):
        if modifier == MOD_AFTER:
            startmin = list(date_offset(stopmax, 1))
        stopmax = [startmin[0] + AFTER_RANGE, startmin[1], startmin[2]]
    elif modifier == MOD_ABOUT or quality == QUAL_ESTIMATED:
        startmin = [startmin[0] - ABOUT_RANGE, startmin[1], startmin[2]]
        stopmax = [stopmax[0] + ABOUT_RANGE, stopmax[1], stopmax[2]]
    return tuple(startmin), tuple(stopmax)


def dates_match(date, other, comparison="="):
    """Port of ``Date.match`` with the same comparison operators.

    ``=``   any part of ``other`` overlaps any part of ``date``
    ``<``   any part of ``date`` < any part of ``other``
    ``<<``  all parts of ``date`` < all parts of ``other``
    ``>``   any part of ``date`` > any part of ``other``
    ``>>``  all parts of ``date`` > all parts of ``other``
    """
    if is_textonly(date) or is_textonly(other):
        # Gramps compares the text of both dates here, which makes a
        # text-only date match any regular date (whose text is empty).
        # We only match text against text.
        if not (is_textonly(date) and is_textonly(other)):
            return False
        if comparison == "=":
            text = str((date or {}).get("text", "")) if isinstance(date, dict) else ""
            other_text = str((other or {}).get("text", "")) if isinstance(other, dict) else ""
            return text.upper().find(other_text.upper()) != -1
        if comparison == "==":
            return (date or {}).get("text") == (other or {}).get("text")
        return False
    if not is_valid_date(date) or not is_valid_date(other):
        return False

    other_start, other_stop = get_start_stop_range(other)
    self_start, self_stop = get_start_stop_range(date)

    if comparison == "=":
        return (
            (self_start <= other_start <= self_stop)
            or (self_start <= other_stop <= self_stop)
            or (other_start <= self_start <= other_stop)
            or (other_start <= self_stop <= other_stop)
        )
    if comparison == "==":
        return self_start == other_start and self_stop == other_stop
    if comparison == "<":
        return self_start < other_stop
    if comparison == "<=":
        return self_start <= other_stop
    if comparison == "<<":
        return self_stop < other_start
    if comparison == ">":
        return self_stop > other_start
    if comparison == ">=":
        return self_stop >= other_start
    if comparison == ">>":
        return self_start > other_stop
    raise ValueError(f"Invalid date comparison operator: '{comparison}'")


# ---------------------------------------------------------------------------
# Parsing of date strings used in filter rules
# ---------------------------------------------------------------------------

_MONTHS = {
    # English
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12,
    # Finnish
    "tammikuu": 1, "tammikuuta": 1, "helmikuu": 2, "helmikuuta": 2,
    "maaliskuu": 3, "maaliskuuta": 3, "huhtikuu": 4, "huhtikuuta": 4,
    "toukokuu": 5, "toukokuuta": 5, "kesäkuu": 6, "kesäkuuta": 6,
    "heinäkuu": 7, "heinäkuuta": 7, "elokuu": 8, "elokuuta": 8,
    "syyskuu": 9, "syyskuuta": 9, "lokakuu": 10, "lokakuuta": 10,
    "marraskuu": 11, "marraskuuta": 11, "joulukuu": 12, "joulukuuta": 12,
}

_QUALITIES = {
    "estimated": QUAL_ESTIMATED, "est": QUAL_ESTIMATED, "est.": QUAL_ESTIMATED,
    "calculated": QUAL_CALCULATED, "calc": QUAL_CALCULATED, "calc.": QUAL_CALCULATED,
    # Finnish
    "arviolta": QUAL_ESTIMATED, "laskettu": QUAL_CALCULATED,
}

_MODIFIERS = {
    "before": MOD_BEFORE, "bef": MOD_BEFORE, "bef.": MOD_BEFORE,
    "after": MOD_AFTER, "aft": MOD_AFTER, "aft.": MOD_AFTER,
    "about": MOD_ABOUT, "abt": MOD_ABOUT, "abt.": MOD_ABOUT, "circa": MOD_ABOUT,
    "c.": MOD_ABOUT, "ca": MOD_ABOUT, "ca.": MOD_ABOUT, "around": MOD_ABOUT,
    "from": MOD_FROM, "to": MOD_TO,
    # Finnish
    "ennen": MOD_BEFORE, "jälkeen": MOD_AFTER, "noin": MOD_ABOUT,
    "alkaen": MOD_FROM, "asti": MOD_TO, "saakka": MOD_TO,
}

_RE_RANGE = re.compile(r"^(?:bet|bet\.|between)\s+(?P<start>.+?)\s+and\s+(?P<stop>.+)$", re.I)
_RE_SPAN = re.compile(r"^from\s+(?P<start>.+?)\s+(?:to|until)\s+(?P<stop>.+)$", re.I)
_RE_ISO = re.compile(r"^(?P<y>-?\d+)-(?P<m>\d{1,2})(?:-(?P<d>\d{1,2}))?$")
_RE_YEAR = re.compile(r"^-?\d+$")
_RE_DOTTED = re.compile(r"^(?P<d>\d{1,2})\.(?P<m>\d{1,2})\.(?P<y>-?\d+)$")
_RE_DOTTED_MONTH = re.compile(r"^(?P<m>\d{1,2})\.(?P<y>-?\d+)$")
_RE_SLASHED = re.compile(r"^(?P<m>\d{1,2})/(?P<d>\d{1,2})/(?P<y>-?\d+)$")
_RE_TEXT_DMY = re.compile(r"^(?:(?P<d>\d{1,2})\.?\s+)?(?P<mon>[^\W\d]+)\.?,?\s+(?P<y>-?\d+)$", re.U)
_RE_TEXT_MDY = re.compile(r"^(?P<mon>[^\W\d]+)\.?\s+(?P<d>\d{1,2}),?\s+(?P<y>-?\d+)$", re.U)


def _parse_subdate(text):
    """Parse a single date without modifiers; return (y, m, d) or None."""
    text = text.strip()
    if not text:
        return None
    match = _RE_ISO.match(text)
    if match:
        return (int(match["y"]), int(match["m"]), int(match["d"] or 0))
    if _RE_YEAR.match(text):
        return (int(text), 0, 0)
    match = _RE_DOTTED.match(text)
    if match:
        return (int(match["y"]), int(match["m"]), int(match["d"]))
    match = _RE_DOTTED_MONTH.match(text)
    if match:
        return (int(match["y"]), int(match["m"]), 0)
    match = _RE_SLASHED.match(text)
    if match:
        return (int(match["y"]), int(match["m"]), int(match["d"]))
    for regex in (_RE_TEXT_DMY, _RE_TEXT_MDY):
        match = regex.match(text)
        if match:
            month = _MONTHS.get(match["mon"].lower())
            if month:
                return (int(match["y"]), month, int(match["d"] or 0))
    return None


def _split_keyword(text, table):
    """Split a leading keyword (modifier/quality) off ``text``."""
    parts = text.split(None, 1)
    if len(parts) == 2 and parts[0].lower() in table:
        return table[parts[0].lower()], parts[1]
    return None, text


def _split_trailing_keyword(text, table):
    """Split a trailing keyword (Finnish-style 'asti', 'jälkeen') off ``text``."""
    parts = text.rsplit(None, 1)
    if len(parts) == 2 and parts[1].lower() in table:
        return table[parts[1].lower()], parts[0]
    return None, text


def parse_date(text):
    """Parse a date string into a date dict.

    Anything that cannot be understood becomes a text-only date, like the
    Gramps parser does.
    """
    if isinstance(text, dict):
        return text
    original = "" if text is None else str(text)
    text = original.strip()
    if not text:
        return {}

    quality, text = _split_keyword(text, _QUALITIES)
    quality = quality or QUAL_NONE

    for regex, modifier in ((_RE_RANGE, MOD_RANGE), (_RE_SPAN, MOD_SPAN)):
        match = regex.match(text)
        if match:
            start = _parse_subdate(match["start"])
            stop = _parse_subdate(match["stop"])
            if start is not None and stop is not None:
                return make_date(start, stop, modifier, quality)
            break

    modifier, rest = _split_keyword(text, _MODIFIERS)
    if modifier is None:
        modifier, rest = _split_trailing_keyword(text, _MODIFIERS)
    if modifier is not None:
        start = _parse_subdate(rest)
        if start is not None:
            return make_date(start, None, modifier, quality)

    start = _parse_subdate(text)
    if start is not None:
        return make_date(start, None, MOD_NONE, quality)

    # Localised "between X and Y" strings (e.g. Finnish "1900 ja 1950
    # välillä") are recognised by their two year numbers.
    numbers = re.findall(r"-?\d+", text)
    if len(numbers) == 2 and not re.search(r"\d[./-]\d", text):
        return make_date((int(numbers[0]), 0, 0), (int(numbers[1]), 0, 0), MOD_RANGE, quality)

    return make_date(EMPTY, None, MOD_TEXTONLY, QUAL_NONE, original)


# ---------------------------------------------------------------------------
# The `dates` query parameter (gramps-web-api semantics)
# ---------------------------------------------------------------------------


def _parse_mask_part(part, allow_wildcard):
    """Parse ``y/m/d`` (wildcards allowed) into a list of ints or ``None``."""
    pieces = part.split("/")
    if len(pieces) != 3:
        raise ValueError(f"Invalid date mask '{part}': expected y/m/d")
    result = []
    for name, piece, limit in zip(("year", "month", "day"), pieces, (None, 12, 31)):
        if piece == "*" and allow_wildcard:
            result.append(None)
            continue
        if not piece.isdigit():
            raise ValueError(f"Invalid {name} '{piece}' in date mask '{part}'")
        value = int(piece)
        if limit is not None and not 1 <= value <= limit:
            raise ValueError(f"Invalid {name} '{piece}' in date mask '{part}'")
        result.append(value)
    return result


def parse_dates_param(dates_param):
    """Parse the ``dates`` query parameter.

    Returns ``("mask", [year, month, day])`` (``None`` = wildcard) or
    ``("range", start, end)`` where start/end are ``(y, m, d)`` or ``None``.

    Formats (as in gramps-web-api): ``y/m/d`` with ``*`` wildcards,
    ``-y/m/d`` (before), ``y/m/d-`` (after), ``y/m/d-y/m/d`` (range).
    """
    if dates_param is None:
        raise ValueError("Missing dates parameter")
    mask = str(dates_param).strip()
    if not mask:
        raise ValueError("Empty dates parameter")
    if "-" in mask:
        parts = mask.split("-")
        if len(parts) != 2 or not (parts[0] or parts[1]):
            raise ValueError(f"Invalid date range '{mask}'")
        start = tuple(_parse_mask_part(parts[0], False)) if parts[0] else None
        end = tuple(_parse_mask_part(parts[1], False)) if parts[1] else None
        return ("range", start, end)
    return ("mask", _parse_mask_part(mask, True))


def match_date_mask(date, mask):
    """Check a date dict against ``[year, month, day]`` (``None`` wildcard)."""
    if not is_valid_date(date):
        return False
    year, month, day = get_start(date)
    year_mask, month_mask, day_mask = mask
    if year_mask is not None and year != year_mask:
        return False
    if month_mask is not None and month != month_mask:
        return False
    if day_mask is not None and day != day_mask:
        return False
    return True


def match_date_range(date, start, end):
    """Check that a date dict falls within ``start``..``end`` (inclusive)."""
    if not is_valid_date(date):
        return False
    if start is not None and dates_match(make_date(start), date, ">"):
        return False
    if end is not None and dates_match(make_date(end), date, "<"):
        return False
    return True


def match_dates(objects, dates_param):
    """Filter objects with a ``date`` attribute by the ``dates`` parameter.

    ``objects`` may be a queryset or a list of model instances (Event,
    Citation, Media). Returns a list of the matching instances.
    Raises ``ValueError`` for a malformed parameter.
    """
    parsed = parse_dates_param(dates_param)
    result = []
    for obj in objects:
        date = getattr(obj, "date", None)
        if parsed[0] == "mask":
            if match_date_mask(date, parsed[1]):
                result.append(obj)
        else:
            if match_date_range(date, parsed[1], parsed[2]):
                result.append(obj)
    return result
