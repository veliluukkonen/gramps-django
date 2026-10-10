"""
Gramps XML (.gramps) importer.

Reads a Gramps XML file (gzip-compressed or plain, DTD 1.7.x) and merges
its content into the database: objects with a known handle are updated,
unknown ones are created. The logic is shared by the
``import_gramps_xml`` management command and ``POST /api/importers/gramps/file``.

Handles
-------
Gramps XML prefixes every handle with an underscore (``handle="_abc"``,
``hlink="_abc"``); Gramps itself strips the underscore on import. Older
versions of this importer stored the handle verbatim (with the underscore),
so existing databases may contain either form. ``import_gramps_xml`` keeps
both working: for every handle found in the file it looks for an existing
object with the stripped handle *or* the legacy underscored handle and
re-uses whichever exists; new objects get the stripped (Gramps) handle.

Dates are stored in the Gramps JSON form, see ``apps.core.dates``.
"""

import gzip
import xml.etree.ElementTree as ET

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

# Order matters: foreign-key targets are imported before their referrers.
MODELS = [
    ("Tag", Tag),
    ("Note", Note),
    ("Repository", Repository),
    ("Source", Source),
    ("Citation", Citation),
    ("Place", Place),
    ("Media", MediaObject),
    ("Event", Event),
    ("Person", Person),
    ("Family", Family),
]

# gramps.gen.lib.date.Date constants
MOD_NONE, MOD_BEFORE, MOD_AFTER, MOD_ABOUT, MOD_RANGE, MOD_SPAN = 0, 1, 2, 3, 4, 5
MOD_TEXTONLY, MOD_FROM, MOD_TO = 6, 7, 8
QUAL_ESTIMATED, QUAL_CALCULATED = 1, 2
CALENDAR_NAMES = [
    "Gregorian", "Julian", "Hebrew", "French Republican",
    "Persian", "Islamic", "Swedish",
]
NEWYEAR_CODES = {"": 0, "Jan1": 0, "Mar1": 1, "Mar25": 2, "Sep1": 3}
GENDER_CODES = {"M": Person.MALE, "F": Person.FEMALE, "U": Person.UNKNOWN, "X": Person.OTHER}

_SQL_CHUNK = 500  # keep IN (...) lists below the SQLite variable limit


class ImportError_(ValueError):
    """Raised for files that cannot be imported."""


def open_gramps_file(source):
    """
    Return a binary file object for a .gramps file (gzipped or plain XML).

    ``source`` is a path or a binary file object.
    """
    if hasattr(source, "read"):
        fileobj = source
        fileobj.seek(0)
        head = fileobj.read(2)
        fileobj.seek(0)
    else:
        fileobj = open(source, "rb")
        head = fileobj.read(2)
        fileobj.seek(0)
    if head == b"\x1f\x8b":
        return gzip.GzipFile(fileobj=fileobj, mode="rb")
    return fileobj


def parse_gramps_xml(source):
    """Parse a .gramps file and return the root element (namespace stripped)."""
    fileobj = open_gramps_file(source)
    try:
        root = ET.parse(fileobj).getroot()
    except ET.ParseError as exc:
        raise ImportError_(f"Not a valid Gramps XML file: {exc}")
    for elem in root.iter():
        if "}" in elem.tag:
            elem.tag = elem.tag.split("}", 1)[1]
    if root.tag != "database":
        raise ImportError_("Not a Gramps XML file (missing <database> root element)")
    return root


def import_gramps_xml(source, clear=False, log=None):
    """
    Import a Gramps XML file (path or binary file object) into the database.

    ``clear`` deletes all existing objects first. ``log`` is an optional
    callable receiving progress strings. Returns a dict with the number of
    imported objects per class plus ``total``.
    """
    root = parse_gramps_xml(source)
    importer = GrampsXmlImporter(root, log=log)
    return importer.run(clear=clear)


def clear_all_objects():
    """Delete every Gramps object (not users, config or history)."""
    BacklinkIndex.objects.all().delete()
    for _, model in reversed(MODELS):
        model.objects.all().delete()


def strip_handle(value):
    """Gramps XML handle/hlink -> stored handle (leading underscore removed)."""
    if value is None:
        return None
    return value.lstrip("_")


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class GrampsXmlImporter:
    """Imports the content of a parsed Gramps XML tree."""

    def __init__(self, root, log=None):
        self.root = root
        self.log = log or (lambda msg: None)
        self.handle_map = {}  # stripped file handle -> database handle
        self.imported = {name: [] for name, _ in MODELS}  # class -> handles
        self.counts = {}
        self._event_types = {}

    # --- driver --------------------------------------------------------

    def run(self, clear=False):
        if clear:
            self.log("Clearing existing data...")
            clear_all_objects()
        with transaction.atomic():
            self._build_handle_map()
            self._import_tags()
            self._import_notes()
            self._import_repositories()
            self._import_sources()
            self._import_citations()
            self._import_places()
            self._import_media()
            self._import_events()
            self._import_people()
            self._import_families()
            self.log("Building backlink index...")
            self._build_backlinks()
        self.counts["total"] = sum(len(v) for v in self.imported.values())
        return dict(self.counts)

    # --- handle resolution ---------------------------------------------

    def _build_handle_map(self):
        """Map every handle mentioned in the file to the handle to store."""
        mentioned = set()
        for elem in self.root.iter():
            for attr in ("handle", "hlink"):
                value = elem.get(attr)
                if value:
                    mentioned.add(strip_handle(value))
        mentioned.discard("")
        candidates = list(mentioned) + ["_" + h for h in mentioned]
        existing = set()
        for _, model in MODELS:
            for i in range(0, len(candidates), _SQL_CHUNK):
                chunk = candidates[i:i + _SQL_CHUNK]
                existing.update(
                    model.objects.filter(handle__in=chunk).values_list("handle", flat=True)
                )
        for handle in mentioned:
            if handle in existing:
                self.handle_map[handle] = handle
            elif "_" + handle in existing:
                self.handle_map[handle] = "_" + handle
            else:
                self.handle_map[handle] = handle

    def _h(self, value):
        """Resolve a raw handle/hlink attribute value to the stored handle."""
        if not value:
            return None
        stripped = strip_handle(value)
        return self.handle_map.get(stripped, stripped)

    def _hlinks(self, el, tag):
        return [self._h(c.get("hlink")) for c in el.findall(tag) if c.get("hlink")]

    def _existing(self, model):
        """Set of handles of ``model`` currently in the database."""
        return set(model.objects.values_list("handle", flat=True))

    # --- generic helpers -----------------------------------------------

    def _save(self, class_name, model, handle, defaults):
        model.objects.update_or_create(handle=handle, defaults=defaults)
        self.imported[class_name].append(handle)

    def _done(self, class_name, label):
        count = len(self.imported[class_name])
        self.counts[class_name.lower()] = count
        self.log(f"  {label}: {count}")

    @staticmethod
    def _text(el, tag):
        child = el.find(tag)
        if child is None:
            return ""
        return child.text or ""

    @staticmethod
    def _priv(el):
        return el.get("priv") == "1"

    @staticmethod
    def _common(el):
        """Attributes shared by all primary objects."""
        return {
            "gramps_id": el.get("id") or None,
            "change": float(el.get("change") or 0),
            "private": el.get("priv") == "1",
        }

    # --- object types --------------------------------------------------

    def _import_tags(self):
        parent = self.root.find("tags")
        for el in (parent.findall("tag") if parent is not None else []):
            self._save("Tag", Tag, self._h(el.get("handle")), {
                "name": el.get("name", ""),
                "color": el.get("color", "#000000000000"),
                "priority": _int(el.get("priority")),
                "change": float(el.get("change") or 0),
            })
        self._done("Tag", "Tags")

    def _import_notes(self):
        parent = self.root.find("notes")
        for el in (parent.findall("note") if parent is not None else []):
            styles = []
            for style in el.findall("style"):
                styles.append({
                    "name": style.get("name", ""),
                    "value": style.get("value", ""),
                    "ranges": [
                        [_int(r.get("start")), _int(r.get("end"))]
                        for r in style.findall("range")
                    ],
                })
            self._save("Note", Note, self._h(el.get("handle")), {
                **self._common(el),
                "type": el.get("type", "General"),
                "format": _int(el.get("format")),
                "text": {"string": self._text(el, "text"), "tags": styles},
                "tag_list": self._hlinks(el, "tagref"),
            })
        self._done("Note", "Notes")

    def _import_repositories(self):
        parent = self.root.find("repositories")
        for el in (parent.findall("repository") if parent is not None else []):
            self._save("Repository", Repository, self._h(el.get("handle")), {
                **self._common(el),
                "name": self._text(el, "rname"),
                "type": self._text(el, "type"),
                "address_list": self._parse_addresses(el),
                "urls": self._parse_urls(el),
                "note_list": self._hlinks(el, "noteref"),
                "tag_list": self._hlinks(el, "tagref"),
            })
        self._done("Repository", "Repositories")

    def _import_sources(self):
        parent = self.root.find("sources")
        for el in (parent.findall("source") if parent is not None else []):
            reporef_list = []
            for rr in el.findall("reporef"):
                reporef_list.append({
                    "ref": self._h(rr.get("hlink")),
                    "call_number": rr.get("callno", ""),
                    "media_type": rr.get("medium", ""),
                    "private": self._priv(rr),
                    "note_list": self._hlinks(rr, "noteref"),
                })
            self._save("Source", Source, self._h(el.get("handle")), {
                **self._common(el),
                "title": self._text(el, "stitle"),
                "author": self._text(el, "sauthor"),
                "pubinfo": self._text(el, "spubinfo"),
                "abbrev": self._text(el, "sabbrev"),
                "reporef_list": reporef_list,
                "media_list": self._parse_objrefs(el),
                "note_list": self._hlinks(el, "noteref"),
                "tag_list": self._hlinks(el, "tagref"),
                "attribute_list": self._parse_src_attributes(el),
            })
        self._done("Source", "Sources")

    def _import_citations(self):
        parent = self.root.find("citations")
        sources = self._existing(Source)
        for el in (parent.findall("citation") if parent is not None else []):
            sourceref = el.find("sourceref")
            source_handle = self._h(sourceref.get("hlink")) if sourceref is not None else None
            if source_handle not in sources:
                source_handle = None
            confidence = self._text(el, "confidence")
            self._save("Citation", Citation, self._h(el.get("handle")), {
                **self._common(el),
                "source_handle_id": source_handle,
                "page": self._text(el, "page"),
                "date": self._parse_date(el),
                "confidence": _int(confidence, Citation.CONF_NORMAL),
                "media_list": self._parse_objrefs(el),
                "note_list": self._hlinks(el, "noteref"),
                "tag_list": self._hlinks(el, "tagref"),
                "attribute_list": self._parse_src_attributes(el),
            })
        self._done("Citation", "Citations")

    def _import_places(self):
        parent = self.root.find("places")
        for el in (parent.findall("placeobj") if parent is not None else []):
            names = []
            for pn in el.findall("pname"):
                name = {"value": pn.get("value", ""), "lang": pn.get("lang", "")}
                date = self._parse_date(pn)
                if date:
                    name["date"] = date
                names.append(name)
            placeref_list = []
            for pr in el.findall("placeref"):
                ref = {"ref": self._h(pr.get("hlink"))}
                date = self._parse_date(pr)
                if date:
                    ref["date"] = date
                placeref_list.append(ref)
            alt_loc = [
                {
                    k: loc.get(k, "")
                    for k in ("street", "locality", "city", "parish", "county",
                              "state", "country", "postal", "phone")
                }
                for loc in el.findall("location")
            ]
            coord = el.find("coord")
            self._save("Place", Place, self._h(el.get("handle")), {
                **self._common(el),
                "title": self._text(el, "ptitle"),
                "name": names[0] if names else {},
                "alt_names": names[1:],
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
            })
        self._done("Place", "Places")

    def _import_media(self):
        parent = self.root.find("objects")
        for el in (parent.findall("object") if parent is not None else []):
            file_el = el.find("file")
            get = file_el.get if file_el is not None else (lambda k, d="": d)
            self._save("Media", MediaObject, self._h(el.get("handle")), {
                **self._common(el),
                "path": get("src", ""),
                "mime": get("mime", ""),
                "checksum": get("checksum", ""),
                "desc": get("description", ""),
                "date": self._parse_date(el),
                "attribute_list": self._parse_attributes(el),
                "citation_list": self._hlinks(el, "citationref"),
                "note_list": self._hlinks(el, "noteref"),
                "tag_list": self._hlinks(el, "tagref"),
            })
        self._done("Media", "Media")

    def _import_events(self):
        parent = self.root.find("events")
        places = self._existing(Place)
        for el in (parent.findall("event") if parent is not None else []):
            place_el = el.find("place")
            place_handle = self._h(place_el.get("hlink")) if place_el is not None else None
            if place_handle not in places:
                place_handle = None
            event_type = self._text(el, "type")
            handle = self._h(el.get("handle"))
            self._event_types[handle] = event_type
            self._save("Event", Event, handle, {
                **self._common(el),
                "type": event_type,
                "date": self._parse_date(el),
                "place_id": place_handle,
                "description": self._text(el, "description"),
                "citation_list": self._hlinks(el, "citationref"),
                "media_list": self._parse_objrefs(el),
                "note_list": self._hlinks(el, "noteref"),
                "tag_list": self._hlinks(el, "tagref"),
                "attribute_list": self._parse_attributes(el),
            })
        self._done("Event", "Events")

    def _import_people(self):
        parent = self.root.find("people")
        if parent is None:
            self._done("Person", "People")
            return
        # Birth/death detection needs the event types of referenced events.
        event_types = dict(
            Event.objects.filter(type__in=["Birth", "Death"]).values_list("handle", "type")
        )
        event_types.update(self._event_types)
        for el in parent.findall("person"):
            primary_name = {}
            alternate_names = []
            for name_el in el.findall("name"):
                name = self._parse_name(name_el)
                if name_el.get("alt") == "1" or primary_name:
                    alternate_names.append(name)
                else:
                    primary_name = name

            event_ref_list = []
            birth_ref_index = death_ref_index = -1
            for i, er in enumerate(el.findall("eventref")):
                ref = self._parse_eventref(er)
                event_ref_list.append(ref)
                if ref["role"] not in ("Primary", ""):
                    continue
                etype = event_types.get(ref["ref"])
                if etype == "Birth" and birth_ref_index < 0:
                    birth_ref_index = i
                elif etype == "Death" and death_ref_index < 0:
                    death_ref_index = i

            person_ref_list = [
                {
                    "ref": self._h(pr.get("hlink")),
                    "rel": pr.get("rel", ""),
                    "private": self._priv(pr),
                    "citation_list": self._hlinks(pr, "citationref"),
                    "note_list": self._hlinks(pr, "noteref"),
                }
                for pr in el.findall("personref")
            ]
            self._save("Person", Person, self._h(el.get("handle")), {
                **self._common(el),
                "gender": GENDER_CODES.get(self._text(el, "gender"), Person.UNKNOWN),
                "primary_name": primary_name,
                "alternate_names": alternate_names,
                "event_ref_list": event_ref_list,
                "family_list": self._hlinks(el, "parentin"),
                "parent_family_list": self._hlinks(el, "childof"),
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
            })
        self._done("Person", "People")

    def _import_families(self):
        parent = self.root.find("families")
        people = self._existing(Person)
        for el in (parent.findall("family") if parent is not None else []):
            rel = el.find("rel")
            father = el.find("father")
            mother = el.find("mother")
            father_handle = self._h(father.get("hlink")) if father is not None else None
            mother_handle = self._h(mother.get("hlink")) if mother is not None else None
            child_ref_list = [
                {
                    "ref": self._h(cr.get("hlink")),
                    "frel": cr.get("frel", "Birth"),
                    "mrel": cr.get("mrel", "Birth"),
                    "private": self._priv(cr),
                    "citation_list": self._hlinks(cr, "citationref"),
                    "note_list": self._hlinks(cr, "noteref"),
                }
                for cr in el.findall("childref")
            ]
            self._save("Family", Family, self._h(el.get("handle")), {
                **self._common(el),
                "type": rel.get("type", "") if rel is not None else "",
                "father_handle_id": father_handle if father_handle in people else None,
                "mother_handle_id": mother_handle if mother_handle in people else None,
                "child_ref_list": child_ref_list,
                "event_ref_list": [self._parse_eventref(er) for er in el.findall("eventref")],
                "media_list": self._parse_objrefs(el),
                "attribute_list": self._parse_attributes(el),
                "citation_list": self._hlinks(el, "citationref"),
                "note_list": self._hlinks(el, "noteref"),
                "tag_list": self._hlinks(el, "tagref"),
                "lds_ord_list": self._parse_lds_ords(el),
            })
        self._done("Family", "Families")

    # --- secondary objects ---------------------------------------------

    def _parse_eventref(self, er):
        return {
            "ref": self._h(er.get("hlink")),
            "role": er.get("role", "Primary"),
            "private": self._priv(er),
            "note_list": self._hlinks(er, "noteref"),
            "citation_list": self._hlinks(er, "citationref"),
            "attribute_list": self._parse_attributes(er),
        }

    def _parse_name(self, name_el):
        surname_list = []
        for sn in name_el.findall("surname"):
            surname_list.append({
                "surname": sn.text or "",
                "prefix": sn.get("prefix", ""),
                "primary": sn.get("prim", "1") != "0",
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
            "display_as": _int(name_el.get("display")),
            "sort_as": _int(name_el.get("sort")),
            "date": self._parse_date(name_el),
            "private": self._priv(name_el),
            "citation_list": self._hlinks(name_el, "citationref"),
            "note_list": self._hlinks(name_el, "noteref"),
        }

    def _parse_attributes(self, el):
        return [
            {
                "type": attr.get("type", ""),
                "value": attr.get("value", ""),
                "private": self._priv(attr),
                "citation_list": self._hlinks(attr, "citationref"),
                "note_list": self._hlinks(attr, "noteref"),
            }
            for attr in el.findall("attribute")
        ]

    def _parse_src_attributes(self, el):
        return [
            {
                "type": attr.get("type", ""),
                "value": attr.get("value", ""),
                "private": self._priv(attr),
            }
            for attr in el.findall("srcattribute")
        ]

    def _parse_objrefs(self, el):
        refs = []
        for objref in el.findall("objref"):
            region = objref.find("region")
            rect = []
            if region is not None:
                rect = [
                    _int(region.get("corner1_x"), 0),
                    _int(region.get("corner1_y"), 0),
                    _int(region.get("corner2_x"), 100),
                    _int(region.get("corner2_y"), 100),
                ]
            refs.append({
                "ref": self._h(objref.get("hlink")),
                "rect": rect,
                "private": self._priv(objref),
                "citation_list": self._hlinks(objref, "citationref"),
                "note_list": self._hlinks(objref, "noteref"),
                "attribute_list": self._parse_attributes(objref),
            })
        return refs

    def _parse_urls(self, el):
        return [
            {
                "path": url.get("href", ""),
                "desc": url.get("description", ""),
                "type": url.get("type", ""),
                "private": self._priv(url),
            }
            for url in el.findall("url")
        ]

    def _parse_addresses(self, el):
        return [
            {
                "street": self._text(addr, "street"),
                "locality": self._text(addr, "locality"),
                "city": self._text(addr, "city"),
                "county": self._text(addr, "county"),
                "state": self._text(addr, "state"),
                "country": self._text(addr, "country"),
                "postal": self._text(addr, "postal"),
                "phone": self._text(addr, "phone"),
                "date": self._parse_date(addr),
                "private": self._priv(addr),
                "citation_list": self._hlinks(addr, "citationref"),
                "note_list": self._hlinks(addr, "noteref"),
            }
            for addr in el.findall("address")
        ]

    def _parse_lds_ords(self, el):
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
                "place": self._h(place.get("hlink")) if place is not None else "",
                "sealed_to": self._h(sealed.get("hlink")) if sealed is not None else "",
                "private": self._priv(lds),
                "citation_list": self._hlinks(lds, "citationref"),
                "note_list": self._hlinks(lds, "noteref"),
            })
        return ords

    # --- dates -----------------------------------------------------------

    def _parse_date(self, el):
        """Parse the date child (dateval, daterange, datespan or datestr)."""
        dv = el.find("dateval")
        if dv is not None:
            return self._parse_dateval(dv)
        dr = el.find("daterange")
        if dr is not None:
            return self._parse_compound(dr, MOD_RANGE)
        ds = el.find("datespan")
        if ds is not None:
            return self._parse_compound(ds, MOD_SPAN)
        dstr = el.find("datestr")
        if dstr is not None:
            return self._date_dict([], MOD_TEXTONLY, 0, 0, 0, dstr.get("val", ""), 0)
        return {}

    @staticmethod
    def _iso_parts(value):
        """'1864-10-02' / '????-05' -> (day, month, year); unknown parts are 0."""
        parts = (value or "").split("-")
        negative = False
        if parts and parts[0] == "" and len(parts) > 1:
            # negative year, e.g. "-0100-01-01"
            negative = True
            parts = parts[1:]
        year = _int(parts[0]) if len(parts) >= 1 else 0
        month = _int(parts[1]) if len(parts) >= 2 else 0
        day = _int(parts[2]) if len(parts) >= 3 else 0
        return day, month, -year if negative else year

    @staticmethod
    def _date_meta(el):
        quality = {"estimated": QUAL_ESTIMATED, "calculated": QUAL_CALCULATED}.get(
            el.get("quality", ""), 0
        )
        cformat = el.get("cformat", "")
        calendar = CALENDAR_NAMES.index(cformat) if cformat in CALENDAR_NAMES else 0
        newyear_str = el.get("newyear", "")
        if newyear_str in NEWYEAR_CODES:
            newyear = NEWYEAR_CODES[newyear_str]
        else:
            try:
                month, day = newyear_str.split("-")
                newyear = [int(month), int(day)]
            except ValueError:
                newyear = 0
        slash = el.get("dualdated") == "1"
        return quality, calendar, newyear, slash

    @staticmethod
    def _date_dict(dateval, modifier, quality, calendar, newyear, text, sortval):
        return {
            "calendar": calendar,
            "dateval": dateval,
            "modifier": modifier,
            "quality": quality,
            "text": text,
            "sortval": sortval,
            "newyear": newyear,
        }

    def _parse_dateval(self, dv):
        day, month, year = self._iso_parts(dv.get("val", ""))
        modifier = {
            "before": MOD_BEFORE, "after": MOD_AFTER, "about": MOD_ABOUT,
            "from": MOD_FROM, "to": MOD_TO,
        }.get(dv.get("type", ""), MOD_NONE)
        quality, calendar, newyear, slash = self._date_meta(dv)
        return self._date_dict(
            [day, month, year, slash], modifier, quality, calendar, newyear, "",
            year * 512 + month * 32 + day,
        )

    def _parse_compound(self, el, modifier):
        sd, sm, sy = self._iso_parts(el.get("start", ""))
        ed, em, ey = self._iso_parts(el.get("stop", ""))
        quality, calendar, newyear, slash = self._date_meta(el)
        return self._date_dict(
            [sd, sm, sy, slash, ed, em, ey, slash], modifier, quality, calendar,
            newyear, "", sy * 512 + sm * 32 + sd,
        )

    # --- backlinks -------------------------------------------------------

    def _build_backlinks(self):
        """Refresh the backlink index for every imported object."""
        for class_name, model in MODELS:
            if class_name == "Tag":
                continue
            handles = self.imported[class_name]
            for i in range(0, len(handles), _SQL_CHUNK):
                for obj in model.objects.filter(handle__in=handles[i:i + _SQL_CHUNK]):
                    populate_backlinks_for_object(obj, class_name)


def object_counts():
    """Current number of objects per class (for logging)."""
    counts = {name.lower(): model.objects.count() for name, model in MODELS}
    counts["backlinks"] = BacklinkIndex.objects.count()
    return counts
