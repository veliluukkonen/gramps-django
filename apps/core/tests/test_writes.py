"""Tests for apps.core.writes (TransactionBuilder) and the write endpoints."""

from django.test import TestCase
from rest_framework.test import APIClient

from apps.auth.models import GrampsUser
from apps.auth.permissions import ROLE_EDITOR, ROLE_GUEST
from apps.core.models import (
    BacklinkIndex,
    Citation,
    Event,
    Family,
    Person,
    Source,
    Transaction,
    TransactionChange,
)
from apps.core.writes import TransactionBuilder, run_transaction


def person_dict(first, surname, handle=None, **extra):
    d = {
        "_class": "Person",
        "gender": 1,
        "primary_name": {
            "_class": "Name",
            "first_name": first,
            "surname_list": [{"_class": "Surname", "surname": surname, "primary": True}],
            "type": "Birth Name",
        },
    }
    if handle:
        d["handle"] = handle
    d.update(extra)
    return d


class TransactionBuilderTests(TestCase):
    def test_add_generates_handle_and_gramps_id(self):
        result = run_transaction(None, "add", lambda b: b.add(person_dict("Matti", "Meikäläinen")))
        self.assertEqual(len(result), 1)
        item = result[0]
        self.assertEqual(item["type"], "add")
        self.assertEqual(item["_class"], "Person")
        self.assertIsNone(item["old"])
        self.assertEqual(item["new"]["gramps_id"], "I0000")
        self.assertTrue(item["handle"])
        self.assertEqual(Person.objects.get(pk=item["handle"]).primary_name["first_name"], "Matti")
        # second person gets the next id
        result2 = run_transaction(None, "add", lambda b: b.add(person_dict("Maija", "Meikäläinen")))
        self.assertEqual(result2[0]["new"]["gramps_id"], "I0001")
        # transaction history recorded
        self.assertEqual(Transaction.objects.count(), 2)
        self.assertEqual(TransactionChange.objects.filter(trans_type=0).count(), 2)

    def test_add_family_updates_members(self):
        father = run_transaction(None, "", lambda b: b.add(person_dict("Isä", "X")))[0]["handle"]
        mother = run_transaction(None, "", lambda b: b.add(person_dict("Äiti", "X")))[0]["handle"]
        child = run_transaction(None, "", lambda b: b.add(person_dict("Lapsi", "X")))[0]["handle"]
        fam = {
            "_class": "Family",
            "father_handle": father,
            "mother_handle": mother,
            "child_ref_list": [{"_class": "ChildRef", "ref": child, "frel": "Birth", "mrel": "Birth"}],
            "type": "Married",
        }
        result = run_transaction(None, "add family", lambda b: b.add(fam))
        types = {(c["_class"], c["type"]) for c in result}
        self.assertIn(("Family", "add"), types)
        self.assertIn(("Person", "update"), types)
        fh = [c for c in result if c["_class"] == "Family"][0]["handle"]
        self.assertEqual(Person.objects.get(pk=father).family_list, [fh])
        self.assertEqual(Person.objects.get(pk=mother).family_list, [fh])
        self.assertEqual(Person.objects.get(pk=child).parent_family_list, [fh])
        self.assertEqual(Family.objects.get(pk=fh).gramps_id, "F0000")
        # backlinks populated
        self.assertTrue(
            BacklinkIndex.objects.filter(source_handle=fh, target_handle=child).exists()
        )

    def test_update_family_children_and_delete_person(self):
        father = run_transaction(None, "", lambda b: b.add(person_dict("Isä", "X")))[0]["handle"]
        child1 = run_transaction(None, "", lambda b: b.add(person_dict("L1", "X")))[0]["handle"]
        child2 = run_transaction(None, "", lambda b: b.add(person_dict("L2", "X")))[0]["handle"]
        fam = {
            "_class": "Family",
            "father_handle": father,
            "child_ref_list": [{"ref": child1, "frel": "Birth", "mrel": "Birth"}],
        }
        result = run_transaction(None, "", lambda b: b.add(fam))
        fh = [c for c in result if c["_class"] == "Family"][0]["handle"]

        # replace child1 with child2
        fam_data = {
            "_class": "Family",
            "handle": fh,
            "father_handle": father,
            "child_ref_list": [{"ref": child2, "frel": "Birth", "mrel": "Birth"}],
        }
        run_transaction(None, "", lambda b: b.update(fam_data, "Family", fh))
        self.assertEqual(Person.objects.get(pk=child1).parent_family_list, [])
        self.assertEqual(Person.objects.get(pk=child2).parent_family_list, [fh])

        # delete father: family father becomes empty
        result = run_transaction(None, "", lambda b: b.delete("Person", father))
        self.assertIn(("Person", "delete"), {(c["_class"], c["type"]) for c in result})
        self.assertIsNone(Family.objects.get(pk=fh).father_handle_id)
        self.assertFalse(Person.objects.filter(pk=father).exists())

    def test_delete_event_strips_refs_and_birth_index(self):
        ev = run_transaction(
            None, "", lambda b: b.add({"_class": "Event", "type": "Birth", "date": {}})
        )[0]["handle"]
        p = run_transaction(
            None,
            "",
            lambda b: b.add(person_dict("A", "B", event_ref_list=[{"ref": ev, "role": "Primary"}])),
        )[0]
        self.assertEqual(p["new"]["birth_ref_index"], 0)
        run_transaction(None, "", lambda b: b.delete("Event", ev))
        person = Person.objects.get(pk=p["handle"])
        self.assertEqual(person.event_ref_list, [])
        self.assertEqual(person.birth_ref_index, -1)

    def test_delete_source_deletes_citations(self):
        s = run_transaction(None, "", lambda b: b.add({"_class": "Source", "title": "S"}))[0]["handle"]
        c = run_transaction(
            None, "", lambda b: b.add({"_class": "Citation", "source_handle": s, "page": "1"})
        )[0]["handle"]
        p = run_transaction(
            None, "", lambda b: b.add(person_dict("A", "B", citation_list=[c]))
        )[0]["handle"]
        result = run_transaction(None, "", lambda b: b.delete("Source", s))
        classes = {(x["_class"], x["type"]) for x in result}
        self.assertIn(("Citation", "delete"), classes)
        self.assertIn(("Source", "delete"), classes)
        self.assertFalse(Citation.objects.filter(pk=c).exists())
        self.assertEqual(Person.objects.get(pk=p).citation_list, [])

    def test_update_keeps_gramps_id_and_rejects_duplicate(self):
        h1 = run_transaction(None, "", lambda b: b.add(person_dict("A", "B")))[0]["handle"]
        run_transaction(None, "", lambda b: b.add(person_dict("C", "D")))
        data = person_dict("A2", "B", handle=h1)
        data["gramps_id"] = ""
        run_transaction(None, "", lambda b: b.update(data, "Person", h1))
        self.assertEqual(Person.objects.get(pk=h1).gramps_id, "I0000")
        data["gramps_id"] = "I0001"
        with self.assertRaises(ValueError):
            run_transaction(None, "", lambda b: b.update(data, "Person", h1))


class WriteEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.editor = GrampsUser.objects.create_user("editor", "pw", role=ROLE_EDITOR)
        self.guest = GrampsUser.objects.create_user("guest", "pw", role=ROLE_GUEST)

    def test_post_person_returns_transaction_list(self):
        self.client.force_authenticate(self.editor)
        resp = self.client.post("/api/people/", person_dict("Matti", "M"), format="json")
        self.assertEqual(resp.status_code, 201, resp.content)
        data = resp.json()
        self.assertEqual(data[0]["new"]["_class"], "Person")
        self.assertEqual(data[0]["new"]["gramps_id"], "I0000")
        self.assertEqual(Transaction.objects.get().user, self.editor)

    def test_post_requires_permission(self):
        resp = self.client.post("/api/people/", person_dict("Matti", "M"), format="json")
        self.assertEqual(resp.status_code, 401)
        self.client.force_authenticate(self.guest)
        resp = self.client.post("/api/people/", person_dict("Matti", "M"), format="json")
        self.assertEqual(resp.status_code, 403)

    def test_objects_post_with_client_handles(self):
        self.client.force_authenticate(self.editor)
        payload = [
            person_dict("A", "B", handle="p-1", event_ref_list=[{"ref": "e-1", "role": "Primary"}]),
            {"_class": "Event", "handle": "e-1", "type": "Birth", "date": {}},
        ]
        resp = self.client.post("/api/objects/", payload, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)
        data = resp.json()
        person = [x for x in data if x["new"]["_class"] == "Person"][0]["new"]
        self.assertEqual(person["gramps_id"], "I0000")
        self.assertEqual(person["birth_ref_index"], 0)
        self.assertEqual(Event.objects.get(pk="e-1").gramps_id, "E0000")

    def test_put_etag_mismatch_and_success(self):
        self.client.force_authenticate(self.editor)
        h = self.client.post("/api/people/", person_dict("A", "B"), format="json").json()[0]["handle"]
        resp = self.client.get(f"/api/people/{h}")
        self.assertEqual(resp.status_code, 200)
        etag = resp["ETag"]
        body = resp.json()
        body["primary_name"]["first_name"] = "Changed"
        bad = self.client.put(f"/api/people/{h}", body, format="json", HTTP_IF_MATCH='"0"')
        self.assertEqual(bad.status_code, 412)
        ok = self.client.put(f"/api/people/{h}", body, format="json", HTTP_IF_MATCH=etag)
        self.assertEqual(ok.status_code, 200, ok.content)
        self.assertEqual(ok.json()[0]["type"], "update")
        self.assertEqual(Person.objects.get(pk=h).primary_name["first_name"], "Changed")

    def test_delete_endpoint(self):
        self.client.force_authenticate(self.editor)
        h = self.client.post("/api/people/", person_dict("A", "B"), format="json").json()[0]["handle"]
        resp = self.client.delete(f"/api/people/{h}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()[0]["type"], "delete")
        self.assertFalse(Person.objects.filter(pk=h).exists())
        resp = self.client.delete(f"/api/people/{h}")
        self.assertEqual(resp.status_code, 404)

    def test_list_pagination_and_gramps_id(self):
        self.client.force_authenticate(self.editor)
        for i in range(3):
            self.client.post("/api/people/", person_dict(f"P{i}", "X"), format="json")
        resp = self.client.get("/api/people/?page=1&pagesize=2&sort=gramps_id")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()), 2)
        self.assertEqual(resp["X-Total-Count"], "3")
        resp = self.client.get("/api/people/?gramps_id=I0002&profile=self")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()[0]["profile"]["name_given"], "P2")
        resp = self.client.get("/api/people/?gramps_id=I9999")
        self.assertEqual(resp.status_code, 404)
        self.assertIn("message", resp.json()["error"])
