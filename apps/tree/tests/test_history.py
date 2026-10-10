"""Tests for the transaction history, raw transactions and undo."""


from apps.core.models import Family, Person, Transaction

from .base import TreeTestCase


class HistoryTests(TreeTestCase):
    def setUp(self):
        self.txn1 = self.add_objects(self.owner, [self.person("p1", "Anna", "Smith", 0)], "Add Anna")
        self.txn2 = self.add_objects(self.owner, [self.person("p2", "Bob", "Smith")], "Add Bob")

    def test_history_list(self):
        self.login(self.owner)
        res = self.client.get("/api/transactions/history/?sort=-id&page=1&pagesize=1")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["X-Total-Count"], "2")
        self.assertEqual(len(res.data), 1)
        txn = res.data[0]
        self.assertEqual(txn["description"], "Add Bob")
        self.assertEqual(txn["connection"]["user"], {"name": "owner", "full_name": "Tree Owner"})
        self.assertEqual(txn["changes"][0]["obj_class"], "Person")
        self.assertEqual(txn["changes"][0]["trans_type"], 0)
        self.assertEqual(txn["changes"][0]["obj_handle"], "p2")
        self.assertNotIn("new_data", txn["changes"][0])
        self.assertIn("first", txn)
        self.assertIn("last", txn)
        self.assertFalse(txn["undo"])

        res = self.client.get("/api/transactions/history/?sort=id")
        self.assertEqual([t["description"] for t in res.data], ["Add Anna", "Add Bob"])

    def test_history_detail_with_data(self):
        self.login(self.owner)
        txn_id = Transaction.objects.get(description="Add Anna").id
        res = self.client.get(f"/api/transactions/history/{txn_id}?old=1&new=1")
        self.assertEqual(res.status_code, 200)
        change = res.data["changes"][0]
        self.assertIsNone(change["old_data"])
        self.assertEqual(change["new_data"]["handle"], "p1")
        self.assertEqual(change["new_data"]["_class"], "Person")
        res = self.client.get("/api/transactions/history/99999")
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.data["error"]["message"], "Transaction 99999 not found")

    def test_history_requires_view_private(self):
        self.login(self.guest)
        res = self.client.get("/api/transactions/history/")
        self.assertEqual(res.status_code, 403)
        self.logout()
        res = self.client.get("/api/transactions/history/")
        self.assertEqual(res.status_code, 401)

    def test_undo_add_via_transactions_endpoint(self):
        self.login(self.owner)
        res = self.client.post("/api/transactions/?undo=1", self.txn2, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data[0]["type"], "delete")
        self.assertEqual(res.data[0]["handle"], "p2")
        self.assertFalse(Person.objects.filter(pk="p2").exists())
        self.assertEqual(Transaction.objects.count(), 3)
        self.assertEqual(Transaction.objects.latest("id").description, "Undo transaction")

    def test_undo_delete_restores_object(self):
        self.login(self.owner)
        res = self.client.delete("/api/people/p2")
        self.assertIn(res.status_code, (200, 204))
        deleted_txn = res.data if isinstance(res.data, list) else [
            {"type": "delete", "handle": "p2", "_class": "Person", "old": self.txn2[0]["new"], "new": None}
        ]
        res = self.client.post("/api/transactions/?undo=1", deleted_txn, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(Person.objects.filter(pk="p2").exists())
        self.assertEqual(Person.objects.get(pk="p2").primary_name["first_name"], "Bob")

    def test_undo_family_with_cascades(self):
        """Undoing an "add family" also restores the parents' family lists."""
        family = {
            "_class": "Family",
            "handle": "f1",
            "father_handle": "p2",
            "mother_handle": "p1",
            "type": "Married",
        }
        txn = self.add_objects(self.owner, [family], "Add family")
        # family add + two person updates (family_list)
        self.assertEqual(len(txn), 3)
        self.assertEqual(Person.objects.get(pk="p1").family_list, ["f1"])
        self.login(self.owner)
        res = self.client.post("/api/transactions/?undo=1", txn, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertFalse(Family.objects.filter(pk="f1").exists())
        self.assertEqual(Person.objects.get(pk="p1").family_list, [])
        self.assertEqual(Person.objects.get(pk="p2").family_list, [])

    def test_undo_conflict_detected(self):
        self.login(self.owner)
        # Change Bob after the transaction: undo must be refused unless forced.
        bob = Person.objects.get(pk="p2")
        bob.primary_name = {**bob.primary_name, "first_name": "Robert"}
        bob.save()
        res = self.client.post("/api/transactions/?undo=1", self.txn2, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["error"]["message"], "Object has changed")
        self.assertTrue(Person.objects.filter(pk="p2").exists())
        res = self.client.post("/api/transactions/?undo=1&force=1", self.txn2, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertFalse(Person.objects.filter(pk="p2").exists())

    def test_apply_raw_transaction(self):
        self.login(self.owner)
        payload = [{
            "type": "add", "handle": "p3", "_class": "Person", "old": None,
            "new": self.person("p3", "Carl", "Jones")
        }]
        res = self.client.post("/api/transactions/", payload, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res["X-Total-Count"], "1")
        self.assertTrue(Person.objects.filter(pk="p3").exists())
        res = self.client.post("/api/transactions/", [], format="json")
        self.assertEqual(res.status_code, 400)

    def test_history_undo_endpoint(self):
        self.login(self.owner)
        txn_id = Transaction.objects.get(description="Add Bob").id
        res = self.client.get(f"/api/transactions/history/{txn_id}/undo")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["can_undo_without_force"])
        self.assertEqual(res.data["conflicts"], [])
        res = self.client.post(f"/api/transactions/history/{txn_id}/undo")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertFalse(Person.objects.filter(pk="p2").exists())
        self.assertTrue(Transaction.objects.get(pk=txn_id).undone)
        res = self.client.get(f"/api/transactions/history/{txn_id}?old=1")
        self.assertTrue(res.data["undo"])

    def test_transactions_require_write_permissions(self):
        self.login(self.member)
        res = self.client.post("/api/transactions/?undo=1", self.txn2, format="json")
        self.assertEqual(res.status_code, 403)
