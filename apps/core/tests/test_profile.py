"""Tests for the profile parameter: age at death and per-event ages."""

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.analysis.dates import display_date, make_date
from apps.analysis.tests import FixtureMixin
from apps.core.datedisplay import format_ymd
from apps.core.profile import get_family_profile, get_person_profile


@override_settings(GRAMPS_LANGUAGE="en")
class ProfileAgeTests(FixtureMixin, TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_age_at_death(self):
        juho = self.people["juho"]  # 1900-01-15 .. 1970-06-30
        profile = get_person_profile(juho, {"all"})
        self.assertEqual(profile["birth"]["age"], "0 days")
        self.assertEqual(profile["death"]["age"], "70 years, 5 months, 15 days")

    def test_age_only_when_requested(self):
        juho = self.people["juho"]
        profile = get_person_profile(juho, {"self"})
        self.assertNotIn("age", profile["birth"])
        self.assertNotIn("age", profile["death"])
        profile = get_person_profile(juho, {"self", "age"})
        self.assertEqual(profile["death"]["age"], "70 years, 5 months, 15 days")

    def test_living_person_has_no_death_age(self):
        anna = self.people["anna"]  # born 1928, no death event
        profile = get_person_profile(anna, {"all"})
        self.assertEqual(profile["birth"]["age"], "0 days")
        self.assertEqual(profile["death"], {})

    def test_event_ages(self):
        matti = self.people["matti"]  # 1926-05-10 .. 1990-02-14, occupation 1950
        profile = get_person_profile(matti, {"all"})
        ages = {e["type"]: e["age"] for e in profile["events"]}
        self.assertEqual(ages["Birth"], "0 days")
        self.assertEqual(ages["Death"], "63 years, 9 months, 4 days")
        self.assertTrue(ages["Occupation"].startswith("23 years"), ages["Occupation"])

    def test_family_members_get_age(self):
        from apps.core.models import Family

        family = Family.objects.get(pk="F1")
        profile = get_family_profile(family, {"all"})
        self.assertEqual(profile["father"]["death"]["age"], "70 years, 5 months, 15 days")
        children = {c["name_given"]: c for c in profile["children"]}
        self.assertEqual(children["Matti"]["death"]["age"], "63 years, 9 months, 4 days")
        self.assertNotIn("age", children["Liisa"]["death"])
        # Not requested: no age keys
        profile = get_family_profile(family, {"self"})
        self.assertNotIn("age", profile["father"]["death"])

    @override_settings(GRAMPS_LANGUAGE="fi")
    def test_age_is_localized(self):
        profile = get_person_profile(self.people["juho"], {"all"})
        self.assertEqual(profile["birth"]["age"], "0 päivää")
        self.assertEqual(profile["death"]["age"], "70 vuotta, 5 kuukautta, 15 päivää")

    def test_person_api_profile_all(self):
        res = self.client.get("/api/people/juho?profile=all")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["profile"]["death"]["age"], "70 years, 5 months, 15 days")
        children = res.data["profile"]["families"][0]["children"]
        self.assertEqual(children[0]["death"]["age"], "63 years, 9 months, 4 days")

    def test_family_api_profile_all(self):
        res = self.client.get("/api/families/F1?profile=all")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["profile"]["children"][0]["death"]["age"], "63 years, 9 months, 4 days")


class DateFormatTests(TestCase):
    @override_settings(GRAMPS_LANGUAGE="fi", GRAMPS_DATE_FORMAT="")
    def test_finnish_uses_day_month_year(self):
        self.assertEqual(format_ymd(1900, 1, 15), "15.1.1900")
        self.assertEqual(format_ymd(1900, 1, 0), "1.1900")
        self.assertEqual(format_ymd(1900, 0, 0), "1900")
        self.assertEqual(format_ymd(0, 0, 0), "")
        # dual-dated stays ISO like in Gramps
        self.assertEqual(format_ymd(1750, 3, 25, slash=True), "1750-03-25")

    @override_settings(GRAMPS_LANGUAGE="en", GRAMPS_DATE_FORMAT="")
    def test_english_uses_iso(self):
        self.assertEqual(format_ymd(1900, 1, 15), "1900-01-15")
        self.assertEqual(format_ymd(1900, 1, 0), "1900-01")
        self.assertEqual(format_ymd(1900, 0, 0), "1900")

    @override_settings(GRAMPS_LANGUAGE="fi", GRAMPS_DATE_FORMAT="iso")
    def test_setting_overrides_language(self):
        self.assertEqual(format_ymd(1900, 1, 15), "1900-01-15")
        self.assertEqual(format_ymd(1900, 1, 15, lang="fi"), "1900-01-15")

    @override_settings(GRAMPS_DATE_FORMAT="")
    def test_display_date_follows_language_argument(self):
        self.assertEqual(display_date(make_date(1952, 3, 15), "fi"), "15.3.1952")
        self.assertEqual(display_date(make_date(1952, 3, 15, modifier=3), "fi"), "noin 15.3.1952")
        self.assertEqual(display_date(make_date(1952, 3, 15), "en"), "1952-03-15")


@override_settings(GRAMPS_DATE_FORMAT="")
class ProfileDateFormatTests(FixtureMixin, TestCase):
    @override_settings(GRAMPS_LANGUAGE="fi")
    def test_profile_dates_in_finnish(self):
        profile = get_person_profile(self.people["juho"], {"all"})
        self.assertEqual(profile["birth"]["date"], "15.1.1900")
        self.assertEqual(profile["death"]["date"], "30.6.1970")
        matti = get_person_profile(self.people["matti"], {"all"})
        self.assertEqual([e["date"] for e in matti["events"] if e["type"] == "Ammatti"], ["1950"])

    @override_settings(GRAMPS_LANGUAGE="fi")
    def test_profile_strings_are_localized(self):
        matti = get_person_profile(self.people["matti"], {"all"})
        self.assertEqual(matti["birth"]["type"], "Syntymä")
        self.assertEqual([e["type"] for e in matti["events"]], ["Syntymä", "Ammatti", "Kuolema"])
        self.assertEqual(matti["events"][0]["role"], "Päähenkilö")
        self.assertEqual(matti["families"][0]["relationship"], "Avioliitto")
        res = APIClient().get("/api/people/matti?profile=all")
        self.assertEqual(res.data["profile"]["death"]["type"], "Kuolema")
        # Custom types pass through untranslated
        res = APIClient().get("/api/people/juho?profile=all")
        self.assertEqual(res.data["profile"]["birth"]["type"], "Syntymä")

    @override_settings(GRAMPS_LANGUAGE="en")
    def test_profile_strings_in_english(self):
        matti = get_person_profile(self.people["matti"], {"all"})
        self.assertEqual([e["type"] for e in matti["events"]], ["Birth", "Occupation", "Death"])
        self.assertEqual(matti["events"][0]["role"], "Primary")

    @override_settings(GRAMPS_LANGUAGE="en")
    def test_profile_dates_in_english(self):
        profile = get_person_profile(self.people["juho"], {"all"})
        self.assertEqual(profile["birth"]["date"], "1900-01-15")

    @override_settings(GRAMPS_LANGUAGE="fi")
    def test_family_api_dates(self):
        res = APIClient().get("/api/families/F1?profile=all")
        self.assertEqual(res.data["profile"]["marriage"]["date"], "20.6.1925")


@override_settings(GRAMPS_LANGUAGE="en")
class EventParticipantsTests(FixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from apps.core.backlinks import populate_backlinks_for_object

        for person in cls.people.values():
            populate_backlinks_for_object(person, "Person")
        from apps.core.models import Family

        for family in Family.objects.all():
            populate_backlinks_for_object(family, "Family")

    def test_person_event_participants(self):
        birth = self.events["juho_birth"]
        res = APIClient().get(f"/api/events/{birth.handle}?profile=all")
        self.assertEqual(res.status_code, 200)
        participants = res.data["profile"]["participants"]
        self.assertEqual(participants["families"], [])
        self.assertEqual(len(participants["people"]), 1)
        self.assertEqual(participants["people"][0]["role"], "Primary")
        self.assertEqual(participants["people"][0]["person"]["name_given"], "Juho")

    def test_family_event_participants(self):
        marriage = self.events["F1_marriage"]
        res = APIClient().get(f"/api/events/{marriage.handle}?profile=all")
        participants = res.data["profile"]["participants"]
        self.assertEqual(participants["people"], [])
        self.assertEqual(participants["families"][0]["role"], "Family")
        self.assertEqual(participants["families"][0]["family"]["gramps_id"], "F_F1")

    def test_participants_only_when_requested(self):
        birth = self.events["juho_birth"]
        res = APIClient().get(f"/api/events/{birth.handle}?profile=self")
        self.assertNotIn("participants", res.data["profile"])
