"""
Management command to import a Gramps XML (.gramps) file.

Usage:
    python manage.py import_gramps_xml /path/to/export.gramps
    python manage.py import_gramps_xml /path/to/export.gramps --clear

The .gramps file can be gzipped or plain XML.
"""

import gzip
import xml.etree.ElementTree as ET

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.backlinks import populate_backlinks_for_object
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

# Gramps XML namespace — version may vary, we strip it
NS_PREFIX = "{http://gramps-project.org/xml/"


class Command(BaseCommand):
    help = "Import a Gramps XML (.gramps) export file into the database"

    def add_arguments(self, parser):
        parser.add_argument("file", type=str, help="Path to .gramps XML file")
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Clear all existing data before import",
        )

    def handle(self, *args, **options):
        file_path = options["file"]

        # Try gzipped first, then plain XML
        try:
            with gzip.open(file_path, "rb") as f:
                tree = ET.parse(f)
        except (gzip.BadGzipFile, OSError):
            tree = ET.parse(file_path)

        root = tree.getroot()

        # Strip namespace for easier tag matching
        self._strip_namespace(root)

        if options["clear"]:
            self.stdout.write("Clearing existing data...")
            self._clear_all()

        with transaction.atomic():
            self._import_tags(root)
            self._import_events(root)
            self._import_places(root)
            self._import_sources(root)
            self._import_repositories(root)
            self._import_citations(root)
            self._import_media(root)
            self._import_notes(root)
            self._import_people(root)
            self._import_families(root)

        # Populate backlinks
        self.stdout.write("Building backlink index...")
        self._build_backlinks()

        self.stdout.write(self.style.SUCCESS("Import complete!"))
        self._print_counts()

    def _strip_namespace(self, root):
        """Remove XML namespace prefixes from all tags."""
        for elem in root.iter():
            if "}" in elem.tag:
                elem.tag = elem.tag.split("}", 1)[1]

    def _clear_all(self):
        BacklinkIndex.objects.all().delete()
        Family.objects.all().delete()
        Person.objects.all().delete()
        Citation.objects.all().delete()
        Event.objects.all().delete()
        Place.objects.all().delete()
        Source.objects.all().delete()
        Repository.objects.all().delete()
        MediaObject.objects.all().delete()
        Note.objects.all().delete()
        Tag.objects.all().delete()

    # --- Tag ---
    def _import_tags(self, root):
        tags_el = root.find("tags")
        if tags_el is None:
            return
        count = 0
        for el in tags_el.findall("tag"):
            Tag.objects.update_or_create(
                handle=el.get("handle"),
                defaults={
                    "name": el.get("name", ""),
                    "color": el.get("color", "#000000000000"),
                    "priority": int(el.get("priority", 0)),
                    "change": float(el.get("change", 0)),
                },
            )
            count += 1
        self.stdout.write(f"  Tags: {count}")

    # --- Event ---
    def _import_events(self, root):
        events_el = root.find("events")
        if events_el is None:
            return
        count = 0
        for el in events_el.findall("event"):
            event_type = self._text(el, "type")
            date = self._parse_date(el)
            place_el = el.find("place")
            place_handle = place_el.get("hlink") if place_el is not None else None

            Event.objects.update_or_create(
                handle=el.get("handle"),
                defaults={
                    "gramps_id": el.get("id") or None,
                    "change": float(el.get("change", 0)),
                    "private": el.get("priv") == "1",
                    "type": event_type,
                    "date": date,
                    "place_id": place_handle,
                    "description": self._text(el, "description"),
                    "citation_list": self._hlinks(el, "citationref"),
                    "media_list": self._parse_objrefs(el),
                    "note_list": self._hlinks(el, "noteref"),
                    "tag_list": self._hlinks(el, "tagref"),
                    "attribute_list": self._parse_attributes(el),
                },
            )
            count += 1
        self.stdout.write(f"  Events: {count}")

    # --- Place ---
    def _import_places(self, root):
        places_el = root.find("places")
        if places_el is None:
            return
        count = 0
        for el in places_el.findall("placeobj"):
            coord = el.find("coord")

            # Primary name
            pname_el = el.find("pname")
            name = {}
            if pname_el is not None:
                name = {
                    "value": pname_el.get("value", ""),
                    "lang": pname_el.get("lang", ""),
                }
                date = self._parse_date(pname_el)
                if date:
                    name["date"] = date

            # Alt names
            alt_names = []
            for pn in el.findall("pname"):
                if pn == pname_el:
                    continue
                an = {
                    "value": pn.get("value", ""),
                    "lang": pn.get("lang", ""),
                }
                date = self._parse_date(pn)
                if date:
                    an["date"] = date
                alt_names.append(an)

            # Place refs (hierarchy)
            placeref_list = []
            for pr in el.findall("placeref"):
                ref = {"ref": pr.get("hlink")}
                date = self._parse_date(pr)
                if date:
                    ref["date"] = date
                placeref_list.append(ref)

            # Alternate locations
            alt_loc = []
            for loc in el.findall("location"):
                alt_loc.append({
                    k: loc.get(k, "")
                    for k in ("street", "locality", "city", "parish",
                              "county", "state", "country", "postal", "phone")
                })

            Place.objects.update_or_create(
                handle=el.get("handle"),
                defaults={
                    "gramps_id": el.get("id") or None,
                    "change": float(el.get("change", 0)),
                    "private": el.get("priv") == "1",
                    "title": self._text(el, "ptitle"),
                    "name": name,
                    "alt_names": alt_names,
                    "place_type": el.get("type", ""),
                    "code": self._text(el, "code"),
                    "lat": coord.get("lat", "") if coord is not None else "",
                    "long": coord.get("long", "") if coord is not None else "",
                    "placeref_list": placeref_list,
                    "alt_loc": alt_loc,
                    "urls": self._parse_urls(el),
                    "media_list": self._parse_objrefs(el),
                    "citation_list": self._hlinks(el, "citationref"),
                    "note_list": self._hlinks(el, "noteref"),
                    "tag_list": self._hlinks(el, "tagref"),
                },
            )
            count += 1
        self.stdout.write(f"  Places: {count}")

    # --- Source ---
    def _import_sources(self, root):
        sources_el = root.find("sources")
        if sources_el is None:
            return
        count = 0
        for el in sources_el.findall("source"):
            reporef_list = []
            for rr in el.findall("reporef"):
                ref = {
                    "ref": rr.get("hlink"),
                    "call_number": rr.get("callno", ""),
                    "media_type": rr.get("medium", ""),
                    "private": rr.get("priv") == "1",
                    "note_list": self._hlinks(rr, "noteref"),
                }
                reporef_list.append(ref)

            Source.objects.update_or_create(
                handle=el.get("handle"),
                defaults={
                    "gramps_id": el.get("id") or None,
                    "change": float(el.get("change", 0)),
                    "private": el.get("priv") == "1",
                    "title": self._text(el, "stitle"),
                    "author": self._text(el, "sauthor"),
                    "pubinfo": self._text(el, "spubinfo"),
                    "abbrev": self._text(el, "sabbrev"),
                    "reporef_list": reporef_list,
                    "media_list": self._parse_objrefs(el),
                    "note_list": self._hlinks(el, "noteref"),
                    "tag_list": self._hlinks(el, "tagref"),
                    "attribute_list": self._parse_src_attributes(el),
                },
            )
            count += 1
        self.stdout.write(f"  Sources: {count}")

    # --- Repository ---
    def _import_repositories(self, root):
        repos_el = root.find("repositories")
        if repos_el is None:
            return
        count = 0
        for el in repos_el.findall("repository"):
            Repository.objects.update_or_create(
                handle=el.get("handle"),
                defaults={
                    "gramps_id": el.get("id") or None,
                    "change": float(el.get("change", 0)),
                    "private": el.get("priv") == "1",
                    "name": self._text(el, "rname"),
                    "type": self._text(el, "type"),
                    "address_list": self._parse_addresses(el),
                    "urls": self._parse_urls(el),
                    "note_list": self._hlinks(el, "noteref"),
                    "tag_list": self._hlinks(el, "tagref"),
                },
            )
            count += 1
        self.stdout.write(f"  Repositories: {count}")

    # --- Citation ---
    def _import_citations(self, root):
        cits_el = root.find("citations")
        if cits_el is None:
            return
        count = 0
        for el in cits_el.findall("citation"):
            source_ref = el.find("sourceref")
            source_handle = source_ref.get("hlink") if source_ref is not None else None

            confidence_text = self._text(el, "confidence")
            confidence = int(confidence_text) if confidence_text else 2

            Citation.objects.update_or_create(
                handle=el.get("handle"),
                defaults={
                    "gramps_id": el.get("id") or None,
                    "change": float(el.get("change", 0)),
                    "private": el.get("priv") == "1",
                    "source_handle_id": source_handle,
                    "page": self._text(el, "page"),
                    "date": self._parse_date(el),
                    "confidence": confidence,
                    "media_list": self._parse_objrefs(el),
                    "note_list": self._hlinks(el, "noteref"),
                    "tag_list": self._hlinks(el, "tagref"),
                    "attribute_list": self._parse_src_attributes(el),
                },
            )
            count += 1
        self.stdout.write(f"  Citations: {count}")

    # --- Media ---
    def _import_media(self, root):
        objects_el = root.find("objects")
        if objects_el is None:
            return
        count = 0
        for el in objects_el.findall("object"):
            file_el = el.find("file")
            MediaObject.objects.update_or_create(
                handle=el.get("handle"),
                defaults={
                    "gramps_id": el.get("id") or None,
                    "change": float(el.get("change", 0)),
                    "private": el.get("priv") == "1",
                    "path": file_el.get("src", "") if file_el is not None else "",
                    "mime": file_el.get("mime", "") if file_el is not None else "",
                    "checksum": file_el.get("checksum", "") if file_el is not None else "",
                    "desc": file_el.get("description", "") if file_el is not None else "",
                    "date": self._parse_date(el),
                    "attribute_list": self._parse_attributes(el),
                    "citation_list": self._hlinks(el, "citationref"),
                    "note_list": self._hlinks(el, "noteref"),
                    "tag_list": self._hlinks(el, "tagref"),
                },
            )
            count += 1
        self.stdout.write(f"  Media: {count}")

    # --- Note ---
    def _import_notes(self, root):
        notes_el = root.find("notes")
        if notes_el is None:
            return
        count = 0
        for el in notes_el.findall("note"):
            # StyledText
            text_el = el.find("text")
            text_string = text_el.text or "" if text_el is not None else ""

            styled_tags = []
            for style in el.findall("style"):
                ranges = []
                for r in style.findall("range"):
                    ranges.append([
                        int(r.get("start", 0)),
                        int(r.get("end", 0)),
                    ])
                styled_tags.append({
                    "name": style.get("name", ""),
                    "value": style.get("value", ""),
                    "ranges": ranges,
                })

            format_val = int(el.get("format", 0))

            Note.objects.update_or_create(
                handle=el.get("handle"),
                defaults={
                    "gramps_id": el.get("id") or None,
                    "change": float(el.get("change", 0)),
                    "private": el.get("priv") == "1",
                    "type": el.get("type", "General"),
                    "format": format_val,
                    "text": {
                        "string": text_string,
                        "tags": styled_tags,
                    },
                    "tag_list": self._hlinks(el, "tagref"),
                },
            )
            count += 1
        self.stdout.write(f"  Notes: {count}")

    # --- Person ---
    def _import_people(self, root):
        people_el = root.find("people")
        if people_el is None:
            return
        count = 0
        for el in people_el.findall("person"):
            gender_str = self._text(el, "gender")
            gender = {"M": 1, "F": 0, "U": 2, "X": 3}.get(gender_str, 2)

            # Names
            names = []
            for name_el in el.findall("name"):
                names.append(self._parse_name(name_el))

            primary_name = names[0] if names else {}
            alternate_names = names[1:] if len(names) > 1 else []

            # Event refs
            event_ref_list = []
            birth_ref_index = -1
            death_ref_index = -1
            for i, er in enumerate(el.findall("eventref")):
                ref = {
                    "ref": er.get("hlink"),
                    "role": er.get("role", "Primary"),
                    "private": er.get("priv") == "1",
                    "note_list": self._hlinks(er, "noteref"),
                    "attribute_list": self._parse_attributes(er),
                }
                event_ref_list.append(ref)

                # Detect birth/death by looking up event type
                try:
                    evt = Event.objects.get(pk=er.get("hlink"))
                    if evt.type == "Birth" and birth_ref_index == -1:
                        birth_ref_index = i
                    elif evt.type == "Death" and death_ref_index == -1:
                        death_ref_index = i
                except Event.DoesNotExist:
                    pass

            # Family references
            family_list = [e.get("hlink") for e in el.findall("parentin")]
            parent_family_list = [e.get("hlink") for e in el.findall("childof")]

            # Person refs
            person_ref_list = []
            for pr in el.findall("personref"):
                person_ref_list.append({
                    "ref": pr.get("hlink"),
                    "rel": pr.get("rel", ""),
                    "private": pr.get("priv") == "1",
                    "citation_list": self._hlinks(pr, "citationref"),
                    "note_list": self._hlinks(pr, "noteref"),
                })

            Person.objects.update_or_create(
                handle=el.get("handle"),
                defaults={
                    "gramps_id": el.get("id") or None,
                    "change": float(el.get("change", 0)),
                    "private": el.get("priv") == "1",
                    "gender": gender,
                    "primary_name": primary_name,
                    "alternate_names": alternate_names,
                    "event_ref_list": event_ref_list,
                    "family_list": family_list,
                    "parent_family_list": parent_family_list,
                    "person_ref_list": person_ref_list,
                    "media_list": self._parse_objrefs(el),
                    "address_list": self._parse_addresses(el),
                    "attribute_list": self._parse_attributes(el),
                    "citation_list": self._hlinks(el, "citationref"),
                    "note_list": self._hlinks(el, "noteref"),
                    "tag_list": self._hlinks(el, "tagref"),
                    "urls": self._parse_urls(el),
                    "lds_ord_list": self._parse_lds_ords(el),
                    "birth_ref_index": birth_ref_index,
                    "death_ref_index": death_ref_index,
                },
            )
            count += 1
        self.stdout.write(f"  People: {count}")

    # --- Family ---
    def _import_families(self, root):
        families_el = root.find("families")
        if families_el is None:
            return
        count = 0
        for el in families_el.findall("family"):
            rel = el.find("rel")
            father = el.find("father")
            mother = el.find("mother")

            # Child refs
            child_ref_list = []
            for cr in el.findall("childref"):
                child_ref_list.append({
                    "ref": cr.get("hlink"),
                    "frel": cr.get("frel", "Birth"),
                    "mrel": cr.get("mrel", "Birth"),
                    "private": cr.get("priv") == "1",
                    "citation_list": self._hlinks(cr, "citationref"),
                    "note_list": self._hlinks(cr, "noteref"),
                })

            # Event refs
            event_ref_list = []
            for er in el.findall("eventref"):
                event_ref_list.append({
                    "ref": er.get("hlink"),
                    "role": er.get("role", "Primary"),
                    "private": er.get("priv") == "1",
                    "note_list": self._hlinks(er, "noteref"),
                    "attribute_list": self._parse_attributes(er),
                })

            Family.objects.update_or_create(
                handle=el.get("handle"),
                defaults={
                    "gramps_id": el.get("id") or None,
                    "change": float(el.get("change", 0)),
                    "private": el.get("priv") == "1",
                    "type": rel.get("type", "") if rel is not None else "",
                    "father_handle_id": father.get("hlink") if father is not None else None,
                    "mother_handle_id": mother.get("hlink") if mother is not None else None,
                    "child_ref_list": child_ref_list,
                    "event_ref_list": event_ref_list,
                    "media_list": self._parse_objrefs(el),
                    "attribute_list": self._parse_attributes(el),
                    "citation_list": self._hlinks(el, "citationref"),
                    "note_list": self._hlinks(el, "noteref"),
                    "tag_list": self._hlinks(el, "tagref"),
                    "lds_ord_list": self._parse_lds_ords(el),
                },
            )
            count += 1
        self.stdout.write(f"  Families: {count}")

    # === Helper methods ===

    def _text(self, el, tag):
        """Get text content of a child element."""
        child = el.find(tag)
        return child.text or "" if child is not None else ""

    def _hlinks(self, el, tag):
        """Get list of hlink attribute values from child elements."""
        return [child.get("hlink") for child in el.findall(tag) if child.get("hlink")]

    def _parse_date(self, el):
        """Parse date from XML element (dateval, daterange, datespan, datestr)."""
        dv = el.find("dateval")
        if dv is not None:
            return self._parse_dateval(dv)

        dr = el.find("daterange")
        if dr is not None:
            return self._parse_daterange(dr, modifier=4)  # MOD_RANGE

        ds = el.find("datespan")
        if ds is not None:
            return self._parse_daterange(ds, modifier=5)  # MOD_SPAN

        dstr = el.find("datestr")
        if dstr is not None:
            return {
                "calendar": 0,
                "dateval": [],
                "modifier": 6,  # MOD_TEXTONLY
                "quality": 0,
                "text": dstr.get("val", ""),
                "sortval": 0,
                "newyear": 0,
            }

        return {}

    def _parse_dateval(self, dv):
        """Parse a <dateval> element."""
        val = dv.get("val", "")
        parts = val.split("-")

        year = int(parts[0]) if len(parts) >= 1 and parts[0] else 0
        month = int(parts[1]) if len(parts) >= 2 and parts[1] else 0
        day = int(parts[2]) if len(parts) >= 3 and parts[2] else 0

        type_map = {"before": 1, "after": 2, "about": 3}
        modifier = type_map.get(dv.get("type", ""), 0)

        quality_map = {"estimated": 1, "calculated": 2}
        quality = quality_map.get(dv.get("quality", ""), 0)

        slash = dv.get("dualdated") == "1"
        sortval = year * 512 + month * 32 + day

        return {
            "calendar": 0,
            "dateval": [day, month, year, slash],
            "modifier": modifier,
            "quality": quality,
            "text": "",
            "sortval": sortval,
            "newyear": 0,
        }

    def _parse_daterange(self, el, modifier):
        """Parse <daterange> or <datespan> element."""
        start = el.get("start", "")
        stop = el.get("stop", "")

        start_parts = start.split("-")
        stop_parts = stop.split("-")

        sy = int(start_parts[0]) if len(start_parts) >= 1 and start_parts[0] else 0
        sm = int(start_parts[1]) if len(start_parts) >= 2 and start_parts[1] else 0
        sd = int(start_parts[2]) if len(start_parts) >= 3 and start_parts[2] else 0

        ey = int(stop_parts[0]) if len(stop_parts) >= 1 and stop_parts[0] else 0
        em = int(stop_parts[1]) if len(stop_parts) >= 2 and stop_parts[1] else 0
        ed = int(stop_parts[2]) if len(stop_parts) >= 3 and stop_parts[2] else 0

        quality_map = {"estimated": 1, "calculated": 2}
        quality = quality_map.get(el.get("quality", ""), 0)

        sortval = sy * 512 + sm * 32 + sd

        return {
            "calendar": 0,
            "dateval": [sd, sm, sy, False, ed, em, ey, False],
            "modifier": modifier,
            "quality": quality,
            "text": "",
            "sortval": sortval,
            "newyear": 0,
        }

    def _parse_name(self, name_el):
        """Parse a <name> element into a Name dict."""
        surname_list = []
        for sn in name_el.findall("surname"):
            surname_list.append({
                "surname": sn.text or "",
                "prefix": sn.get("prefix", ""),
                "primary": sn.get("prim") == "1",
                "origintype": sn.get("derivation", ""),
                "connector": sn.get("connector", ""),
            })

        return {
            "type": name_el.get("type", "Birth Name"),
            "first_name": self._text(name_el, "first"),
            "suffix": self._text(name_el, "suffix"),
            "title": self._text(name_el, "title"),
            "call": self._text(name_el, "call"),
            "nick": self._text(name_el, "nick"),
            "famnick": self._text(name_el, "familynick"),
            "group_as": self._text(name_el, "group"),
            "surname_list": surname_list,
            "display_as": 0,
            "sort_as": 0,
            "date": self._parse_date(name_el),
            "private": name_el.get("priv") == "1",
            "citation_list": self._hlinks(name_el, "citationref"),
            "note_list": self._hlinks(name_el, "noteref"),
        }

    def _parse_attributes(self, el):
        """Parse <attribute> elements."""
        attrs = []
        for attr in el.findall("attribute"):
            attrs.append({
                "type": attr.get("type", ""),
                "value": attr.get("value", ""),
                "private": attr.get("priv") == "1",
                "citation_list": self._hlinks(attr, "citationref"),
                "note_list": self._hlinks(attr, "noteref"),
            })
        return attrs

    def _parse_src_attributes(self, el):
        """Parse <srcattribute> elements."""
        attrs = []
        for attr in el.findall("srcattribute"):
            attrs.append({
                "type": attr.get("type", ""),
                "value": attr.get("value", ""),
                "private": attr.get("priv") == "1",
            })
        return attrs

    def _parse_objrefs(self, el):
        """Parse <objref> (media reference) elements."""
        refs = []
        for objref in el.findall("objref"):
            ref = {
                "ref": objref.get("hlink"),
                "private": objref.get("priv") == "1",
                "citation_list": self._hlinks(objref, "citationref"),
                "note_list": self._hlinks(objref, "noteref"),
                "attribute_list": self._parse_attributes(objref),
            }
            region = objref.find("region")
            if region is not None:
                ref["rect"] = [
                    int(region.get("corner1_x", 0)),
                    int(region.get("corner1_y", 0)),
                    int(region.get("corner2_x", 100)),
                    int(region.get("corner2_y", 100)),
                ]
            else:
                ref["rect"] = []
            refs.append(ref)
        return refs

    def _parse_urls(self, el):
        """Parse <url> elements."""
        urls = []
        for url in el.findall("url"):
            urls.append({
                "path": url.get("href", ""),
                "desc": url.get("description", ""),
                "type": url.get("type", ""),
                "private": url.get("priv") == "1",
            })
        return urls

    def _parse_addresses(self, el):
        """Parse <address> elements."""
        addresses = []
        for addr in el.findall("address"):
            addresses.append({
                "street": self._text(addr, "street"),
                "locality": self._text(addr, "locality"),
                "city": self._text(addr, "city"),
                "county": self._text(addr, "county"),
                "state": self._text(addr, "state"),
                "country": self._text(addr, "country"),
                "postal": self._text(addr, "postal"),
                "phone": self._text(addr, "phone"),
                "date": self._parse_date(addr),
                "private": addr.get("priv") == "1",
                "citation_list": self._hlinks(addr, "citationref"),
                "note_list": self._hlinks(addr, "noteref"),
            })
        return addresses

    def _parse_lds_ords(self, el):
        """Parse <lds_ord> elements."""
        ords = []
        for lds in el.findall("lds_ord"):
            temple = lds.find("temple")
            status_el = lds.find("status")
            place = lds.find("place")
            sealed = lds.find("sealed_to")
            ords.append({
                "type": lds.get("type", ""),
                "date": self._parse_date(lds),
                "temple": temple.get("val", "") if temple is not None else "",
                "status": status_el.get("val", "") if status_el is not None else "",
                "place": place.get("hlink", "") if place is not None else "",
                "sealed_to": sealed.get("hlink", "") if sealed is not None else "",
                "private": lds.get("priv") == "1",
                "citation_list": self._hlinks(lds, "citationref"),
                "note_list": self._hlinks(lds, "noteref"),
            })
        return ords

    def _build_backlinks(self):
        """Populate BacklinkIndex for all objects."""
        BacklinkIndex.objects.all().delete()
        models_types = [
            (Person, "Person"),
            (Family, "Family"),
            (Event, "Event"),
            (Place, "Place"),
            (Source, "Source"),
            (Citation, "Citation"),
            (Repository, "Repository"),
            (MediaObject, "Media"),
            (Note, "Note"),
        ]
        for model, type_name in models_types:
            for obj in model.objects.all():
                populate_backlinks_for_object(obj, type_name)

    def _print_counts(self):
        self.stdout.write(f"\nDatabase totals:")
        self.stdout.write(f"  People:       {Person.objects.count()}")
        self.stdout.write(f"  Families:     {Family.objects.count()}")
        self.stdout.write(f"  Events:       {Event.objects.count()}")
        self.stdout.write(f"  Places:       {Place.objects.count()}")
        self.stdout.write(f"  Sources:      {Source.objects.count()}")
        self.stdout.write(f"  Citations:    {Citation.objects.count()}")
        self.stdout.write(f"  Repositories: {Repository.objects.count()}")
        self.stdout.write(f"  Media:        {MediaObject.objects.count()}")
        self.stdout.write(f"  Notes:        {Note.objects.count()}")
        self.stdout.write(f"  Tags:         {Tag.objects.count()}")
        self.stdout.write(f"  Backlinks:    {BacklinkIndex.objects.count()}")
