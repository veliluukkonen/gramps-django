"""Tests for /api/trees/, /api/config/, /api/tasks/, delete, search index, reports."""

from django.conf import settings

from apps.core.models import BacklinkIndex, Event, Family, Person, Transaction
from apps.tree.models import Config, TaskResult

from .base import TreeTestCase


class TreeTests(TreeTestCase):
    def test_tree_details(self):
        self.add_objects(self.owner, [self.person("p1", "Anna", "Smith", 0)])
        self.login(self.member)
        res = self.client.get("/api/trees/-")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["name"], settings.TREE_NAME)
        self.assertEqual(res.data["usage_people"], 1)
        self.assertEqual(res.data["usage_media"], 0)
        self.assertIsNone(res.data["quota_people"])
        self.assertIsNone(res.data["quota_media"])
        self.assertIsNone(res.data["min_role_ai"])
        self.assertTrue(res.data["enabled"])
        self.assertIn("id", res.data)
        res = self.client.get(f"/api/trees/{res.data['id']}")
        self.assertEqual(res.status_code, 200)
        res = self.client.get("/api/trees/")
        self.assertEqual(len(res.data), 1)

    def test_tree_update(self):
        self.login(self.owner)
        res = self.client.put("/api/trees/-", {"name": "Our tree", "min_role_ai": 2}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["new_name"], "Our tree")
        self.assertEqual(res.data["min_role_ai"], 2)
        self.assertEqual(Config.get("tree_name"), "Our tree")
        self.assertEqual(self.client.get("/api/trees/-").data["name"], "Our tree")
        self.assertEqual(self.client.get("/api/trees/-").data["min_role_ai"], 2)
        # quotas need the admin-only permission
        res = self.client.put("/api/trees/-", {"quota_people": 10}, format="json")
        self.assertEqual(res.status_code, 403)
        self.login(self.admin)
        res = self.client.put("/api/trees/-", {"quota_people": 10}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.client.get("/api/trees/-").data["quota_people"], 10)
        self.login(self.member)
        res = self.client.put("/api/trees/-", {"name": "x"}, format="json")
        self.assertEqual(res.status_code, 403)

    def test_migrate(self):
        self.login(self.owner)
        res = self.client.post("/api/trees/-/migrate")
        self.assertEqual(res.status_code, 200)
        self.login(self.member)
        self.assertEqual(self.client.post("/api/trees/-/migrate").status_code, 403)

    def test_repair(self):
        self.add_objects(self.owner, [
            self.person("p1", "Anna", "Smith", 0),
            self.person("p2", "Bob", "Smith"),
            {"_class": "Event", "handle": "e1", "type": "Birth"},
        ])
        # Make the data inconsistent behind the back of TransactionBuilder.
        anna = Person.objects.get(pk="p1")
        anna.event_ref_list = [
            {"ref": "e1", "role": "Primary"},
            {"ref": "missing-event", "role": "Primary"},
        ]
        anna.note_list = ["missing-note"]
        anna.family_list = ["missing-family"]
        anna.birth_ref_index = -1
        anna.save()
        Family.objects.create(handle="f1", father_handle_id="p2", mother_handle_id="p1",
                              child_ref_list=[{"ref": "p1"}, {"ref": "nobody"}])
        BacklinkIndex.objects.all().delete()

        self.login(self.owner)
        res = self.client.post("/api/trees/-/repair")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertGreater(res.data["num_errors"], 0)
        self.assertIn("references to missing objects removed", res.data["message"])
        anna = Person.objects.get(pk="p1")
        self.assertEqual([r["ref"] for r in anna.event_ref_list], ["e1"])
        self.assertEqual(anna.note_list, [])
        self.assertEqual(anna.family_list, ["f1"])
        self.assertEqual(anna.parent_family_list, ["f1"])
        self.assertEqual(anna.birth_ref_index, 0)
        self.assertEqual(Person.objects.get(pk="p2").family_list, ["f1"])
        self.assertEqual([r["ref"] for r in Family.objects.get(pk="f1").child_ref_list], ["p1"])
        self.assertTrue(BacklinkIndex.objects.filter(source_handle="p1", target_handle="e1").exists())
        self.assertEqual(Transaction.objects.latest("id").description, "Check and repair")
        self.assertEqual(TaskResult.objects.filter(name="repair_tree", state="SUCCESS").count(), 1)

        # second run: clean
        res = self.client.post("/api/trees/-/repair")
        self.assertEqual(res.data["num_errors"], 0)

    def test_repair_requires_permission(self):
        self.login(self.member)
        self.assertEqual(self.client.post("/api/trees/-/repair").status_code, 403)


class ConfigTests(TreeTestCase):
    def test_config_crud(self):
        self.login(self.admin)
        res = self.client.get("/api/config/EMAIL_HOST/")
        self.assertEqual(res.status_code, 404)
        res = self.client.put("/api/config/EMAIL_HOST/", {"value": "smtp.example.com"}, format="json")
        self.assertEqual(res.status_code, 200)
        res = self.client.get("/api/config/EMAIL_HOST/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, "smtp.example.com")
        res = self.client.get("/api/config/")
        self.assertEqual(res.data, {"EMAIL_HOST": "smtp.example.com"})
        res = self.client.put("/api/config/NOT_ALLOWED/", {"value": "x"}, format="json")
        self.assertEqual(res.status_code, 404)
        res = self.client.delete("/api/config/EMAIL_HOST/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.client.get("/api/config/").data, {})
        self.assertEqual(self.client.delete("/api/config/EMAIL_HOST/").status_code, 404)

    def test_config_permissions(self):
        self.login(self.owner)  # owner has no EditSettings in the role table
        res = self.client.put("/api/config/EMAIL_HOST/", {"value": "x"}, format="json")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self.client.get("/api/config/").status_code, 403)


class TaskTests(TreeTestCase):
    def test_task_status(self):
        self.login(self.member)
        self.assertEqual(self.client.get("/api/tasks/nope").status_code, 404)
        TaskResult.objects.create(id="t1", name="x", state="SUCCESS", result={"url": "/x"}, info={"url": "/x"})
        res = self.client.get("/api/tasks/t1")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["state"], "SUCCESS")
        self.assertEqual(res.data["result"], '{"url": "/x"}')
        self.assertEqual(res.data["result_object"], {"url": "/x"})


class DeleteObjectsTests(TreeTestCase):
    def setUp(self):
        self.add_objects(self.owner, [
            self.person("p1", "Anna", "Smith", 0),
            self.person("p2", "Bob", "Smith"),
            {"_class": "Event", "handle": "e1", "type": "Birth"},
            {"_class": "Note", "handle": "n1", "text": {"string": "hi", "tags": []}},
        ])
        self.add_objects(self.owner, [
            {"_class": "Family", "handle": "f1", "father_handle": "p2", "mother_handle": "p1"},
        ])

    def test_delete_namespaces(self):
        self.login(self.owner)
        res = self.client.post("/api/objects/delete/?namespaces=families,people")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(Person.objects.count(), 0)
        self.assertEqual(Family.objects.count(), 0)
        self.assertEqual(Event.objects.count(), 1)
        txn = Transaction.objects.latest("id")
        self.assertEqual(txn.description, "Delete families, people")
        self.assertGreaterEqual(txn.changes.filter(trans_type=2).count(), 3)

    def test_delete_all(self):
        self.login(self.owner)
        res = self.client.post("/api/objects/delete/")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(Person.objects.count(), 0)
        self.assertEqual(Event.objects.count(), 0)
        self.assertEqual(BacklinkIndex.objects.count(), 0)
        self.assertEqual(Transaction.objects.latest("id").description, "Delete all objects")

    def test_delete_unknown_namespace_and_permissions(self):
        self.login(self.owner)
        self.assertEqual(self.client.post("/api/objects/delete/?namespaces=bogus").status_code, 422)
        self.login(self.member)
        self.assertEqual(self.client.post("/api/objects/delete/").status_code, 403)
        self.assertEqual(Person.objects.count(), 2)


class MiscTests(TreeTestCase):
    def test_search_index(self):
        self.login(self.owner)
        self.assertEqual(self.client.post("/api/search/index/?full=1").status_code, 201)
        self.login(self.member)
        self.assertEqual(self.client.post("/api/search/index/").status_code, 403)

    def test_reports(self):
        self.login(self.member)
        res = self.client.get("/api/reports/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, [])
        self.assertEqual(self.client.get("/api/reports/ancestor_report").status_code, 404)
        self.assertEqual(self.client.get("/api/reports/ancestor_report/file").status_code, 404)
