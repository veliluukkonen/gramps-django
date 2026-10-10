"""Tests for apps.core.dates (date parsing, matching and the dates parameter)."""

from django.test import SimpleTestCase, TestCase

from apps.core.dates import (
    MOD_ABOUT,
    MOD_AFTER,
    MOD_BEFORE,
    MOD_RANGE,
    MOD_SPAN,
    MOD_TEXTONLY,
    QUAL_ESTIMATED,
    dates_match,
    get_start_stop_range,
    is_valid_date,
    make_date,
    match_dates,
    parse_date,
    parse_dates_param,
)
from apps.core.models import Event


class ParseDateTests(SimpleTestCase):
    def test_single_dates(self):
        self.assertEqual(parse_date("1900")["dateval"], [0, 0, 1900, False])
        self.assertEqual(parse_date("1900-05-02")["dateval"], [2, 5, 1900, False])
        self.assertEqual(parse_date("1900-05")["dateval"], [0, 5, 1900, False])
        self.assertEqual(parse_date("2.5.1900")["dateval"], [2, 5, 1900, False])
        self.assertEqual(parse_date("5/2/1900")["dateval"], [2, 5, 1900, False])
        self.assertEqual(parse_date("2 May 1900")["dateval"], [2, 5, 1900, False])
        self.assertEqual(parse_date("May 2, 1900")["dateval"], [2, 5, 1900, False])
        self.assertEqual(parse_date("May 1900")["dateval"], [0, 5, 1900, False])
        self.assertEqual(parse_date("2. toukokuuta 1900")["dateval"], [2, 5, 1900, False])
        self.assertEqual(parse_date("1900")["sortval"], 1900 * 512)

    def test_modifiers_and_quality(self):
        self.assertEqual(parse_date("before 1900")["modifier"], MOD_BEFORE)
        self.assertEqual(parse_date("after 1900")["modifier"], MOD_AFTER)
        self.assertEqual(parse_date("about 1900")["modifier"], MOD_ABOUT)
        self.assertEqual(parse_date("abt. 1900")["modifier"], MOD_ABOUT)
        self.assertEqual(parse_date("ennen 1900")["modifier"], MOD_BEFORE)
        self.assertEqual(parse_date("1900 jälkeen")["modifier"], MOD_AFTER)
        estimated = parse_date("estimated about 1900")
        self.assertEqual(estimated["quality"], QUAL_ESTIMATED)
        self.assertEqual(estimated["modifier"], MOD_ABOUT)

    def test_ranges_and_spans(self):
        date = parse_date("between 1900 and 1950")
        self.assertEqual(date["modifier"], MOD_RANGE)
        self.assertEqual(date["dateval"], [0, 0, 1900, False, 0, 0, 1950, False])
        self.assertEqual(parse_date("bet 1900 and 1950")["modifier"], MOD_RANGE)
        date = parse_date("from 1900 until 1950")
        self.assertEqual(date["modifier"], MOD_SPAN)
        self.assertEqual(date["dateval"], [0, 0, 1900, False, 0, 0, 1950, False])
        self.assertEqual(parse_date("from 1900-01-01 to 1950-12-31")["dateval"], [1, 1, 1900, False, 31, 12, 1950, False])
        # Localised strings from the frontend
        for text in ("1900 ja 1950 välillä", "zwischen 1900 und 1950", "mellan 1900 och 1950", "1900 és 1950 között"):
            date = parse_date(text)
            self.assertEqual(date["modifier"], MOD_RANGE, text)
            self.assertEqual(date["dateval"], [0, 0, 1900, False, 0, 0, 1950, False], text)

    def test_text_only_fallback(self):
        date = parse_date("some day in spring")
        self.assertEqual(date["modifier"], MOD_TEXTONLY)
        self.assertEqual(date["text"], "some day in spring")
        self.assertFalse(is_valid_date(date))
        self.assertEqual(parse_date(""), {})
        self.assertEqual(parse_date(None), {})


class StartStopRangeTests(SimpleTestCase):
    def test_plain_dates(self):
        self.assertEqual(get_start_stop_range(make_date((1900, 5, 2))), ((1900, 5, 2), (1900, 5, 2)))
        self.assertEqual(get_start_stop_range(make_date((1900, 0, 0))), ((1900, 1, 1), (1900, 12, 31)))
        self.assertEqual(get_start_stop_range(make_date((1900, 5, 0))), ((1900, 5, 1), (1900, 5, 31)))

    def test_modified_dates(self):
        self.assertEqual(get_start_stop_range(parse_date("before 1900")), ((1849, 12, 31), (1899, 12, 31)))
        self.assertEqual(get_start_stop_range(parse_date("after 1900")), ((1901, 1, 1), (1951, 1, 1)))
        self.assertEqual(get_start_stop_range(parse_date("about 1900")), ((1850, 1, 1), (1950, 12, 31)))
        self.assertEqual(get_start_stop_range(parse_date("between 1900 and 1950")), ((1900, 1, 1), (1950, 12, 31)))
        self.assertEqual(get_start_stop_range(parse_date("estimated 1900")), ((1850, 1, 1), (1950, 12, 31)))

    def test_dates_match(self):
        birth = make_date((1930, 3, 15))
        self.assertTrue(dates_match(birth, parse_date("between 1929 and 1931")))
        self.assertTrue(dates_match(birth, parse_date("1930")))
        self.assertTrue(dates_match(birth, parse_date("1930-03")))
        self.assertFalse(dates_match(birth, parse_date("1930-04")))
        self.assertFalse(dates_match(birth, parse_date("between 1931 and 1940")))
        self.assertTrue(dates_match(birth, parse_date("before 1935")))
        self.assertFalse(dates_match(birth, parse_date("after 1935")))
        self.assertTrue(dates_match(make_date((1932, 0, 0), modifier=MOD_ABOUT), parse_date("1960")))
        self.assertFalse(dates_match({}, parse_date("1930")))
        self.assertFalse(dates_match(birth, parse_date("nonsense")))
        text = make_date(modifier=MOD_TEXTONLY, text="Easter 1930")
        self.assertTrue(dates_match(text, make_date(modifier=MOD_TEXTONLY, text="easter")))
        self.assertTrue(dates_match(birth, make_date((1930, 1, 1)), "<<") is False)
        self.assertTrue(dates_match(birth, make_date((1930, 1, 1)), ">>"))


class DatesParamTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        def event(handle, date):
            return Event.objects.create(handle=handle, gramps_id=handle.upper(), type="Birth", date=date)

        cls.e1900 = event("e1", make_date((1900, 5, 2)))
        cls.e1905 = event("e2", make_date((1905, 0, 0)))
        cls.e1930 = event("e3", make_date((1930, 3, 15)))
        cls.e1932 = event("e4", make_date((1932, 0, 0), modifier=MOD_ABOUT))
        cls.e1960 = event("e5", make_date((1960, 5, 2)))
        cls.e_range = event("e6", make_date((1961, 0, 0), (1963, 0, 0), MOD_RANGE))
        cls.e1970 = event("e7", make_date((1970, 12, 31)))
        cls.e_text = event("e8", {"modifier": MOD_TEXTONLY, "text": "some day", "dateval": [], "sortval": 0})
        cls.e_empty = event("e9", {})

    def handles(self, dates):
        return {e.handle for e in match_dates(Event.objects.all(), dates)}

    def test_parse_dates_param(self):
        self.assertEqual(parse_dates_param("*/5/2"), ("mask", [None, 5, 2]))
        self.assertEqual(parse_dates_param("1930/3/15"), ("mask", [1930, 3, 15]))
        self.assertEqual(parse_dates_param("1930/*/*"), ("mask", [1930, None, None]))
        self.assertEqual(parse_dates_param("-1931/1/1"), ("range", None, (1931, 1, 1)))
        self.assertEqual(parse_dates_param("1960/1/1-"), ("range", (1960, 1, 1), None))
        self.assertEqual(parse_dates_param("1900/1/1-1910/12/31"), ("range", (1900, 1, 1), (1910, 12, 31)))
        for bad in ("", "1930", "*/13/1", "1930/3/32", "a/b/c", "-", "1900/1/1-1910/1/1-1920/1/1", "*/5/2-"):
            with self.assertRaises(ValueError, msg=bad):
                parse_dates_param(bad)

    def test_anniversaries(self):
        self.assertEqual(self.handles("*/5/2"), {"e1", "e5"})
        self.assertEqual(self.handles("*/05/02"), {"e1", "e5"})
        self.assertEqual(self.handles("*/12/31"), {"e7"})
        self.assertEqual(self.handles("*/1/1"), set())

    def test_masks(self):
        self.assertEqual(self.handles("1930/3/15"), {"e3"})
        self.assertEqual(self.handles("1930/*/*"), {"e3"})
        self.assertEqual(self.handles("1905/*/*"), {"e2"})
        self.assertEqual(self.handles("*/*/*"), {"e1", "e2", "e3", "e4", "e5", "e6", "e7"})

    def test_ranges(self):
        self.assertEqual(self.handles("-1931/1/1"), {"e1", "e2", "e3"})
        self.assertEqual(self.handles("1960/1/1-"), {"e5", "e6", "e7"})
        self.assertEqual(self.handles("1900/1/1-1910/12/31"), {"e1", "e2"})
        self.assertEqual(self.handles("1900/5/2-1900/5/2"), {"e1"})
        self.assertEqual(self.handles("1961/1/1-1963/12/31"), {"e6"})
        self.assertEqual(self.handles("1962/1/1-1963/12/31"), set())

    def test_accepts_lists(self):
        events = list(Event.objects.filter(handle__in=["e1", "e3"]))
        self.assertEqual([e.handle for e in match_dates(events, "*/5/2")], ["e1"])
