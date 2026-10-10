"""Tests for apps.core.filters (Gramps rule evaluation)."""

import json

from django.test import TestCase

from apps.core.dates import MOD_ABOUT, MOD_RANGE, make_date
from apps.core.filters import apply_rules
from apps.core.models import (
    Event,
    Family,
    MediaObject,
    Note,
    Person,
    Place,
    Source,
    Tag,
)


def name(first, surname):
    return {
        "first_name": first,
        "surname_list": [{"surname": surname, "prefix": "", "connector": "", "primary": True, "origintype": "Inherited"}],
        "suffix": "", "title": "", "call": "", "nick": "", "famnick": "", "type": "Birth Name",
    }


def event_ref(handle, role="Primary"):
    return {"ref": handle, "role": role, "private": False, "note_list": [], "attribute_list": []}


def child_ref(handle, frel="Birth", mrel="Birth"):
    return {"ref": handle, "frel": frel, "mrel": mrel, "private": False, "note_list": [], "citation_list": []}


class FilterFixtureMixin:
    """Three generations: grandparents -> two children -> one child each."""

    @classmethod
    def setUpTestData(cls):
        cls.tag_blog = Tag.objects.create(handle="tag_blog", name="Blog")
        cls.tag_todo = Tag.objects.create(handle="tag_todo", name="ToDo")

        cls.finland = Place.objects.create(handle="pl_fi", gramps_id="P0001", name={"value": "Finland"}, place_type="Country")
        cls.uusimaa = Place.objects.create(
            handle="pl_uu", gramps_id="P0002", name={"value": "Uusimaa"}, place_type="Region",
            placeref_list=[{"ref": "pl_fi", "date": {}}],
        )
        cls.helsinki = Place.objects.create(
            handle="pl_hki", gramps_id="P0003", name={"value": "Helsinki"}, place_type="City",
            placeref_list=[{"ref": "pl_uu", "date": {}}], lat="60.17", long="24.94",
        )

        def event(handle, gid, etype, date, place=None, description=""):
            return Event.objects.create(
                handle=handle, gramps_id=gid, type=etype, date=date, place=place, description=description,
            )

        cls.ev_gp1_birth = event("ev1", "E0001", "Birth", make_date((1900, 5, 2)), cls.helsinki, "born in town")
        cls.ev_gp1_death = event("ev2", "E0002", "Death", make_date((1970, 12, 31)))
        cls.ev_gp2_birth = event("ev3", "E0003", "Birth", make_date((1905, 0, 0)))
        cls.ev_p1_birth = event("ev4", "E0004", "Birth", make_date((1930, 3, 15)), cls.helsinki)
        cls.ev_p2_birth = event("ev5", "E0005", "Birth", make_date((1932, 0, 0), modifier=MOD_ABOUT))
        cls.ev_c1_birth = event("ev6", "E0006", "Birth", make_date((1960, 5, 2)))
        cls.ev_c2_birth = event("ev7", "E0007", "Birth", make_date((1961, 0, 0), (1963, 0, 0), MOD_RANGE))
        cls.ev_marriage = event("ev8", "E0008", "Marriage", make_date((1925, 6, 1)), cls.helsinki)
        cls.ev_text = event("ev9", "E0009", "Burial", {"modifier": 6, "text": "some day", "dateval": [], "sortval": 0})

        def person(handle, gid, gender, pname, events=(), **kwargs):
            return Person.objects.create(
                handle=handle, gramps_id=gid, gender=gender, primary_name=pname,
                event_ref_list=[event_ref(e) for e in events], **kwargs,
            )

        cls.gp1 = person("gp1", "I0001", Person.MALE, name("Matti", "Virtanen"), ["ev1", "ev2"],
                         birth_ref_index=0, death_ref_index=1, family_list=["fam1"])
        cls.gp2 = person("gp2", "I0002", Person.FEMALE, name("Liisa", "Virtanen"), ["ev3"],
                         birth_ref_index=0, family_list=["fam1"])
        cls.p1 = person("p1", "I0003", Person.MALE, name("Pekka", "Virtanen"), ["ev4", "ev8"],
                        birth_ref_index=0, family_list=["fam2"], parent_family_list=["fam1"],
                        tag_list=["tag_blog"])
        cls.p2 = person("p2", "I0004", Person.FEMALE, name("Anna", "Virtanen"), ["ev5"],
                        birth_ref_index=0, family_list=["fam3"], parent_family_list=["fam1"])
        cls.s1 = person("s1", "I0005", Person.FEMALE, name("Maija", "Korhonen"), [],
                        family_list=["fam2"], private=True)
        cls.c1 = person("c1", "I0006", Person.MALE, {**name("Jukka", "Virtanen"), "nick": "Juki"}, ["ev6"],
                        birth_ref_index=0, parent_family_list=["fam2"],
                        attribute_list=[{"type": "Y-DNA", "value": "R1a"}],
                        person_ref_list=[{"ref": "p2", "rel": "DNA"}],
                        media_list=[{"ref": "m1"}, {"ref": "m2"}], note_list=["n1"],
                        address_list=[{"street": "Main st"}])
        cls.s2 = person("s2", "I0007", Person.MALE, name("Kalle", "Nieminen"), [], family_list=["fam3"])
        cls.c2 = person("c2", "I0008", Person.FEMALE, name("Sari", "Nieminen"), ["ev7"],
                        birth_ref_index=0, parent_family_list=["fam3"], tag_list=["tag_blog", "tag_todo"])
        cls.loner = person("lone", "I0009", Person.UNKNOWN,
                           {"first_name": "Nobody", "surname_list": []}, [])

        cls.fam1 = Family.objects.create(
            handle="fam1", gramps_id="F0001", father_handle=cls.gp1, mother_handle=cls.gp2, type="Married",
            child_ref_list=[child_ref("p1"), child_ref("p2")], event_ref_list=[event_ref("ev8", "Family")],
        )
        cls.fam2 = Family.objects.create(
            handle="fam2", gramps_id="F0002", father_handle=cls.p1, mother_handle=cls.s1, type="Unmarried",
            child_ref_list=[child_ref("c1")],
        )
        cls.fam3 = Family.objects.create(
            handle="fam3", gramps_id="F0003", father_handle=cls.s2, mother_handle=cls.p2, type="Married",
            child_ref_list=[child_ref("c2", frel="Adopted", mrel="Adopted")], tag_list=["tag_todo"],
        )

        cls.media_img = MediaObject.objects.create(
            handle="m1", gramps_id="O0001", mime="image/jpeg", path="a.jpg", desc="Photo",
            attribute_list=[{"type": "map:bounds", "value": "[[1,2],[3,4]]"}],
        )
        cls.media_pdf = MediaObject.objects.create(handle="m2", gramps_id="O0002", mime="application/pdf", path="b.pdf")
        cls.note1 = Note.objects.create(handle="n1", gramps_id="N0001", type="General", text={"string": "Hello world", "tags": []})
        cls.note2 = Note.objects.create(handle="n2", gramps_id="N0002", type="Research", text={"string": "Todo list", "tags": []})
        cls.source_blog = Source.objects.create(handle="src1", gramps_id="S0001", title="Post", tag_list=["tag_blog"],
                                                media_list=[{"ref": "m1"}], reporef_list=[{"ref": "r1"}])
        cls.source_plain = Source.objects.create(handle="src2", gramps_id="S0002", title="Plain")


class PersonRuleTests(FilterFixtureMixin, TestCase):
    def ids(self, rules, **extra):
        if isinstance(rules, dict):
            rules = [rules]
        payload = {"rules": rules, **extra}
        return set(apply_rules(Person.objects.all(), "Person", payload).values_list("gramps_id", flat=True))

    def test_gender_rules(self):
        self.assertEqual(self.ids({"name": "IsFemale"}), {"I0002", "I0004", "I0005", "I0008"})
        self.assertEqual(self.ids({"name": "IsMale"}), {"I0001", "I0003", "I0006", "I0007"})
        self.assertEqual(self.ids({"name": "HasUnknownGender"}), {"I0009"})

    def test_has_tag(self):
        self.assertEqual(self.ids({"name": "HasTag", "values": ["Blog"]}), {"I0003", "I0008"})
        self.assertEqual(self.ids({"name": "HasTag", "values": ["todo"]}), set())
        self.assertEqual(self.ids({"name": "HasTag", "values": ["^to", ], "regex": True}), {"I0008"})

    def test_has_id_of(self):
        self.assertEqual(self.ids({"name": "HasIdOf", "values": ["I0003"]}), {"I0003"})
        self.assertEqual(self.ids({"name": "HasIdOf", "values": ["I000[12]$"], "regex": True}), {"I0001", "I0002"})
        self.assertEqual(self.ids({"name": "HasIdOf", "values": ["nope"]}), set())

    def test_count_rules(self):
        self.assertEqual(self.ids({"name": "HavePhotos", "values": ["1", "greater than"]}), {"I0006"})
        self.assertEqual(self.ids({"name": "HavePhotos", "values": ["2", "equal to"]}), {"I0006"})
        self.assertEqual(len(self.ids({"name": "HavePhotos", "values": ["1", "less than"]})), 8)
        self.assertEqual(self.ids({"name": "HasNote"}), {"I0006"})  # default: 0 greater than
        self.assertEqual(self.ids({"name": "HasAddress", "values": ["0", "greater than"]}), {"I0006"})
        self.assertEqual(self.ids({"name": "HasAssociation", "values": ["0", "greater than"]}), {"I0006"})
        with self.assertRaises(ValueError):
            self.ids({"name": "HavePhotos", "values": ["many", "greater than"]})
        with self.assertRaises(ValueError):
            self.ids({"name": "HavePhotos", "values": ["1", "at least"]})

    def test_has_birth_date_range(self):
        # "about 1932" (I0004) is widened by 50 years, so it overlaps everything nearby
        self.assertEqual(self.ids({"name": "HasBirth", "values": ["between 1929 and 1931", "", ""]}), {"I0003", "I0004"})
        self.assertEqual(
            self.ids({"name": "HasBirth", "values": ["from 1960 until 1965", "", ""]}),
            {"I0004", "I0006", "I0008"},
        )
        # Finnish localised span string as sent by the frontend
        self.assertEqual(self.ids({"name": "HasBirth", "values": ["1899 ja 1906 välillä", "", ""]}), {"I0001", "I0002", "I0004"})
        self.assertEqual(self.ids({"name": "HasBirth", "values": ["before 1901", "", ""]}), {"I0001", "I0004"})
        self.assertEqual(self.ids({"name": "HasBirth", "values": ["before 1801", "", ""]}), set())
        self.assertEqual(self.ids({"name": "HasBirth", "values": ["1900-05-02", "", ""]}), {"I0001", "I0004"})
        self.assertEqual(self.ids({"name": "HasBirth", "values": ["1900-05-03", "", ""]}), {"I0004"})

    def test_has_birth_place_and_description(self):
        self.assertEqual(self.ids({"name": "HasBirth", "values": ["", "Helsinki", ""]}), {"I0001", "I0003"})
        self.assertEqual(self.ids({"name": "HasBirth", "values": ["", "Finland", ""]}), {"I0001", "I0003"})
        self.assertEqual(self.ids({"name": "HasBirth", "values": ["", "", "town"]}), {"I0001"})
        self.assertEqual(self.ids({"name": "HasBirth", "values": ["", "", "^born"], "regex": True}), {"I0001"})
        self.assertEqual(self.ids({"name": "HasDeath", "values": ["1970", "", ""]}), {"I0001"})

    def test_family_related_rules(self):
        self.assertEqual(self.ids({"name": "HaveAltFamilies"}), {"I0008"})
        self.assertEqual(self.ids({"name": "HaveChildren"}), {"I0001", "I0002", "I0003", "I0004", "I0005", "I0007"})
        self.assertEqual(self.ids({"name": "NeverMarried"}), {"I0006", "I0008", "I0009"})
        self.assertEqual(self.ids({"name": "MultipleMarriages"}), set())
        self.assertEqual(self.ids({"name": "Disconnected"}), {"I0009"})
        self.assertEqual(self.ids({"name": "MissingParent"}), {"I0001", "I0002", "I0005", "I0007", "I0009"})
        self.assertEqual(self.ids({"name": "HasRelationship", "values": ["1", "Unmarried", "1"]}), {"I0003", "I0005"})

    def test_misc_person_rules(self):
        self.assertEqual(self.ids({"name": "HasNickname"}), {"I0006"})
        self.assertEqual(self.ids({"name": "IncompleteNames"}), {"I0009"})
        self.assertEqual(self.ids({"name": "PeoplePrivate"}), {"I0005"})
        self.assertEqual(len(self.ids({"name": "PeoplePublic"})), 8)
        self.assertEqual(self.ids({"name": "NoBirthdate"}), {"I0005", "I0007", "I0009"})
        self.assertEqual(len(self.ids({"name": "NoDeathdate"})), 8)
        self.assertEqual(self.ids({"name": "HasAttribute", "values": ["Y-DNA", "*"], "regex": True}), {"I0006"})
        self.assertEqual(self.ids({"name": "HasAttribute", "values": ["Y-DNA", "r1"]}), {"I0006"})
        self.assertEqual(self.ids({"name": "HasAttribute", "values": ["Y-DNA", "R1b"]}), set())
        self.assertEqual(self.ids({"name": "HasAssociationType", "values": ["DNA"]}), {"I0006"})
        self.assertEqual(self.ids({"name": "HasNameOf", "values": ["", "Niem"]}), {"I0007", "I0008"})
        self.assertEqual(self.ids({"name": "HasNameOf", "values": ["^ma", ""], "regex": True}), {"I0001", "I0005"})
        self.assertEqual(self.ids({"name": "HasEvent", "values": ["Marriage", "1925", "Helsinki", ""]}), {"I0003"})
        # Events with missing place or date
        self.assertEqual(self.ids({"name": "PersonWithIncompleteEvent"}), {"I0001", "I0002", "I0004", "I0006", "I0008"})
        self.assertEqual(self.ids({"name": "FamilyWithIncompleteEvent"}), set())

    def test_ancestor_generations(self):
        rule = {"name": "IsLessThanNthGenerationAncestorOf", "values": ["I0006", 2]}
        self.assertEqual(self.ids(rule), {"I0006", "I0003", "I0005"})
        rule = {"name": "IsLessThanNthGenerationAncestorOf", "values": ["I0006", "3"]}
        self.assertEqual(self.ids(rule), {"I0006", "I0003", "I0005", "I0001", "I0002"})
        self.assertEqual(self.ids({"name": "IsAncestorOf", "values": ["I0006", "0"]}), {"I0003", "I0005", "I0001", "I0002"})
        self.assertEqual(self.ids({"name": "IsAncestorOf", "values": ["I0006", "1"]}), {"I0006", "I0003", "I0005", "I0001", "I0002"})

    def test_descendant_generations(self):
        rule = {"name": "IsLessThanNthGenerationDescendantOf", "values": ["I0001", 1]}
        self.assertEqual(self.ids(rule), {"I0003", "I0004"})
        rule = {"name": "IsLessThanNthGenerationDescendantOf", "values": ["I0001", 2]}
        self.assertEqual(self.ids(rule), {"I0003", "I0004", "I0006", "I0008"})
        self.assertEqual(self.ids({"name": "IsDescendantOf", "values": ["I0003", "0"]}), {"I0006"})
        self.assertEqual(self.ids({"name": "IsDescendantOf", "values": ["I0003"]}), {"I0003", "I0006"})
        self.assertEqual(self.ids({"name": "IsDescendantOf", "values": ["XXXX"]}), set())

    def test_tree_chart_query(self):
        """The rule combination sent by the tree chart view."""
        rules = {
            "function": "or",
            "rules": [
                {"name": "IsLessThanNthGenerationAncestorOf", "values": ["I0003", 2]},
                {"name": "IsLessThanNthGenerationDescendantOf", "values": ["I0003", 2]},
            ],
        }
        self.assertEqual(self.ids(**rules), {"I0003", "I0001", "I0002", "I0006"})

    def test_degrees_of_separation(self):
        self.assertEqual(self.ids({"name": "DegreesOfSeparation", "values": ["I0006", 0]}), {"I0006"})
        self.assertEqual(self.ids({"name": "DegreesOfSeparation", "values": ["I0006", 1]}), {"I0006", "I0003", "I0005"})
        self.assertEqual(
            self.ids({"name": "DegreesOfSeparation", "values": ["I0006", 2]}),
            {"I0006", "I0003", "I0005", "I0001", "I0002"},
        )
        self.assertEqual(
            self.ids({"name": "DegreesOfSeparation", "values": ["I0006", 3]}),
            {"I0006", "I0003", "I0005", "I0001", "I0002", "I0004"},
        )

    def test_relationship_path_between(self):
        rule = {"name": "RelationshipPathBetween", "values": ["I0006", "I0008"]}
        self.assertEqual(self.ids(rule), {"I0006", "I0003", "I0001", "I0002", "I0004", "I0008"})
        rule = {"name": "RelationshipPathBetween", "values": ["I0006", "I0001"]}
        self.assertEqual(self.ids(rule), {"I0006", "I0003", "I0001"})
        rule = {"name": "RelationshipPathBetween", "values": ["I0006", "I0009"]}
        self.assertEqual(self.ids(rule), {"I0006", "I0009"})

    def test_function_or_one_and_invert(self):
        rules = [{"name": "IsMale"}, {"name": "HasTag", "values": ["Blog"]}]
        self.assertEqual(self.ids(rules, function="and"), {"I0003"})
        self.assertEqual(self.ids(rules, function="or"), {"I0001", "I0003", "I0006", "I0007", "I0008"})
        self.assertEqual(self.ids(rules, function="one"), {"I0001", "I0006", "I0007", "I0008"})
        self.assertEqual(self.ids({"name": "IsFemale"}, invert=True), {"I0001", "I0003", "I0006", "I0007", "I0009"})
        self.assertEqual(self.ids(rules, function="or", invert=True), {"I0002", "I0004", "I0005", "I0009"})

    def test_result_is_sortable_queryset(self):
        rules = json.dumps({"rules": [{"name": "HasTag", "values": ["Blog"]}]})
        queryset = apply_rules(Person.objects.all(), "Person", rules)
        self.assertEqual(list(queryset.order_by("-gramps_id").values_list("gramps_id", flat=True)), ["I0008", "I0003"])
        self.assertEqual(queryset.count(), 2)

    def test_errors(self):
        with self.assertRaisesRegex(ValueError, "Unknown filter rule"):
            self.ids({"name": "NoSuchRule"})
        with self.assertRaisesRegex(ValueError, "Unknown filter function"):
            self.ids({"name": "IsMale"}, function="xor")
        with self.assertRaisesRegex(ValueError, "Invalid rules JSON"):
            apply_rules(Person.objects.all(), "Person", "{not json")
        with self.assertRaisesRegex(ValueError, "non-empty"):
            apply_rules(Person.objects.all(), "Person", {"rules": []})
        with self.assertRaisesRegex(ValueError, "Invalid regular expression"):
            self.ids({"name": "HasIdOf", "values": ["I00(" ], "regex": True})
        with self.assertRaisesRegex(ValueError, "Unknown object class"):
            apply_rules(Person.objects.all(), "Monster", {"rules": [{"name": "IsMale"}]})


class OtherObjectRuleTests(FilterFixtureMixin, TestCase):
    def ids(self, queryset, class_name, rules, **extra):
        if isinstance(rules, dict):
            rules = [rules]
        return set(apply_rules(queryset, class_name, {"rules": rules, **extra}).values_list("gramps_id", flat=True))

    def test_family_rules(self):
        fams = Family.objects.all()
        self.assertEqual(self.ids(fams, "Family", {"name": "HasRelType", "values": ["Unmarried"]}), {"F0002"})
        self.assertEqual(self.ids(fams, "Family", {"name": "HasRelType", "values": ["^un"], "regex": True}), {"F0002"})
        self.assertEqual(self.ids(fams, "Family", {"name": "HasTag", "values": ["ToDo"]}), {"F0003"})
        self.assertEqual(self.ids(fams, "Family", {"name": "HasIdOf", "values": ["F0001"]}), {"F0001"})
        self.assertEqual(self.ids(fams, "Family", {"name": "HasEvent", "values": ["Marriage", "between 1920 and 1930", "", ""]}), {"F0001"})
        self.assertEqual(self.ids(fams, "Family", {"name": "HasEvent", "values": ["", "", "Uusimaa", ""]}), {"F0001"})
        self.assertEqual(self.ids(fams, "Family", {"name": "HasEvent", "values": ["Divorce", "", "", ""]}), set())
        self.assertEqual(self.ids(fams, "Family", {"name": "HasGallery", "values": ["0", "greater than"]}), set())
        self.assertEqual(self.ids(fams, "Family", {"name": "FamilyPrivate"}), set())
        self.assertEqual(self.ids(fams, "Family", {"name": "ChildHasIdOf", "values": ["I0006"]}), {"F0002"})

    def test_event_rules(self):
        events = Event.objects.all()
        self.assertEqual(len(self.ids(events, "Event", {"name": "HasType", "values": ["Birth"]})), 6)
        self.assertEqual(self.ids(events, "Event", {"name": "HasType", "values": ["Marriage"]}), {"E0008"})
        self.assertEqual(
            self.ids(events, "Event", {"name": "HasData", "values": ["", "between 1900 and 1910", "", ""]}),
            {"E0001", "E0003", "E0005"},  # E0005 is "about 1932", widened by 50 years
        )
        self.assertEqual(
            self.ids(events, "Event", {"name": "HasData", "values": ["", "between 1800 and 1810", "", ""]}),
            set(),
        )
        self.assertEqual(self.ids(events, "Event", {"name": "HasData", "values": ["Birth", "", "Helsinki", ""]}), {"E0001", "E0004"})
        self.assertEqual(self.ids(events, "Event", {"name": "HasData", "values": ["", "", "", "TOWN"]}), {"E0001"})
        self.assertEqual(self.ids(events, "Event", {"name": "HasData", "values": ["", "some", "", ""]}), {"E0009"})
        self.assertEqual(self.ids(events, "Event", {"name": "HasIdOf", "values": ["E0002"]}), {"E0002"})
        self.assertEqual(self.ids(events, "Event", {"name": "HasSourceCount", "values": ["0", "greater than"]}), set())

    def test_place_rules(self):
        places = Place.objects.all()
        self.assertEqual(self.ids(places, "Place", {"name": "HasData", "values": ["hels", "", ""]}), {"P0003"})
        self.assertEqual(self.ids(places, "Place", {"name": "HasData", "values": ["", "Country", ""]}), {"P0001"})
        self.assertEqual(self.ids(places, "Place", {"name": "HasTitle", "values": ["Helsinki, Uusimaa"]}), {"P0003"})
        self.assertEqual(self.ids(places, "Place", {"name": "HasNoLatOrLon"}), {"P0001", "P0002"})
        self.assertEqual(self.ids(places, "Place", {"name": "HasNote", "values": ["0", "greater than"]}), set())

    def test_source_rules(self):
        sources = Source.objects.all()
        self.assertEqual(self.ids(sources, "Source", {"name": "HasTag", "values": ["Blog"]}), {"S0001"})
        self.assertEqual(self.ids(sources, "Source", {"name": "HasGallery", "values": ["0", "greater than"]}), {"S0001"})
        self.assertEqual(self.ids(sources, "Source", {"name": "HasRepository", "values": ["0", "greater than"]}), {"S0001"})
        rules = [{"name": "HasTag", "values": ["ToDo"]}, {"name": "HasIdOf", "values": ["S0002"]}]
        self.assertEqual(self.ids(sources, "Source", rules), set())
        self.assertEqual(self.ids(sources, "Source", rules, function="or"), {"S0002"})

    def test_media_rules(self):
        media = MediaObject.objects.all()
        self.assertEqual(self.ids(media, "Media", {"name": "HasMedia", "values": ["", "image/", "", ""]}), {"O0001"})
        self.assertEqual(self.ids(media, "Media", {"name": "HasMedia", "values": ["", "application/pdf", "", ""]}), {"O0002"})
        self.assertEqual(self.ids(media, "Media", {"name": "HasAttribute", "values": ["map:bounds", "*"], "regex": True}), {"O0001"})
        self.assertEqual(self.ids(media, "MediaObject", {"name": "HasIdOf", "values": ["O0002"]}), {"O0002"})

    def test_note_rules(self):
        notes = Note.objects.all()
        self.assertEqual(self.ids(notes, "Note", {"name": "HasType", "values": ["Research"]}), {"N0002"})
        self.assertEqual(self.ids(notes, "Note", {"name": "HasNote", "values": ["world", ""]}), {"N0001"})
        self.assertEqual(self.ids(notes, "Note", {"name": "MatchesRegexpOf", "values": ["^todo"]}), {"N0002"})
        rules = {"function": "or", "rules": [{"name": "HasIdOf", "values": ["N0001"]}, {"name": "HasIdOf", "values": ["N0002"]}]}
        self.assertEqual(self.ids(notes, "Note", **rules), {"N0001", "N0002"})

    def test_tag_namespace_has_no_rules(self):
        with self.assertRaises(ValueError):
            apply_rules(Tag.objects.all(), "Tag", {"rules": [{"name": "HasIdOf", "values": ["x"]}]})
