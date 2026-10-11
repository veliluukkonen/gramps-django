"""
Numeric date formatting for display strings (profiles, timelines).

Two formats are supported, mirroring Gramps' numeric date formats:

* ``iso``: ``1900-01-15``, ``1900-01``, ``1900``
* ``dmy``: ``15.1.1900``, ``1.1900``, ``1900`` (the Finnish "PP.KK.VVVV")

The format comes from ``settings.GRAMPS_DATE_FORMAT`` when set; otherwise it
follows the language (Finnish uses ``dmy``, everything else ``iso``).
Dual-dated (slash) dates are always shown in ISO form, as Gramps does.
"""

from django.conf import settings

FORMAT_ISO = "iso"
FORMAT_DMY = "dmy"
DMY_LANGUAGES = {"fi"}


def date_format(lang=None):
    """Return ``"iso"`` or ``"dmy"`` for the given language (default: settings)."""
    configured = (getattr(settings, "GRAMPS_DATE_FORMAT", "") or "").strip().lower()
    if configured in (FORMAT_ISO, FORMAT_DMY):
        return configured
    if lang is None:
        lang = getattr(settings, "GRAMPS_LANGUAGE", "") or ""
    return FORMAT_DMY if lang[:2].lower() in DMY_LANGUAGES else FORMAT_ISO


def _iso(year, month, day):
    if not month and not day:
        return str(year)
    if not day:
        return "%s-%02d" % (year, month)
    return "%s-%02d-%02d" % (year, month, day)


def _dmy(year, month, day):
    if not month and not day:
        return str(year)
    if not day:
        return "%d.%s" % (month, year)
    return "%d.%d.%s" % (day, month, year)


def format_ymd(year, month, day, slash=False, lang=None):
    """Format a (possibly partial) date; "" when all parts are zero."""
    year, month, day = int(year or 0), int(month or 0), int(day or 0)
    if not year and not month and not day:
        return ""
    if slash or date_format(lang) == FORMAT_ISO:
        return _iso(year, month, day)
    return _dmy(year, month, day)
