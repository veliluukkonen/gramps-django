"""Tests for apps.core.names (Name normalization on read and write)."""

from django.test import TestCase

from apps.core.models import Person
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
