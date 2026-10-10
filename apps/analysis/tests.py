"""Tests for the analysis endpoints: timeline, relations and DNA."""

from django.test import TestCase
from rest_framework.test import APIClient

from apps.core.models import Event, Family, Note, Person, Place

from .cache import TreeCache
from .dates import display_date, make_date, span_string
from .dna import parse_raw_dna_match_string
from .relations import get_one_relationship

MALE = Person.MALE
FEMALE = Person.FEMALE

DNA_RAW = (
    "Chromosome,Start,End,cM,SNPs\n"
    "1,1000000,2000000,12.5,1500\n"
    "2,3000000,4500000,8.1,900\n"
)


def _name(first, surname):
    return {
        "_class": "Name",
        "first_name": first,
        "surname_list": [{"_class": "Surname", "surname": surname, "primary": True}],
        "type": "Birth Name",
    }


class FixtureMixin:
    """
    Three generations (plus one great-grandchild)::

        Juho (M 1900-1970) = Maria (F 1902-1980), m. 1925
          |- Matti (M 1926-1990) = Anna (F 1928), m. 1950
          |    |- Kalle (M 1952) = Tiina (F 1954), m. 1978
          |    |    |- Timo (M 1980)
          |    |- Sari (F 1955)
          |- Liisa (F 1930) = Pekka (M 1929), m. 1955
               |- Ville (M 1958)

    Kalle has a DNA association with Ville (segment data in a note).
    """

    @classmethod
    def setUpTestData(cls):
        cls.place = Place.objects.create(
            handle="PL1", gramps_id="P0001", title="Helsinki",
            name={"value": "Helsinki"}, lat="60.17", long="24.94",
        )
        cls.people = {}
        cls.events = {}
        counter = {"e": 0}

        def event(etype, year, month=0, day=0, place=None, description=""):
            counter["e"] += 1
            handle = "E%03d" % counter["e"]
            return Event.objects.create(
                handle=handle, gramps_id=handle, type=etype,
                date=make_date(year, month, day), place=place, description=description,
            )

        def person(handle, first, surname, gender, birth=None, death=None, extra_events=()):
            refs = []
            birth_index = death_index = -1
            if birth:
                evt = event("Birth", *birth, place=cls.place)
                cls.events[handle + "_birth"] = evt
                refs.append({"_class": "EventRef", "ref": evt.handle, "role": "Primary"})
                birth_index = len(refs) - 1
            for etype, ymd in extra_events:
                evt = event(etype, *ymd)
                cls.events[handle + "_" + etype] = evt
                refs.append({"_class": "EventRef", "ref": evt.handle, "role": "Primary"})
            if death:
                evt = event("Death", *death)
                cls.events[handle + "_death"] = evt
                refs.append({"_class": "EventRef", "ref": evt.handle, "role": "Primary"})
                death_index = len(refs) - 1
            obj = Person.objects.create(
                handle=handle, gramps_id="I_" + handle, gender=gender,
                primary_name=_name(first, surname), event_ref_list=refs,
                birth_ref_index=birth_index, death_ref_index=death_index,
            )
            cls.people[handle] = obj
            return obj

        def family(handle, father, mother, children, marriage=None, ftype="Married"):
            refs = []
            if marriage:
                evt = event("Marriage", *marriage, place=cls.place)
                cls.events[handle + "_marriage"] = evt
                refs.append({"_class": "EventRef", "ref": evt.handle, "role": "Family"})
            fam = Family.objects.create(
                handle=handle, gramps_id="F_" + handle, type=ftype,
                father_handle=father, mother_handle=mother, event_ref_list=refs,
                child_ref_list=[
                    {"_class": "ChildRef", "ref": c.handle, "frel": "Birth", "mrel": "Birth"}
                    for c in children
                ],
            )
            for parent in (father, mother):
                if parent is not None:
                    parent.family_list = list(parent.family_list) + [handle]
                    parent.save()
            for child in children:
                child.parent_family_list = list(child.parent_family_list) + [handle]
                child.save()
            return fam

        juho = person("juho", "Juho", "Virtanen", MALE, (1900, 1, 15), (1970, 6, 30))
        maria = person("maria", "Maria", "Virtanen", FEMALE, (1902, 3, 1), (1980, 12, 24))
        matti = person(
            "matti", "Matti", "Virtanen", MALE, (1926, 5, 10), (1990, 2, 14),
            extra_events=(("Occupation", (1950, 0, 0)),),
        )
        anna = person("anna", "Anna", "Virtanen", FEMALE, (1928, 11, 5))
        liisa = person("liisa", "Liisa", "Korhonen", FEMALE, (1930, 8, 20))
        pekka = person("pekka", "Pekka", "Korhonen", MALE, (1929, 2, 2))
        kalle = person(
            "kalle", "Kalle", "Virtanen", MALE, (1952, 3, 15),
            extra_events=(("Residence", (1975, 1, 1)),),
        )
        tiina = person("tiina", "Tiina", "Virtanen", FEMALE, (1954, 7, 7))
        sari = person("sari", "Sari", "Virtanen", FEMALE, (1955, 9, 1))
        ville = person("ville", "Ville", "Korhonen", MALE, (1958, 1, 1))
        timo = person("timo", "Timo", "Virtanen", MALE, (1980, 5, 5))

        family("F1", juho, maria, [matti, liisa], marriage=(1925, 6, 20))
        family("F2", matti, anna, [kalle, sari], marriage=(1950, 7, 1))
        family("F3", pekka, liisa, [ville], marriage=(1955, 4, 4))
        family("F4", kalle, tiina, [timo], marriage=(1978, 6, 10))

        # Undated event for Kalle (dropped by discard_empty)
        undated = Event.objects.create(handle="E_undated", gramps_id="E_undated", type="Census", date={})
        kalle.refresh_from_db()
        kalle.event_ref_list = list(kalle.event_ref_list) + [
            {"_class": "EventRef", "ref": undated.handle, "role": "Primary"}
        ]

        # DNA association Kalle -> Ville with the segment data in a note
        Note.objects.create(
            handle="N_dna", gramps_id="N0001", type="Association",
            text={"_class": "StyledText", "string": DNA_RAW, "tags": []},
        )
        kalle.person_ref_list = [
            {"_class": "PersonRef", "ref": "ville", "rel": "DNA", "note_list": ["N_dna"], "citation_list": []}
        ]
        kalle.attribute_list = [{"_class": "Attribute", "type": "Y-DNA", "value": "M269+ L21+"}]
        kalle.save()
        for p in cls.people.values():
            p.refresh_from_db()


class RelationTests(FixtureMixin, TestCase):
    def rel(self, h1, h2, lang="en"):
        db = TreeCache()
        return get_one_relationship(db, self.people[h1], self.people[h2], depth=20, lang=lang)

    def test_english_relationships(self):
        cases = {
            ("kalle", "matti"): ("father", 1, 0),
            ("matti", "kalle"): ("son", 0, 1),
            ("kalle", "anna"): ("mother", 1, 0),
            ("juho", "timo"): ("great grandson", 0, 3),
            ("timo", "juho"): ("great grandfather", 3, 0),
            ("timo", "maria"): ("great grandmother", 3, 0),
            ("kalle", "juho"): ("grandfather", 2, 0),
            ("kalle", "sari"): ("sister", 1, 1),
            ("sari", "kalle"): ("brother", 1, 1),
            ("kalle", "ville"): ("first cousin", 2, 2),
            ("ville", "timo"): ("first cousin once removed (down)", 2, 3),
            ("timo", "ville"): ("first cousin once removed (up)", 3, 2),
            ("kalle", "liisa"): ("aunt", 2, 1),
            ("liisa", "kalle"): ("nephew", 1, 2),
            ("matti", "anna"): ("wife", -1, -1),
            ("anna", "matti"): ("husband", -1, -1),
            ("kalle", "pekka"): ("", -1, -1),
            ("kalle", "kalle"): ("", -1, -1),
        }
        for (h1, h2), expected in cases.items():
            with self.subTest(h1=h1, h2=h2):
                self.assertEqual(self.rel(h1, h2), expected)

    def test_finnish_relationships(self):
        cases = {
            ("kalle", "matti"): "isä",
            ("matti", "kalle"): "poika",
            ("kalle", "anna"): "äiti",
            ("juho", "timo"): "pojan pojan poika",
            ("timo", "juho"): "isän isän isä",
            ("timo", "maria"): "isän isän äiti",
            ("kalle", "sari"): "sisar",
            ("sari", "kalle"): "veli",
            ("kalle", "ville"): "serkku",
            ("ville", "timo"): "serkun poika",
            ("timo", "ville"): "isän serkku",
            ("kalle", "liisa"): "isän sisar",
            ("liisa", "kalle"): "veljen poika",
            ("matti", "anna"): "vaimo",
            ("anna", "matti"): "aviomies",
            ("kalle", "pekka"): "",
        }
        for (h1, h2), expected in cases.items():
            with self.subTest(h1=h1, h2=h2):
                self.assertEqual(self.rel(h1, h2, "fi")[0], expected)

    def test_relation_endpoint(self):
        client = APIClient()
        response = client.get("/api/relations/kalle/ville?depth=20&locale=en")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["relationship_string"], "first cousin")
        self.assertEqual(response.json()["distance_common_origin"], 2)
        self.assertEqual(response.json()["distance_common_other"], 2)

        response = client.get("/api/relations/kalle/ville?depth=20&locale=fi")
        self.assertEqual(response.json()["relationship_string"], "serkku")

        response = client.get("/api/relations/kalle/pekka?depth=20&locale=fi")
        self.assertEqual(response.json()["relationship_string"], "")
        self.assertEqual(response.json()["distance_common_origin"], -1)

        response = client.get("/api/relations/kalle/nobody?depth=20")
        self.assertEqual(response.status_code, 404)
        self.assertIn("message", response.json()["error"])

    def test_relations_all_endpoint(self):
        client = APIClient()
        response = client.get("/api/relations/kalle/ville/all?locale=en")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data[0]["relationship_string"], "first cousin")
        self.assertEqual(set(data[0]["common_ancestors"]), {"juho", "maria"})


class DateTests(TestCase):
    def test_span_string(self):
        birth = make_date(1952, 3, 15)
        self.assertEqual(span_string(birth, make_date(1990, 2, 14), 1), "37 years")
        self.assertEqual(span_string(birth, make_date(1990, 2, 14), 3), "37 years, 10 months, 30 days")
        self.assertEqual(span_string(birth, make_date(1953, 3, 15), 1), "1 year")
        self.assertEqual(span_string(birth, make_date(1953, 3, 15), 1, "fi"), "1 vuosi")
        self.assertEqual(span_string(birth, make_date(1990, 2, 14), 1, "fi"), "37 vuotta")
        self.assertEqual(span_string(birth, birth, 1), "0 days")
        self.assertEqual(span_string(birth, make_date(1950, 1, 1), 1), "-2 years")
        self.assertEqual(span_string(birth, make_date(1990, modifier=3), 1), "about 37 years")
        self.assertEqual(span_string(birth, {}, 1), "")

    def test_display_date(self):
        self.assertEqual(display_date(make_date(1952, 3, 15)), "1952-03-15")
        self.assertEqual(display_date(make_date(1952)), "1952")
        self.assertEqual(display_date(make_date(1952, modifier=3)), "about 1952")
        self.assertEqual(display_date(make_date(1952, modifier=3), "fi"), "noin 1952")
        self.assertEqual(display_date({"text": "sometime", "modifier": 6, "dateval": [0, 0, 0, False]}), "sometime")


class TimelineTests(FixtureMixin, TestCase):
    def get(self, url):
        response = APIClient().get(url)
        self.assertEqual(response.status_code, 200, response.content)
        return response

    def test_person_timeline_default(self):
        response = self.get("/api/people/kalle/timeline?locale=en")
        data = response.json()
        self.assertEqual(response["X-Total-Count"], str(len(data)))
        labels = [(item["label"], item["date"], item["age"]) for item in data]
        self.assertEqual(
            labels,
            [
                ("Birth", "1952-03-15", "0 days"),
                ("Birth (Wife)", "1954-07-07", "2 years"),
                ("Birth (Sister)", "1955-09-01", "3 years"),
                ("Residence", "1975-01-01", "22 years"),
                ("Marriage", "1978-06-10", "26 years"),
                ("Birth (Son)", "1980-05-05", "28 years"),
                ("Death (Father)", "1990-02-14", "37 years"),
            ],
        )
        # dates are ascending
        sortvals = [Event.objects.get(pk=item["handle"]).date["sortval"] for item in data]
        self.assertEqual(sortvals, sorted(sortvals))
        # the undated census event is discarded by default
        self.assertNotIn("E_undated", [item["handle"] for item in data])
        # anchor person's own events have no person profile (omit_anchor)
        birth = data[0]
        self.assertEqual(birth["person"], {"relationship": "self"})
        self.assertEqual(birth["type"], "Birth")
        self.assertEqual(birth["role"], "Primary")
        self.assertEqual(birth["place"]["name"], "Helsinki")
        self.assertEqual(birth["place"]["handle"], "PL1")
        self.assertEqual(birth["place"]["lat"], 60.17)
        self.assertEqual(birth["span"], birth["age"])
        self.assertIn("gramps_id", birth)
        self.assertIn("description", birth)
        self.assertIn("media", birth)
        # relative events carry a person profile with name and age
        father_death = data[-1]
        self.assertEqual(father_death["person"]["name_given"], "Matti")
        self.assertEqual(father_death["person"]["name_surname"], "Virtanen")
        self.assertEqual(father_death["person"]["handle"], "matti")
        self.assertEqual(father_death["person"]["relationship"], "father")
        self.assertEqual(father_death["person"]["age"], "63 years")
        self.assertEqual(father_death["role"], "Primary")
        marriage = data[4]
        self.assertEqual(marriage["role"], "Family")

    def test_person_timeline_finnish(self):
        data = self.get("/api/people/kalle/timeline?locale=fi").json()
        labels = [item["label"] for item in data]
        self.assertEqual(
            labels,
            [
                "Syntymä", "Syntymä (Vaimo)", "Syntymä (Sisar)", "Asuinpaikka",
                "Avioliitto", "Syntymä (Poika)", "Kuolema (Isä)",
            ],
        )
        self.assertEqual(data[0]["role"], "Päähenkilö")
        self.assertEqual(data[-1]["age"], "37 vuotta")
        self.assertEqual(data[-1]["person"]["relationship"], "isä")

    def test_person_timeline_default_locale_is_finnish(self):
        with self.settings(GRAMPS_LANGUAGE="fi"):
            data = self.get("/api/people/kalle/timeline").json()
        self.assertEqual(data[0]["label"], "Syntymä")

    def test_person_timeline_options(self):
        # Not filtering by first event: father's birth appears before Kalle's
        data = self.get("/api/people/kalle/timeline?locale=en&first=0").json()
        self.assertEqual(data[0]["label"], "Birth (Father)")
        self.assertEqual(data[0]["age"], "-25 years")
        # ancestors=2 climbs to the grandparents
        data = self.get("/api/people/kalle/timeline?locale=en&ancestors=2").json()
        self.assertIn("Death (Grandfather)", [item["label"] for item in data])
        # relatives filter
        data = self.get("/api/people/kalle/timeline?locale=en&relatives=father").json()
        self.assertEqual(
            [item["label"] for item in data],
            ["Birth", "Residence", "Marriage", "Death (Father)"],
        )
        # event filters: Birth and Death are always included
        data = self.get("/api/people/kalle/timeline?locale=en&events=Residence&relatives=father").json()
        self.assertEqual([item["label"] for item in data], ["Birth", "Residence", "Death (Father)"])
        data = self.get("/api/people/kalle/timeline?locale=en&event_classes=vital,family&relatives=father").json()
        self.assertEqual([item["label"] for item in data], ["Birth", "Marriage", "Death (Father)"])
        # relative_events include the relative's other events
        data = self.get("/api/people/kalle/timeline?locale=en&relatives=father&relative_events=Occupation&first=0").json()
        self.assertIn("Occupation (Father)", [item["label"] for item in data])
        # keeping the anchor's profile
        data = self.get("/api/people/kalle/timeline?locale=en&omit_anchor=0").json()
        self.assertEqual(data[0]["person"]["name_given"], "Kalle")
        # precision
        data = self.get("/api/people/kalle/timeline?locale=en&precision=3").json()
        self.assertEqual(data[-1]["age"], "37 years, 10 months, 30 days")
        # ratings
        data = self.get("/api/people/kalle/timeline?locale=en&ratings=1").json()
        self.assertEqual(data[0]["citations"], 0)
        self.assertEqual(data[0]["confidence"], 0)
        # date range
        data = self.get("/api/people/kalle/timeline?locale=en&dates=1975/1/1-1979/1/1").json()
        self.assertEqual([item["label"] for item in data], ["Residence", "Marriage"])
        # keys filter
        data = self.get("/api/people/kalle/timeline?locale=en&keys=label,date").json()
        self.assertEqual(set(data[0].keys()), {"label", "date"})

    def test_person_timeline_pagination(self):
        response = self.get("/api/people/kalle/timeline?locale=en&page=2&pagesize=3")
        data = response.json()
        self.assertEqual(response["X-Total-Count"], "7")
        self.assertEqual([item["label"] for item in data], ["Residence", "Marriage", "Birth (Son)"])

    def test_person_timeline_errors(self):
        client = APIClient()
        response = client.get("/api/people/nobody/timeline")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": {"message": "Person nobody not found"}})
        response = client.get("/api/people/kalle/timeline?events=Bogus")
        self.assertEqual(response.status_code, 422)
        response = client.get("/api/people/kalle/timeline?relatives=cousin")
        self.assertEqual(response.status_code, 422)

    def test_family_timeline(self):
        data = self.get("/api/families/F2/timeline?locale=en").json()
        labels = [(item["label"], item["person"].get("name_given")) for item in data]
        self.assertEqual(
            labels,
            [
                ("Birth", "Matti"),
                ("Birth", "Anna"),
                ("Occupation", "Matti"),
                ("Marriage", "Matti"),
                ("Birth", "Kalle"),
                ("Birth", "Sari"),
                ("Residence", "Kalle"),
                ("Marriage", "Kalle"),
                ("Death", "Matti"),
            ],
        )
        # ages are relative to each person's own birth
        self.assertEqual(data[-1]["age"], "63 years")
        self.assertEqual(data[-1]["person"]["age"], "63 years")
        self.assertEqual(APIClient().get("/api/families/nope/timeline").status_code, 404)

    def test_timelines_people_and_families(self):
        data = self.get("/api/timelines/people/?handles=juho,maria&locale=en").json()
        self.assertEqual(
            [(item["label"], item["date"]) for item in data],
            [
                ("Birth", "1900-01-15"),
                ("Birth", "1902-03-01"),
                ("Marriage", "1925-06-20"),
                ("Death", "1970-06-30"),
                ("Death", "1980-12-24"),
            ],
        )
        data = self.get("/api/timelines/people/?anchor=kalle&handles=juho&locale=en").json()
        self.assertIn("Death (Grandfather)", [item["label"] for item in data])
        data = self.get("/api/timelines/families/?handles=F1&locale=fi").json()
        self.assertEqual(data[2]["label"], "Avioliitto")


class DnaTests(FixtureMixin, TestCase):
    def test_parse_raw_dna_match_string(self):
        segments = parse_raw_dna_match_string(DNA_RAW)
        self.assertEqual(
            segments,
            [
                {"chromosome": "1", "start": 1000000, "stop": 2000000, "side": "U",
                 "cM": 12.5, "SNPs": 1500, "comment": ""},
                {"chromosome": "2", "start": 3000000, "stop": 4500000, "side": "U",
                 "cM": 8.1, "SNPs": 900, "comment": ""},
            ],
        )
        # tab separated without header, with side column (Gramplet format)
        raw = "3\t100\t200\t5,5\t400\tM\tcomment here\n4\t300\t400\t7.25\t500\tP\n"
        segments = parse_raw_dna_match_string(raw)
        self.assertEqual(segments[0]["side"], "M")
        self.assertEqual(segments[0]["cM"], 5.5)
        self.assertEqual(segments[0]["comment"], "comment here")
        self.assertEqual(segments[1]["side"], "P")
        # semicolon separated with header, side column
        raw = "Chr;Start;Stop;cM;SNPs;Side\n5;1;2;3.0;4;p\n"
        self.assertEqual(parse_raw_dna_match_string(raw)[0]["side"], "P")
        self.assertEqual(parse_raw_dna_match_string("garbage"), [])
        self.assertEqual(parse_raw_dna_match_string(""), [])

    def test_parser_endpoint(self):
        client = APIClient()
        response = client.post("/api/parsers/dna-match", {"string": DNA_RAW}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 2)
        self.assertEqual(response.json()[0]["chromosome"], "1")
        response = client.post("/api/parsers/dna-match", DNA_RAW, content_type="text/plain")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 2)
        response = client.post("/api/parsers/dna-match", {}, format="json")
        self.assertEqual(response.status_code, 422)

    def test_dna_matches_endpoint(self):
        client = APIClient()
        response = client.get("/api/people/kalle/dna/matches?locale=en&raw=1")
        self.assertEqual(response.status_code, 200)
        matches = response.json()
        self.assertEqual(len(matches), 1)
        match = matches[0]
        self.assertEqual(match["handle"], "ville")
        self.assertEqual(match["relation"], "first cousin")
        self.assertEqual(set(match["ancestor_handles"]), {"juho", "maria"})
        self.assertEqual(
            {p["name_given"] for p in match["ancestor_profiles"]}, {"Juho", "Maria"}
        )
        self.assertEqual(match["person_ref_idx"], 0)
        self.assertEqual(match["note_handles"], ["N_dna"])
        self.assertEqual(match["raw_data"], [DNA_RAW])
        self.assertEqual(len(match["segments"]), 2)
        # the match is on the paternal side (via Kalle's father)
        self.assertEqual({s["side"] for s in match["segments"]}, {"P"})
        self.assertEqual(match["segments"][0]["cM"], 12.5)

        response = client.get("/api/people/kalle/dna/matches?locale=fi")
        self.assertEqual(response.json()[0]["relation"], "serkku")
        self.assertNotIn("raw_data", response.json()[0])

        self.assertEqual(client.get("/api/people/ville/dna/matches").json(), [])
        self.assertEqual(client.get("/api/people/nobody/dna/matches").status_code, 404)

    def test_ydna_endpoint(self):
        client = APIClient()
        response = client.get("/api/people/kalle/ydna?raw=1")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["raw_data"], "M269+ L21+")
        self.assertIn("clade_lineage", data)
        self.assertIn("tree_version", data)
        self.assertEqual(client.get("/api/people/ville/ydna").json(), {})
        self.assertEqual(client.get("/api/people/nobody/ydna").status_code, 404)
