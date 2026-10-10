"""Tests for the types and translations endpoints of apps.special."""

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.core.models import (
    Citation,
    Event,
    Family,
    MediaObject,
    Note,
    Person,
    Place,
    Repository,
    Source,
)

from .translations import translate
from .types import CUSTOM_RECORD_TYPES, DEFAULT_RECORD_TYPES


class TypesDefaultTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_types_root_structure(self):
        resp = self.client.get("/api/types/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(set(data), {"default", "custom"})
        self.assertEqual(list(data["default"]), DEFAULT_RECORD_TYPES)
        self.assertEqual(list(data["custom"]), CUSTOM_RECORD_TYPES)
        self.assertIn("Birth", data["default"]["event_types"])
        self.assertEqual(data["default"]["event_types"][0], "Unknown")
        self.assertEqual(data["default"]["gender_types"], ["Male", "Female", "Unknown"])
        self.assertNotIn("Custom", data["default"]["event_types"])
        for key in CUSTOM_RECORD_TYPES:
            self.assertEqual(data["custom"][key], [])

    @override_settings(GRAMPS_LANGUAGE="fi")
    def test_types_locale_finnish(self):
        plain = self.client.get("/api/types/").json()["default"]
        local = self.client.get("/api/types/?locale=1").json()["default"]
        idx = plain["event_types"].index("Birth")
        self.assertEqual(local["event_types"][idx], "Syntymä")
        self.assertEqual(len(plain["event_types"]), len(local["event_types"]))
        idx = plain["event_role_types"].index("Primary")
        self.assertEqual(local["event_role_types"][idx], "Päähenkilö")
        self.assertEqual(local["gender_types"], ["Mies", "Nainen", "Tuntematon"])

    @override_settings(GRAMPS_LANGUAGE="sv_SE")
    def test_types_locale_region_code(self):
        local = self.client.get("/api/types/default/event_types?locale=true").json()
        self.assertIn("Födelse", local)

    @override_settings(GRAMPS_LANGUAGE="xx")
    def test_types_locale_unknown_language_falls_back_to_english(self):
        local = self.client.get("/api/types/default/event_types?locale=1").json()
        plain = self.client.get("/api/types/default/event_types").json()
        self.assertEqual(len(local), len(plain))
        self.assertEqual(local[plain.index("Birth")], "Birth")
        # English display names are not always identical to the XML names
        self.assertEqual(local[plain.index("Bas Mitzvah")], "Bat Mitzvah")
        self.assertNotIn("Syntymä", local)

    def test_default_types_list(self):
        resp = self.client.get("/api/types/default/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(list(resp.json()), DEFAULT_RECORD_TYPES)

    def test_default_type_detail(self):
        resp = self.client.get("/api/types/default/family_relation_types")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), ["Married", "Unmarried", "Civil Union", "Unknown"])

    def test_default_type_map(self):
        resp = self.client.get("/api/types/default/event_types/map")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["-1"], "Unknown")
        self.assertEqual(data["0"], "Custom")
        self.assertEqual(data["12"], "Birth")
        gender = self.client.get("/api/types/default/gender_types/map").json()
        self.assertEqual(gender, {"1": "Male", "0": "Female", "2": "Unknown"})

    @override_settings(GRAMPS_LANGUAGE="de")
    def test_default_type_map_locale(self):
        data = self.client.get("/api/types/default/event_types/map?locale=1").json()
        self.assertEqual(data["12"], "Geburt")

    def test_unknown_datatype_404(self):
        for url in (
            "/api/types/default/nope",
            "/api/types/default/nope/map",
            "/api/types/custom/nope",
        ):
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 404, url)
            self.assertIn("message", resp.json()["error"])


class TypesCustomTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        Person.objects.create(
            handle="P1",
            gramps_id="I0001",
            primary_name={
                "first_name": "Matti",
                "type": "Patronymic Name",
                "surname_list": [{"surname": "Virtanen", "origintype": "Talonnimi"}],
            },
            alternate_names=[{"first_name": "M", "type": "Birth Name", "surname_list": []}],
            event_ref_list=[{"ref": "E1", "role": "Primary"}, {"ref": "E2", "role": "Kummi"}],
            urls=[{"type": "Web Home", "path": "x"}, {"type": "Geni", "path": "y"}],
            attribute_list=[{"type": "Occupation", "value": "a"}, {"type": "Rippikoulu", "value": "b"}],
            media_list=[{"ref": "M1", "attribute_list": [{"type": "Kuvaaja", "value": "c"}]}],
        )
        Family.objects.create(
            handle="F1",
            gramps_id="F0001",
            type="Kihloissa",
            child_ref_list=[{"ref": "P1", "frel": "Birth", "mrel": "Kasvatti"}],
            event_ref_list=[{"ref": "E3", "role": "Family"}, {"ref": "E3", "role": "Todistaja"}],
            attribute_list=[{"type": "Vihkijä", "value": "d"}],
        )
        Event.objects.create(handle="E1", gramps_id="E0001", type="Birth")
        Event.objects.create(
            handle="E2", gramps_id="E0002", type="Rippikoulu",
            attribute_list=[{"type": "Seurakunta", "value": "e"}],
        )
        Place.objects.create(
            handle="L1", gramps_id="L0001", place_type="Kylä",
            urls=[{"type": "Web Search", "path": "z"}],
        )
        Repository.objects.create(handle="R1", gramps_id="R0001", type="Seurakunta-arkisto")
        Note.objects.create(handle="N1", gramps_id="N0001", type="General")
        Note.objects.create(handle="N2", gramps_id="N0002", type="Muistiinpano")
        Source.objects.create(
            handle="S1", gramps_id="S0001",
            reporef_list=[{"ref": "R1", "media_type": "Mikrofilmi"}],
            attribute_list=[{"type": "Signum", "value": "f"}],
        )
        Citation.objects.create(
            handle="C1", gramps_id="C0001",
            attribute_list=[{"type": "Sivunumero", "value": "g"}],
        )
        MediaObject.objects.create(
            handle="M1", gramps_id="O0001",
            attribute_list=[{"type": "Valokuvaaja", "value": "h"}],
        )

    def test_custom_types(self):
        resp = self.client.get("/api/types/custom/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(list(data), CUSTOM_RECORD_TYPES)
        self.assertEqual(data["event_types"], ["Rippikoulu"])
        self.assertEqual(data["event_attribute_types"], ["Seurakunta"])
        self.assertEqual(data["person_attribute_types"], ["Rippikoulu"])
        self.assertEqual(data["family_attribute_types"], ["Vihkijä"])
        self.assertEqual(data["media_attribute_types"], ["Kuvaaja", "Valokuvaaja"])
        self.assertEqual(data["family_relation_types"], ["Kihloissa"])
        self.assertEqual(data["child_reference_types"], ["Kasvatti"])
        self.assertEqual(data["event_role_types"], ["Kummi", "Todistaja"])
        self.assertEqual(data["name_types"], ["Patronymic Name"])
        self.assertEqual(data["name_origin_types"], ["Talonnimi"])
        self.assertEqual(data["repository_types"], ["Seurakunta-arkisto"])
        self.assertEqual(data["note_types"], ["Muistiinpano"])
        self.assertEqual(data["source_attribute_types"], ["Signum", "Sivunumero"])
        self.assertEqual(data["source_media_types"], ["Mikrofilmi"])
        self.assertEqual(data["url_types"], ["Geni"])
        self.assertEqual(data["place_types"], ["Kylä"])

    def test_custom_type_detail_and_root(self):
        resp = self.client.get("/api/types/custom/event_types")
        self.assertEqual(resp.json(), ["Rippikoulu"])
        root = self.client.get("/api/types/").json()
        self.assertEqual(root["custom"]["place_types"], ["Kylä"])


class TranslationsTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    @override_settings(GRAMPS_LANGUAGE="fi")
    def test_language_list(self):
        resp = self.client.get("/api/translations/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        by_code = {d["language"]: d for d in data}
        for code in ("en", "fi", "sv", "de"):
            self.assertIn(code, by_code)
            self.assertEqual(set(by_code[code]), {"default", "current", "language", "native"})
        self.assertEqual(by_code["fi"]["default"], "Finnish")
        self.assertEqual(by_code["fi"]["native"], "suomi")
        self.assertEqual(by_code["fi"]["current"], "suomi")
        self.assertEqual(by_code["de"]["native"], "Deutsch")
        self.assertEqual(by_code["sv"]["current"], "ruotsi")
        self.assertEqual([d["language"] for d in data], sorted(d["language"] for d in data))

    def test_language_list_sort(self):
        data = self.client.get("/api/translations/?sort=-language").json()
        codes = [d["language"] for d in data]
        self.assertEqual(codes, sorted(codes, reverse=True))
        resp = self.client.get("/api/translations/?sort=bogus")
        self.assertEqual(resp.status_code, 422)
        self.assertIn("message", resp.json()["error"])

    def test_translate_post(self):
        resp = self.client.post(
            "/api/translations/fi",
            {"strings": ["Birth", "Role|Primary", "Nonexistent string xyz", "Ctx|Plain"]},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(),
            [
                {"original": "Birth", "translation": "Syntymä"},
                {"original": "Role|Primary", "translation": "Päähenkilö"},
                {"original": "Nonexistent string xyz", "translation": "Nonexistent string xyz"},
                {"original": "Ctx|Plain", "translation": "Plain"},
            ],
        )

    def test_translate_get(self):
        resp = self.client.get("/api/translations/de", {"strings": '["Birth", "Death"]'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(),
            [
                {"original": "Birth", "translation": "Geburt"},
                {"original": "Death", "translation": "Tod"},
            ],
        )

    def test_translate_region_code_and_unknown_language(self):
        resp = self.client.post("/api/translations/sv_SE", {"strings": ["Birth"]}, format="json")
        self.assertEqual(resp.json()[0]["translation"], "Födelse")
        resp = self.client.post("/api/translations/xx", {"strings": ["Birth"]}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()[0]["translation"], "Birth")
        resp = self.client.post("/api/translations/en", {"strings": ["Role|Primary"]}, format="json")
        self.assertEqual(resp.json()[0]["translation"], "Primary")

    def test_translate_bad_input(self):
        resp = self.client.get("/api/translations/fi", {"strings": "not json"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("message", resp.json()["error"])
        resp = self.client.get("/api/translations/fi")
        self.assertEqual(resp.status_code, 400)
        resp = self.client.post("/api/translations/fi", {"strings": "Birth"}, format="json")
        self.assertEqual(resp.status_code, 400)
        resp = self.client.post("/api/translations/fi", {}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_translate_helper_deferred_context(self):
        self.assertEqual(translate("Role\x04Primary", "fi"), "Päähenkilö")
        self.assertEqual(translate("Nope\x04Primary", "xx"), "Primary")
        self.assertEqual(translate("", "fi"), "")
