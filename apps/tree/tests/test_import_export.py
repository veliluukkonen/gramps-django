"""Tests for the Gramps XML importer/exporter and the importer/exporter endpoints."""

import gzip
import io
import os
import xml.etree.ElementTree as ET

from django.conf import settings
from django.core.management import call_command

from apps.core.models import (
    BacklinkIndex,
    Citation,
    Event,
    Family,
    MediaObject,
    Note,
    Person,
    Place,
    Repository,
    Source,
    Tag,
)
from apps.migration.importer import import_gramps_xml
from apps.tree.export_gedcom import gedcom_date
from apps.tree.export_xml import export_gramps_xml

from .base import EXAMPLE_GRAMPS, TreeTestCase

EXPECTED = {
    "person": 60, "family": 23, "event": 125, "place": 43, "source": 4,
    "citation": 3, "repository": 2, "media": 6, "note": 7, "tag": 1,
}


def _counts():
    return {
        "person": Person.objects.count(), "family": Family.objects.count(),
        "event": Event.objects.count(), "place": Place.objects.count(),
        "source": Source.objects.count(), "citation": Citation.objects.count(),
        "repository": Repository.objects.count(), "media": MediaObject.objects.count(),
        "note": Note.objects.count(), "tag": Tag.objects.count(),
    }


def _element_counts(root):
    counts = {}
    for el in root.iter():
        tag = el.tag.split("}", 1)[-1]
        counts[tag] = counts.get(tag, 0) + 1
    return counts


class ImporterTests(TreeTestCase):
    def test_import_example(self):
        result = import_gramps_xml(EXAMPLE_GRAMPS)
        self.assertEqual({k: result[k] for k in EXPECTED}, EXPECTED)
        self.assertEqual(result["total"], sum(EXPECTED.values()))
        self.assertEqual(_counts(), EXPECTED)
        # handles are stored without the leading underscore
        self.assertTrue(Person.objects.filter(pk="a701e8fd8ea27f99704").exists() or
                        not Person.objects.filter(handle__startswith="_").exists())
        self.assertFalse(Person.objects.filter(handle__startswith="_").exists())
        anna = Person.objects.get(gramps_id="I0000")
        self.assertEqual(anna.primary_name["first_name"], "Anna")
        self.assertTrue(anna.primary_name["surname_list"][0]["primary"])
        self.assertEqual(anna.gender, Person.FEMALE)
        self.assertGreaterEqual(anna.birth_ref_index, 0)
        birth = Event.objects.get(pk=anna.event_ref_list[anna.birth_ref_index]["ref"])
        self.assertEqual(birth.type, "Birth")
        self.assertEqual(birth.date["dateval"][:3], [2, 10, 1864])
        self.assertIsNotNone(birth.place_id)
        self.assertTrue(Place.objects.filter(pk=birth.place_id).exists())
        # family links resolved
        fam = Family.objects.exclude(father_handle=None).first()
        self.assertIn(fam.handle, Person.objects.get(pk=fam.father_handle_id).family_list)
        self.assertGreater(BacklinkIndex.objects.count(), 0)
        self.assertTrue(BacklinkIndex.objects.filter(source_handle=anna.handle, target_type="Event").exists())
        # citations point to existing sources
        for cit in Citation.objects.all():
            self.assertTrue(Source.objects.filter(pk=cit.source_handle_id).exists())

    def test_import_is_idempotent_merge(self):
        import_gramps_xml(EXAMPLE_GRAMPS)
        import_gramps_xml(EXAMPLE_GRAMPS)
        self.assertEqual(_counts(), EXPECTED)

    def test_import_legacy_underscore_handles(self):
        """Objects stored with the old underscored handles are updated, not duplicated."""
        import_gramps_xml(EXAMPLE_GRAMPS)
        anna = Person.objects.get(gramps_id="I0000")
        # simulate a legacy database for one person and the events it references
        Person.objects.filter(pk=anna.handle).update(handle="_" + anna.handle)
        import_gramps_xml(EXAMPLE_GRAMPS)
        self.assertEqual(Person.objects.count(), EXPECTED["person"])
        self.assertTrue(Person.objects.filter(pk="_" + anna.handle).exists())
        fam = Family.objects.filter(mother_handle_id="_" + anna.handle)
        self.assertTrue(fam.exists() or Family.objects.filter(father_handle_id="_" + anna.handle).exists())

    def test_import_gzip_and_clear(self):
        with open(EXAMPLE_GRAMPS, "rb") as f:
            gz = gzip.compress(f.read())
        self.add_objects(self.owner, [self.person("x1", "Old", "One")])
        import_gramps_xml(io.BytesIO(gz), clear=True)
        self.assertEqual(_counts(), EXPECTED)
        self.assertFalse(Person.objects.filter(pk="x1").exists())

    def test_management_command(self):
        out = io.StringIO()
        call_command("import_gramps_xml", EXAMPLE_GRAMPS, stdout=out)
        self.assertIn("Import complete", out.getvalue())
        self.assertEqual(Person.objects.count(), 60)

    def test_invalid_file(self):
        from apps.migration.importer import ImportError_

        with self.assertRaises(ImportError_):
            import_gramps_xml(io.BytesIO(b"<html>no</html>"))
        with self.assertRaises(ImportError_):
            import_gramps_xml(io.BytesIO(b"garbage"))

    def test_date_edge_cases(self):
        xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<database xmlns="http://gramps-project.org/xml/1.7.2/">
  <header><created date="2024-01-01" version="5.2.0"/></header>
  <events>
    <event handle="_e1" change="1" id="E1"><type>Birth</type><dateval val="????-05-02" type="about" quality="estimated"/></event>
    <event handle="_e2" change="1" id="E2"><type>Death</type><daterange start="1900" stop="1950-06" cformat="Julian"/></event>
    <event handle="_e3" change="1" id="E3"><type>Burial</type><datestr val="sometime"/></event>
    <event handle="_e4" change="1" id="E4"><type>Census</type><dateval val="1800-01-01" type="from" newyear="Mar25" dualdated="1"/></event>
  </events>
</database>"""
        import_gramps_xml(io.BytesIO(xml))
        e1 = Event.objects.get(pk="e1").date
        self.assertEqual(e1["dateval"], [2, 5, 0, False])
        self.assertEqual(e1["modifier"], 3)
        self.assertEqual(e1["quality"], 1)
        e2 = Event.objects.get(pk="e2").date
        self.assertEqual(e2["dateval"], [0, 0, 1900, False, 0, 6, 1950, False])
        self.assertEqual(e2["modifier"], 4)
        self.assertEqual(e2["calendar"], 1)
        e3 = Event.objects.get(pk="e3").date
        self.assertEqual(e3["modifier"], 6)
        self.assertEqual(e3["text"], "sometime")
        e4 = Event.objects.get(pk="e4").date
        self.assertEqual(e4["modifier"], 7)
        self.assertEqual(e4["newyear"], 2)
        self.assertTrue(e4["dateval"][3])


class ExporterTests(TreeTestCase):
    def _export(self, compress=False, exclude_private=False):
        path = os.path.join(settings.MEDIA_ROOT, "test-export.gramps")
        os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
        export_gramps_xml(path, compress=compress, exclude_private=exclude_private)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        return path

    def test_round_trip(self):
        import_gramps_xml(EXAMPLE_GRAMPS)
        anna = Person.objects.get(gramps_id="I0000")
        path = self._export()
        root = ET.parse(path).getroot()
        self.assertEqual(root.tag, "{http://gramps-project.org/xml/1.7.2/}database")
        exported = _element_counts(root)
        original = _element_counts(ET.parse(EXAMPLE_GRAMPS).getroot())
        for tag in ("person", "family", "event", "placeobj", "source", "citation", "repository",
                    "object", "note", "tag", "eventref", "childref", "childof", "parentin",
                    "dateval", "daterange", "surname", "pname", "placeref", "objref", "noteref",
                    "citationref", "attribute", "address", "url", "father", "mother", "rel",
                    "reporef", "sourceref", "tagref", "coord", "ptitle", "description"):
            self.assertEqual(exported.get(tag, 0), original.get(tag, 0), tag)
        # handles carry the underscore prefix again
        people = root.find("{http://gramps-project.org/xml/1.7.2/}people")
        handles = {p.get("handle") for p in people}
        self.assertIn("_" + anna.handle, handles)

        # Re-import into an empty database: identical content.
        from apps.migration.importer import clear_all_objects

        clear_all_objects()
        import_gramps_xml(path)
        self.assertEqual(_counts(), EXPECTED)
        anna2 = Person.objects.get(gramps_id="I0000")
        self.assertEqual(anna2.handle, anna.handle)
        self.assertEqual(anna2.primary_name, anna.primary_name)
        self.assertEqual(anna2.event_ref_list, anna.event_ref_list)
        self.assertEqual(anna2.family_list, anna.family_list)
        for model in (Event, Place, Family, Citation, Source, Note, MediaObject, Repository):
            self.assertEqual(model.objects.count(), EXPECTED[model.__name__.lower().replace("object", "")])
        birth = Event.objects.get(pk=anna2.event_ref_list[anna2.birth_ref_index]["ref"])
        self.assertEqual(birth.date["dateval"][:3], [2, 10, 1864])
        # re-importing the export into the same DB does not duplicate
        import_gramps_xml(path)
        self.assertEqual(_counts(), EXPECTED)

    def test_export_gzip(self):
        import_gramps_xml(EXAMPLE_GRAMPS)
        path = self._export(compress=True)
        with gzip.open(path, "rb") as f:
            root = ET.parse(f).getroot()
        self.assertEqual(len(root.find("{http://gramps-project.org/xml/1.7.2/}people")), 60)

    def test_export_excludes_private(self):
        self.add_objects(self.owner, [
            self.person("p1", "Anna", "Smith", 0, private=True),
            self.person("p2", "Bob", "Smith"),
            {"_class": "Note", "handle": "n1", "text": {"string": "secret", "tags": []}, "private": True},
        ])
        Person.objects.filter(pk="p2").update(note_list=["n1"])
        path = self._export(exclude_private=True)
        root = ET.parse(path).getroot()
        content = open(path, encoding="utf-8").read()
        self.assertNotIn("Anna", content)
        self.assertNotIn("secret", content)
        self.assertNotIn("noteref", content)
        self.assertIn("Bob", content)
        self.assertEqual(len(root.find("{http://gramps-project.org/xml/1.7.2/}people")), 1)

    def test_gedcom_dates(self):
        self.assertEqual(gedcom_date({"dateval": [2, 10, 1864, False], "modifier": 0}), "2 OCT 1864")
        self.assertEqual(gedcom_date({"dateval": [0, 0, 1864, False], "modifier": 1}), "BEF 1864")
        self.assertEqual(
            gedcom_date({"dateval": [0, 0, 1900, False, 0, 6, 1950, False], "modifier": 4}),
            "BET 1900 AND JUN 1950",
        )
        self.assertEqual(gedcom_date({"dateval": [], "modifier": 6, "text": "sometime"}), "(sometime)")
        self.assertEqual(gedcom_date({}), "")


class ImporterExporterEndpointTests(TreeTestCase):
    def test_lists(self):
        self.login(self.member)
        res = self.client.get("/api/importers/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data[0]["extension"], "gramps")
        self.assertEqual(self.client.get("/api/importers/gramps").data["extension"], "gramps")
        self.assertEqual(self.client.get("/api/importers/ged").status_code, 404)
        res = self.client.get("/api/exporters/")
        self.assertEqual({e["extension"] for e in res.data}, {"gramps", "ged"})
        for exp in res.data:
            self.assertEqual(set(exp), {"name", "description", "extension", "module"})
        self.assertEqual(self.client.get("/api/exporters/gramps").data["extension"], "gramps")
        self.assertEqual(self.client.get("/api/exporters/csv").status_code, 404)

    def test_import_endpoint_raw_body(self):
        self.login(self.owner)
        with open(EXAMPLE_GRAMPS, "rb") as f:
            body = gzip.compress(f.read())
        res = self.client.generic(
            "POST", "/api/importers/gramps/file", data=body, content_type="application/gzip"
        )
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["person"], 60)
        self.assertEqual(Person.objects.count(), 60)
        # no temp file left behind
        export_dir = os.path.join(settings.MEDIA_ROOT, "export")
        self.assertEqual([f for f in os.listdir(export_dir) if f.endswith(".gramps")], [])

    def test_import_endpoint_multipart_and_errors(self):
        self.login(self.owner)
        with open(EXAMPLE_GRAMPS, "rb") as f:
            res = self.client.post("/api/importers/gramps/file", {"file": f}, format="multipart")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(Person.objects.count(), 60)
        res = self.client.generic("POST", "/api/importers/gramps/file", data=b"", content_type="application/octet-stream")
        self.assertEqual(res.status_code, 400)
        res = self.client.generic("POST", "/api/importers/gramps/file", data=b"<nope/>", content_type="text/xml")
        self.assertEqual(res.status_code, 400)
        self.assertIn("error", res.data)
        res = self.client.generic("POST", "/api/importers/ged/file", data=b"0 HEAD", content_type="text/plain")
        self.assertEqual(res.status_code, 404)
        self.login(self.member)
        res = self.client.generic("POST", "/api/importers/gramps/file", data=b"<x/>", content_type="text/xml")
        self.assertEqual(res.status_code, 403)

    def test_export_endpoint_flow(self):
        import_gramps_xml(EXAMPLE_GRAMPS)
        token = self.login(self.member)
        res = self.client.post("/api/exporters/gramps/file?compress=true")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["file_type"], ".gramps")
        self.assertTrue(res.data["url"].startswith("/api/exporters/gramps/file/processed/"))
        self.assertTrue(res.data["url"].endswith(res.data["file_name"]))
        # download with ?jwt= only (no Authorization header)
        self.logout()
        res2 = self.client.get(res.data["url"])
        self.assertEqual(res2.status_code, 401)
        res2 = self.client.get(f"{res.data['url']}?jwt={token}")
        self.assertEqual(res2.status_code, 200)
        self.assertIn("gramps-web-export-", res2["Content-Disposition"])
        with gzip.open(io.BytesIO(res2.content), "rb") as f:
            root = ET.parse(f).getroot()
        self.assertEqual(len(root.find("{http://gramps-project.org/xml/1.7.2/}people")), 60)
        # file is removed after download
        self.assertEqual(self.client.get(f"{res.data['url']}?jwt={token}").status_code, 404)
        self.assertEqual(self.client.get(f"/api/exporters/gramps/file/processed/evil.gramps?jwt={token}").status_code, 422)

    def test_export_direct_download_and_gedcom(self):
        import_gramps_xml(EXAMPLE_GRAMPS)
        token = self.login(self.owner)
        self.logout()
        res = self.client.get(f"/api/exporters/gramps/file?jwt={token}&compress=0")
        self.assertEqual(res.status_code, 200)
        root = ET.fromstring(res.content)
        self.assertEqual(len(root.find("{http://gramps-project.org/xml/1.7.2/}people")), 60)
        res = self.client.get(f"/api/exporters/ged/file?jwt={token}")
        self.assertEqual(res.status_code, 200)
        text = res.content.decode("utf-8")
        self.assertTrue(text.startswith("0 HEAD\n"))
        self.assertTrue(text.rstrip().endswith("0 TRLR"))
        self.assertEqual(text.count(" INDI\n"), 60)
        self.assertEqual(text.count(" FAM\n"), 23)
        self.assertIn("1 BIRT\n2 DATE 2 OCT 1864", text)
        self.assertIn("1 NAME Anna /Hansdotter/", text)

    def test_export_requires_auth(self):
        self.assertEqual(self.client.post("/api/exporters/gramps/file").status_code, 401)
        self.assertEqual(self.client.get("/api/exporters/gramps/file").status_code, 401)
