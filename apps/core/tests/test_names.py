"""Tests for apps.core.names (Name normalization on read and write)."""

from django.test import TestCase

from apps.core.models import Event, Person
from apps.core.names import normalize_name, normalize_name_list
from apps.core.serializers import PersonSerializer
from apps.core.writes import run_transaction


class NormalizeNameTests(TestCase):
    def test_name_without_surname_gets_empty_surname_list(self):
        name = normalize_name({"first_name": "Anna Brita", "suffix": "Ollintytär"})
        self.assertEqual(name["surname_list"], [])
        self.assertEqual(name["first_name"], "Anna Brita")
        self.assertEqual(name["suffix"], "Ollintytär")
        self.assertEqual(name["title"], "")
        self.assertEqual(name["call"], "")
        self.assertEqual(name["citation_list"], [])
        self.assertEqual(name["note_list"], [])
        self.assertEqual(name["_class"], "Name")

    def test_existing_values_are_kept(self):
        name = normalize_name({"type": "Syntymänimi", "surname_list": [{"surname": "Vaarala"}]})
        self.assertEqual(name["type"], "Syntymänimi")
        self.assertEqual(name["surname_list"][0]["surname"], "Vaarala")
        self.assertEqual(name["surname_list"][0]["prefix"], "")
        self.assertEqual(name["surname_list"][0]["connector"], "")

    def test_none_values_and_non_dicts(self):
        self.assertEqual(normalize_name(None)["surname_list"], [])
        name = normalize_name({"surname_list": None, "first_name": None})
        self.assertEqual(name["surname_list"], [])
        self.assertEqual(name["first_name"], "")
        self.assertEqual(normalize_name_list(None), [])

    def test_input_is_not_mutated(self):
        original = {"first_name": "Matti"}
        normalize_name(original)
        self.assertEqual(original, {"first_name": "Matti"})


class PersonNameReadWriteTests(TestCase):
    def test_sparse_stored_names_are_completed_on_read(self):
        person = Person.objects.create(
            handle="h1",
            gramps_id="I0001",
            primary_name={"first_name": "Anna Brita", "suffix": "Ollintytär"},
            alternate_names=[{"type": "Syntymänimi"}],
        )
        data = PersonSerializer(person).data
        self.assertEqual(data["primary_name"]["surname_list"], [])
        self.assertEqual(data["primary_name"]["first_name"], "Anna Brita")
        self.assertEqual(data["alternate_names"][0]["surname_list"], [])
        self.assertEqual(data["alternate_names"][0]["type"], "Syntymänimi")

    def test_names_are_completed_on_write(self):
        def add(builder):
            builder.add({
                "_class": "Person",
                "handle": "h2",
                "primary_name": {"first_name": "Anna Brita", "suffix": "Ollintytär"},
                "alternate_names": [{"first_name": "Anna"}],
            })

        run_transaction(None, "add", add)
        person = Person.objects.get(pk="h2")
        self.assertEqual(person.primary_name["surname_list"], [])
        self.assertEqual(person.primary_name["suffix"], "Ollintytär")
        self.assertEqual(person.alternate_names[0]["surname_list"], [])


class TypeNormalizationTests(TestCase):
    def test_localized_types_are_stored_as_standard_values(self):
        from apps.core.gramps_types import normalize_type, normalize_types

        self.assertEqual(normalize_type("Syntymä", "event_types"), "Birth")
        self.assertEqual(normalize_type("Birth", "event_types"), "Birth")
        self.assertEqual(normalize_type("Oma tyyppi", "event_types"), "Oma tyyppi")
        self.assertEqual(normalize_type("Päähenkilö", "event_role_types"), "Primary")
        data = normalize_types({
            "primary_name": {"type": "Syntymänimi", "surname_list": [{"origintype": "Isännimi"}]},
            "event_ref_list": [{"ref": "e", "role": "Päähenkilö"}],
            "attribute_list": [{"type": "Ammatti"}],
        }, "Person")
        self.assertEqual(data["primary_name"]["type"], "Birth Name")
        self.assertEqual(data["event_ref_list"][0]["role"], "Primary")

    def test_localized_birth_event_sets_birth_index(self):
        def add(builder):
            builder.add({"_class": "Event", "handle": "e1", "type": "Syntymä"})
            builder.add({
                "_class": "Person", "handle": "p1",
                "primary_name": {"first_name": "Anna"},
                "event_ref_list": [{"_class": "EventRef", "ref": "e1", "role": "Päähenkilö"}],
            })

        run_transaction(None, "add", add)
        self.assertEqual(Event.objects.get(pk="e1").type, "Birth")
        person = Person.objects.get(pk="p1")
        self.assertEqual(person.birth_ref_index, 0)
        self.assertEqual(person.event_ref_list[0]["role"], "Primary")

    def test_event_type_change_updates_birth_index(self):
        def add(builder):
            builder.add({"_class": "Event", "handle": "e2", "type": "Oma"})
            builder.add({
                "_class": "Person", "handle": "p2",
                "event_ref_list": [{"_class": "EventRef", "ref": "e2", "role": "Primary"}],
            })

        run_transaction(None, "add", add)
        self.assertEqual(Person.objects.get(pk="p2").birth_ref_index, -1)
        run_transaction(None, "upd", lambda b: b.update(
            {"_class": "Event", "handle": "e2", "type": "Syntymä"}))
        self.assertEqual(Person.objects.get(pk="p2").birth_ref_index, 0)
