"""
Date helpers for the analysis endpoints.

Gramps dates are stored as JSON::

    {"dateval": [day, month, year, slash, (day2, month2, year2, slash2)],
     "modifier": 0, "quality": 0, "sortval": 2415021, "text": "", ...}

Ages and spans are computed from the year/month/day components (not from
``sortval``) following the arithmetic in ``gramps.gen.lib.date.Span``.
"""

from apps.core.profile import _format_date

MOD_NONE = 0
MOD_BEFORE = 1
MOD_AFTER = 2
MOD_ABOUT = 3
MOD_RANGE = 4
MOD_SPAN = 5
MOD_TEXTONLY = 6
MOD_FROM = 7
MOD_TO = 8

# Age above which Gramps reports "greater than N years".
ALIVE_LIMIT = 110

_WORDS = {
    "en": {
        "more than": "more than",
        "less than": "less than",
        "about": "about",
        "between": "between",
        "and": "and",
        "from": "from",
        "to": "to",
        "before": "before",
        "after": "after",
        "unknown": "unknown",
        "greater than": "greater than",
        "0 days": "0 days",
        "year": ("{n} year", "{n} years"),
        "month": ("{n} month", "{n} months"),
        "day": ("{n} day", "{n} days"),
    },
    "fi": {
        "more than": "enemmän kuin",
        "less than": "vähemmän kuin",
        "about": "noin",
        "between": "välillä",
        "and": "ja",
        "from": "alkaen",
        "to": "asti",
        "before": "ennen",
        "after": "jälkeen",
        "unknown": "tuntematon",
        "greater than": "yli",
        "0 days": "0 päivää",
        "year": ("{n} vuosi", "{n} vuotta"),
        "month": ("{n} kuukausi", "{n} kuukautta"),
        "day": ("{n} päivä", "{n} päivää"),
    },
}


def words(lang):
    """Return the word table for a language (English fallback)."""
    return _WORDS.get((lang or "en")[:2], _WORDS["en"])


def gregorian_sdn(year, month, day):
    """Serial day number of a Gregorian date (same algorithm as Gramps)."""
    if year == 0 or month == 0 or day == 0:
        # Gramps treats missing parts as 1 for sorting purposes
        month = month or 1
        day = day or 1
    if year < 0:
        year += 4801
    else:
        year += 4800
    if month > 2:
        month -= 3
    else:
        month += 9
        year -= 1
    return (
        ((year // 100) * 146097) // 4
        + ((year % 100) * 1461) // 4
        + (month * 153 + 2) // 5
        + day
        - 32045
    )


def _dateval(date):
    if not date or not isinstance(date, dict):
        return None
    dateval = date.get("dateval")
    if not dateval or len(dateval) < 3:
        return None
    return dateval


def get_ymd(date):
    """Return (year, month, day) of the (start) date or None if undated."""
    dateval = _dateval(date)
    if dateval is None:
        return None
    day, month, year = dateval[0] or 0, dateval[1] or 0, dateval[2] or 0
    if not year and not month and not day:
        return None
    return (year, month, day)


def get_stop_ymd(date):
    """Return (year, month, day) of the second date of a range/span."""
    dateval = _dateval(date)
    if dateval is None or len(dateval) < 7:
        return None
    day, month, year = dateval[4] or 0, dateval[5] or 0, dateval[6] or 0
    if not year and not month and not day:
        return None
    return (year, month, day)


def get_modifier(date):
    if not date or not isinstance(date, dict):
        return MOD_NONE
    try:
        return int(date.get("modifier") or 0)
    except (TypeError, ValueError):
        return MOD_NONE


def get_sortval(date):
    """Return the sort value of a date (0 when undated/invalid)."""
    if not date or not isinstance(date, dict):
        return 0
    sortval = date.get("sortval")
    if sortval:
        try:
            return int(sortval)
        except (TypeError, ValueError):
            pass
    ymd = get_ymd(date)
    if ymd is None or get_modifier(date) == MOD_TEXTONLY:
        return 0
    return gregorian_sdn(*ymd)


def is_valid(date):
    """A date is valid when it has a date value and is not text-only."""
    return get_sortval(date) != 0 and get_modifier(date) != MOD_TEXTONLY


def make_date(year, month=0, day=0, modifier=MOD_NONE):
    """Build a Gramps date dict from components."""
    return {
        "_class": "Date",
        "calendar": 0,
        "dateval": [day, month, year, False],
        "modifier": modifier,
        "quality": 0,
        "sortval": gregorian_sdn(year, month, day),
        "text": "",
        "newyear": 0,
        "format": None,
    }


def parse_ymd_string(value):
    """Parse 'y/m/d' into a date dict (None on error)."""
    try:
        year, month, day = value.split("/")
        return make_date(int(year), int(month), int(day))
    except (ValueError, AttributeError):
        return None


def add_years(date, years):
    """Return a copy of ``date`` shifted by a number of years."""
    ymd = get_ymd(date)
    if ymd is None:
        return None
    year, month, day = ymd
    return make_date(year + years, month, day)


def is_before(date, other):
    """True if ``date`` is strictly before ``other`` (by sort value)."""
    return get_sortval(date) < get_sortval(other)


def is_after(date, other):
    """True if ``date`` is strictly after ``other`` (by sort value)."""
    return get_sortval(date) > get_sortval(other)


def diff_ymd(later, earlier):
    """
    Difference ``later - earlier`` as (years, months, days).

    Both arguments are (year, month, day) tuples; missing parts count as 1,
    mirroring ``Span._diff`` in Gramps.
    """
    ymd1 = [v or 1 for v in later]
    ymd2 = [v or 1 for v in earlier]
    if ymd2[2] > ymd1[2]:
        if ymd2[1] > ymd1[1]:
            ymd1[0] -= 1
            ymd1[1] += 12
        ymd1[1] -= 1
        ymd1[2] += 31
    if ymd2[1] > ymd1[1]:
        ymd1[0] -= 1
        ymd1[1] += 12
    days = ymd1[2] - ymd2[2]
    months = ymd1[1] - ymd2[1]
    years = ymd1[0] - ymd2[0]
    if days > 31:
        months += days // 31
        days = days % 31
    if months > 12:
        years += months // 12
        months = months % 12
    return (years, months, days)


def _plural(table, key, number):
    singular, plural = table[key]
    form = singular if abs(number) == 1 else plural
    return form.format(n=number)


def format_diff(diff, precision=1, lang="en"):
    """Format a (years, months, days) tuple like ``Span._format``."""
    table = words(lang)
    if diff == (-1, -1, -1):
        return table["unknown"]
    years, months, days = diff
    parts = []
    if years != 0:
        parts.append(_plural(table, "year", years))
    if precision == len(parts):
        return ", ".join(parts)
    if months != 0:
        parts.append(_plural(table, "month", months))
    if precision == len(parts):
        return ", ".join(parts)
    if days != 0:
        parts.append(_plural(table, "day", days))
    if parts:
        return ", ".join(parts)
    return table["0 days"]


def span_string(start_date, end_date, precision=1, lang="en"):
    """
    Return the age string between two Gramps dates, following
    ``Span.format(precision).strip("()")`` of Gramps.

    Returns "" when either date is not usable.
    """
    if not is_valid(start_date) or not is_valid(end_date):
        return ""
    table = words(lang)
    mod1 = get_modifier(start_date)
    mod2 = get_modifier(end_date)
    ymd1 = get_ymd(start_date)
    ymd2 = get_ymd(end_date)
    if ymd1 is None or ymd2 is None:
        return ""
    # Span orders the two dates so that date1 is the later one
    negative = get_sortval(start_date) > get_sortval(end_date)
    if negative:
        later, earlier, mod_later, mod_earlier = ymd1, ymd2, mod1, mod2
        later_date, earlier_date = start_date, end_date
    else:
        later, earlier, mod_later, mod_earlier = ymd2, ymd1, mod2, mod1
        later_date, earlier_date = end_date, start_date
    diff = diff_ymd(later, earlier)
    if negative:
        diff = tuple(-v for v in diff)
    if diff[0] > ALIVE_LIMIT:
        return "%s %s" % (table["greater than"], _plural(table, "year", ALIVE_LIMIT))
    text = format_diff(diff, precision, lang)
    if mod_later == MOD_NONE:
        if mod_earlier == MOD_NONE:
            return text
        if mod_earlier in (MOD_BEFORE, MOD_TO):
            return "%s %s" % (table["more than"], text)
        if mod_earlier in (MOD_AFTER, MOD_FROM):
            return "%s %s" % (table["less than"], text)
        if mod_earlier == MOD_ABOUT:
            return "%s %s" % (table["about"], format_diff(diff, 1, lang))
        if mod_earlier in (MOD_RANGE, MOD_SPAN):
            stop = get_stop_ymd(earlier_date)
            if stop:
                return "%s %s %s %s" % (
                    table["between"],
                    format_diff(diff_ymd(later, stop), precision, lang),
                    table["and"],
                    text,
                )
            return text
    elif mod_later in (MOD_BEFORE, MOD_TO):
        if mod_earlier in (MOD_BEFORE, MOD_TO):
            return table["unknown"]
        if mod_earlier == MOD_ABOUT:
            return "%s %s" % (table["less than"], text)
        return "%s %s" % (table["less than"], text)
    elif mod_later in (MOD_AFTER, MOD_FROM):
        if mod_earlier in (MOD_AFTER, MOD_FROM):
            return table["unknown"]
        if mod_earlier == MOD_ABOUT:
            return "%s %s" % (table["more than"], text)
        return "%s %s" % (table["more than"], text)
    elif mod_later == MOD_ABOUT:
        if mod_earlier in (MOD_BEFORE, MOD_TO):
            return "%s %s" % (table["more than"], text)
        if mod_earlier in (MOD_AFTER, MOD_FROM):
            return "%s %s" % (table["less than"], text)
        return "%s %s" % (table["about"], format_diff(diff, 1, lang))
    elif mod_later in (MOD_RANGE, MOD_SPAN):
        stop = get_stop_ymd(later_date)
        if stop and mod_earlier == MOD_NONE:
            return "%s %s %s %s" % (
                table["between"],
                text,
                table["and"],
                format_diff(diff_ymd(stop, earlier), precision, lang),
            )
        return "%s %s" % (table["about"], text)
    return text


def _format_ymd(year, month, day):
    return _format_date({"dateval": [day, month, year, False]})


def display_date(date, lang="en"):
    """
    Display a Gramps date as text (ISO style, like the Gramps default
    date format), honouring modifiers such as "about" or "between".
    """
    if not date or not isinstance(date, dict):
        return ""
    text = date.get("text") or ""
    modifier = get_modifier(date)
    if modifier == MOD_TEXTONLY:
        return text
    ymd = get_ymd(date)
    if ymd is None:
        return text
    table = words(lang)
    start = _format_ymd(*ymd)
    if modifier in (MOD_RANGE, MOD_SPAN):
        stop = get_stop_ymd(date)
        stop_text = _format_ymd(*stop) if stop else ""
        if modifier == MOD_RANGE:
            if lang[:2] == "fi":
                return "%s %s %s %s" % (start, table["and"], stop_text, table["between"])
            return "%s %s %s %s" % (table["between"], start, table["and"], stop_text)
        if lang[:2] == "fi":
            return "%s %s %s %s" % (start, table["from"], stop_text, table["to"])
        return "%s %s %s %s" % (table["from"], start, table["to"], stop_text)
    finnish = (lang or "en")[:2] == "fi"
    if modifier == MOD_BEFORE:
        return "%s %s" % (table["before"], start)
    if modifier == MOD_AFTER:
        return "%s %s" % (start, table["after"]) if finnish else "%s %s" % (table["after"], start)
    if modifier == MOD_ABOUT:
        return "%s %s" % (table["about"], start)
    if modifier == MOD_FROM:
        return "%s %s" % (start, table["from"]) if finnish else "%s %s" % (table["from"], start)
    if modifier == MOD_TO:
        return "%s %s" % (start, table["to"]) if finnish else "%s %s" % (table["to"], start)
    return start
