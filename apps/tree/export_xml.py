"""
Gramps XML exporter: the inverse of ``apps.migration.importer``.

Writes a Gramps XML 1.7.2 document (optionally gzip-compressed, which is
what a ``.gramps`` file is) from the database. The element order follows
the Gramps DTD and the layout of Gramps' own ``exportxml.py`` so that the
output can be opened by Gramps desktop and re-imported here.

Handles are written with the leading underscore Gramps expects
(``handle="_<handle>"``); legacy handles that already start with an
underscore are not doubled.
"""

import gzip
import time
from xml.sax.saxutils import escape

from apps.core.models import (
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

XML_VERSION = "1.7.2"
GRAMPS_VERSION = "5.2.0"
NAMESPACE = f"http://gramps-project.org/xml/{XML_VERSION}/"

MOD_BEFORE, MOD_AFTER, MOD_ABOUT, MOD_RANGE, MOD_SPAN = 1, 2, 3, 4, 5
MOD_TEXTONLY, MOD_FROM, MOD_TO = 6, 7, 8
CALENDAR_NAMES = [
    "Gregorian", "Julian", "Hebrew", "French Republican",
    "Persian", "Islamic", "Swedish",
]
GENDER_XML = {Person.MALE: "M", Person.FEMALE: "F", Person.OTHER: "X"}


def attr(value):
    """Escape a string for use inside a double-quoted XML attribute."""
    return escape(str(value), {'"': "&quot;", "\n": "&#10;", "\r": "&#13;", "\t": "&#9;"})


def text(value):
    return escape(str(value))


def xml_handle(handle):
    return "_" + str(handle).lstrip("_")


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class GrampsXmlWriter:
    """Serialises the whole database to Gramps XML."""

    def __init__(self, out, exclude_private=False):
        """``out`` is a text file object; ``exclude_private`` drops private data."""
        self.out = out
        self.exclude_private = exclude_private
        self.excluded = set()  # handles of private primary objects (when excluding)

    # --- low level helpers ------------------------------------------------

    def w(self, indent, line):
        self.out.write("  " * indent + line + "\n")

    def ref(self, handle):
        """Return the handle to write, or None if it must be dropped."""
        if not handle:
            return None
        if handle in self.excluded:
            return None
        return handle

    def keep(self, obj):
        """True if a secondary object (dict with optional 'private') is kept."""
        if not self.exclude_private:
            return True
        return not (isinstance(obj, dict) and obj.get("private"))

    def write_ref(self, indent, tag, handle, extra="", close=True):
        handle = self.ref(handle)
        if handle is None:
            return False
        end = "/>" if close else ">"
        self.w(indent, f'<{tag} hlink="{attr(xml_handle(handle))}"{extra}{end}')
        return True

    def write_refs(self, indent, tag, handles):
        for handle in handles or []:
            self.write_ref(indent, tag, handle)

    def write_line(self, indent, tag, value):
        if value:
            self.w(indent, f"<{tag}>{text(value)}</{tag}>")

    def priv(self, obj):
        private = obj.private if hasattr(obj, "private") else obj.get("private")
        return ' priv="1"' if private else ""

    def primary_tag(self, indent, tag, obj, extra="", close=True):
        change = _int(obj.change)
        line = (
            f'<{tag} handle="{attr(xml_handle(obj.handle))}" change="{change}"'
            f' id="{attr(obj.gramps_id or "")}"{self.priv(obj)}{extra}'
        )
        self.w(indent, line + (">" if close else ""))

    # --- dates ---------------------------------------------------------------

    @staticmethod
    def iso_date(day, month, year):
        if year == 0:
            y = "????"
        else:
            y = f"{year:04d}" if year > 0 else f"-{-year:04d}"
        if month == 0:
            m = "-??" if day else ""
        else:
            m = f"-{month:02d}"
        d = f"-{day:02d}" if day else ""
        value = f"{y}{m}{d}"
        if value.replace("-", "").replace("?", "") == "":
            return ""
        return value

    def write_date(self, indent, date):
        if not isinstance(date, dict) or not date:
            return
        modifier = _int(date.get("modifier"))
        dateval = date.get("dateval") or []
        quality = _int(date.get("quality"))
        qual = {1: ' quality="estimated"', 2: ' quality="calculated"'}.get(quality, "")
        calendar = _int(date.get("calendar"))
        cformat = (
            f' cformat="{CALENDAR_NAMES[calendar]}"'
            if 0 < calendar < len(CALENDAR_NAMES) else ""
        )
        dual = ' dualdated="1"' if len(dateval) > 3 and dateval[3] else ""
        newyear = date.get("newyear") or 0
        if isinstance(newyear, (list, tuple)) and len(newyear) == 2:
            ny = f' newyear="{newyear[0]}-{newyear[1]}"'
        else:
            ny_str = {1: "Mar1", 2: "Mar25", 3: "Sep1"}.get(_int(newyear), "")
            ny = f' newyear="{ny_str}"' if ny_str else ""
        extra = f"{qual}{cformat}{dual}{ny}"

        if modifier == MOD_TEXTONLY:
            if date.get("text"):
                self.w(indent, f'<datestr val="{attr(date.get("text"))}"/>')
            return
        if len(dateval) < 3:
            return
        start = self.iso_date(_int(dateval[0]), _int(dateval[1]), _int(dateval[2]))
        if modifier in (MOD_RANGE, MOD_SPAN):
            stop = ""
            if len(dateval) >= 7:
                stop = self.iso_date(_int(dateval[4]), _int(dateval[5]), _int(dateval[6]))
            if start or stop:
                tag = "daterange" if modifier == MOD_RANGE else "datespan"
                self.w(indent, f'<{tag} start="{start}" stop="{stop}"{extra}/>')
            return
        if not start:
            return
        mode = {
            MOD_BEFORE: "before", MOD_AFTER: "after", MOD_ABOUT: "about",
            MOD_FROM: "from", MOD_TO: "to",
        }.get(modifier)
        mode_str = f' type="{mode}"' if mode else ""
        self.w(indent, f'<dateval val="{start}"{mode_str}{extra}/>')

    # --- secondary objects ----------------------------------------------------

    def write_attributes(self, indent, attrs, tag="attribute"):
        for a in attrs or []:
            if not isinstance(a, dict) or not self.keep(a):
                continue
            head = (
                f'<{tag}{self.priv(a)} type="{attr(a.get("type", ""))}"'
                f' value="{attr(a.get("value", ""))}"'
            )
            citations = [h for h in a.get("citation_list") or [] if self.ref(h)]
            notes = [h for h in a.get("note_list") or [] if self.ref(h)]
            if tag == "srcattribute" or not (citations or notes):
                self.w(indent, head + "/>")
                continue
            self.w(indent, head + ">")
            self.write_refs(indent + 1, "citationref", citations)
            self.write_refs(indent + 1, "noteref", notes)
            self.w(indent, f"</{tag}>")

    def write_media_refs(self, indent, refs):
        for r in refs or []:
            if not isinstance(r, dict) or not self.keep(r):
                continue
            handle = self.ref(r.get("ref"))
            if handle is None:
                continue
            rect = r.get("rect") or []
            region = None
            if len(rect) == 4:
                x1, y1, x2, y2 = [_int(v) for v in rect]
                if not (x1 == y1 == x2 == y2 == 0) and not (x1 == y1 == 0 and x2 == y2 == 100):
                    region = (x1, y1, x2, y2)
            attrs = [a for a in r.get("attribute_list") or [] if self.keep(a)]
            citations = [h for h in r.get("citation_list") or [] if self.ref(h)]
            notes = [h for h in r.get("note_list") or [] if self.ref(h)]
            head = f'<objref hlink="{attr(xml_handle(handle))}"{self.priv(r)}'
            if region is None and not (attrs or citations or notes):
                self.w(indent, head + "/>")
                continue
            self.w(indent, head + ">")
            if region is not None:
                self.w(
                    indent + 1,
                    f'<region corner1_x="{region[0]}" corner1_y="{region[1]}"'
                    f' corner2_x="{region[2]}" corner2_y="{region[3]}"/>',
                )
            self.write_attributes(indent + 1, attrs)
            self.write_refs(indent + 1, "citationref", citations)
            self.write_refs(indent + 1, "noteref", notes)
            self.w(indent, "</objref>")

    def write_event_refs(self, indent, refs):
        for r in refs or []:
            if not isinstance(r, dict) or not self.keep(r):
                continue
            handle = self.ref(r.get("ref"))
            if handle is None:
                continue
            role = r.get("role", "")
            if isinstance(role, dict):
                role = role.get("string", "")
            role_str = f' role="{attr(role)}"' if role else ""
            attrs = [a for a in r.get("attribute_list") or [] if self.keep(a)]
            notes = [h for h in r.get("note_list") or [] if self.ref(h)]
            citations = [h for h in r.get("citation_list") or [] if self.ref(h)]
            extra = f"{self.priv(r)}{role_str}"
            if not (attrs or notes or citations):
                self.write_ref(indent, "eventref", handle, extra)
                continue
            self.write_ref(indent, "eventref", handle, extra, close=False)
            self.write_attributes(indent + 1, attrs)
            self.write_refs(indent + 1, "noteref", notes)
            self.write_refs(indent + 1, "citationref", citations)
            self.w(indent, "</eventref>")

    def write_cit_note_ref(self, indent, tag, r, extra):
        """Write a personref/childref element with citationref*/noteref* children."""
        handle = self.ref(r.get("ref"))
        if handle is None:
            return
        citations = [h for h in r.get("citation_list") or [] if self.ref(h)]
        notes = [h for h in r.get("note_list") or [] if self.ref(h)]
        extra = f"{self.priv(r)}{extra}"
        if not (citations or notes):
            self.write_ref(indent, tag, handle, extra)
            return
        self.write_ref(indent, tag, handle, extra, close=False)
        self.write_refs(indent + 1, "citationref", citations)
        self.write_refs(indent + 1, "noteref", notes)
        self.w(indent, f"</{tag}>")

    def write_addresses(self, indent, addresses):
        for a in addresses or []:
            if not isinstance(a, dict) or not self.keep(a):
                continue
            self.w(indent, f"<address{self.priv(a)}>")
            self.write_date(indent + 1, a.get("date"))
            for key in ("street", "locality", "city", "county", "state", "country", "postal", "phone"):
                self.write_line(indent + 1, key, a.get(key, ""))
            self.write_refs(indent + 1, "noteref", [h for h in a.get("note_list") or [] if self.ref(h)])
            self.write_refs(indent + 1, "citationref", [h for h in a.get("citation_list") or [] if self.ref(h)])
            self.w(indent, "</address>")

    def write_urls(self, indent, urls):
        for u in urls or []:
            if not isinstance(u, dict) or not self.keep(u):
                continue
            type_str = f' type="{attr(u.get("type"))}"' if u.get("type") else ""
            desc = f' description="{attr(u.get("desc"))}"' if u.get("desc") else ""
            self.w(indent, f'<url{self.priv(u)} href="{attr(u.get("path", ""))}"{type_str}{desc}/>')

    def write_lds_ords(self, indent, ords):
        for o in ords or []:
            if not isinstance(o, dict) or not self.keep(o):
                continue
            self.w(indent, f'<lds_ord type="{attr(o.get("type", ""))}"{self.priv(o)}>')
            self.write_date(indent + 1, o.get("date"))
            if o.get("temple"):
                self.w(indent + 1, f'<temple val="{attr(o["temple"])}"/>')
            self.write_ref(indent + 1, "place", o.get("place"))
            if o.get("status"):
                self.w(indent + 1, f'<status val="{attr(o["status"])}"/>')
            self.write_ref(indent + 1, "sealed_to", o.get("sealed_to"))
            self.write_refs(indent + 1, "noteref", [h for h in o.get("note_list") or [] if self.ref(h)])
            self.write_refs(indent + 1, "citationref", [h for h in o.get("citation_list") or [] if self.ref(h)])
            self.w(indent, "</lds_ord>")

    def write_name(self, indent, name, alternative=False):
        if not isinstance(name, dict) or not name:
            return
        if not self.keep(name):
            return
        head = "<name"
        if alternative:
            head += ' alt="1"'
        if name.get("type"):
            head += f' type="{attr(name["type"])}"'
        head += self.priv(name)
        if _int(name.get("sort_as")):
            head += f' sort="{_int(name.get("sort_as"))}"'
        if _int(name.get("display_as")):
            head += f' display="{_int(name.get("display_as"))}"'
        self.w(indent, head + ">")
        first = "".join(str(name.get("first_name", "")).splitlines())
        self.write_line(indent + 1, "first", first)
        self.write_line(indent + 1, "call", name.get("call", ""))
        for sn in name.get("surname_list") or []:
            if not isinstance(sn, dict):
                continue
            line = "<surname"
            if sn.get("prefix"):
                line += f' prefix="{attr(sn["prefix"])}"'
            if not sn.get("primary", True):
                line += ' prim="0"'
            if sn.get("connector"):
                line += f' connector="{attr(sn["connector"])}"'
            if sn.get("origintype"):
                line += f' derivation="{attr(sn["origintype"])}"'
            self.w(indent + 1, f'{line}>{text(sn.get("surname", ""))}</surname>')
        self.write_line(indent + 1, "suffix", name.get("suffix", ""))
        self.write_line(indent + 1, "title", name.get("title", ""))
        self.write_line(indent + 1, "nick", name.get("nick", ""))
        self.write_line(indent + 1, "familynick", name.get("famnick", ""))
        self.write_line(indent + 1, "group", name.get("group_as", ""))
        self.write_date(indent + 1, name.get("date"))
        self.write_refs(indent + 1, "noteref", [h for h in name.get("note_list") or [] if self.ref(h)])
        self.write_refs(indent + 1, "citationref", [h for h in name.get("citation_list") or [] if self.ref(h)])
        self.w(indent, "</name>")

    # --- primary objects --------------------------------------------------

    def write_tag(self, tag):
        self.w(
            2,
            f'<tag handle="{attr(xml_handle(tag.handle))}" change="{_int(tag.change)}"'
            f' name="{attr(tag.name)}" color="{attr(tag.color)}"'
            f' priority="{_int(tag.priority)}"/>',
        )

    def write_event(self, event):
        self.primary_tag(2, "event", event)
        self.w(3, f"<type>{text(event.type)}</type>")
        self.write_date(3, event.date)
        self.write_ref(3, "place", event.place_id)
        self.write_line(3, "description", event.description)
        self.write_attributes(3, event.attribute_list)
        self.write_refs(3, "noteref", event.note_list)
        self.write_refs(3, "citationref", event.citation_list)
        self.write_media_refs(3, event.media_list)
        self.write_refs(3, "tagref", event.tag_list)
        self.w(2, "</event>")

    def write_person(self, person):
        self.primary_tag(2, "person", person)
        self.w(3, f"<gender>{GENDER_XML.get(person.gender, 'U')}</gender>")
        self.write_name(3, person.primary_name)
        for name in person.alternate_names or []:
            self.write_name(3, name, alternative=True)
        self.write_event_refs(3, person.event_ref_list)
        self.write_lds_ords(3, person.lds_ord_list)
        self.write_media_refs(3, person.media_list)
        self.write_addresses(3, person.address_list)
        self.write_attributes(3, person.attribute_list)
        self.write_urls(3, person.urls)
        self.write_refs(3, "childof", person.parent_family_list)
        self.write_refs(3, "parentin", person.family_list)
        for r in person.person_ref_list or []:
            if isinstance(r, dict) and self.keep(r):
                self.write_cit_note_ref(3, "personref", r, f' rel="{attr(r.get("rel", ""))}"')
        self.write_refs(3, "noteref", person.note_list)
        self.write_refs(3, "citationref", person.citation_list)
        self.write_refs(3, "tagref", person.tag_list)
        self.w(2, "</person>")

    def write_family(self, family):
        self.primary_tag(2, "family", family)
        if family.type:
            self.w(3, f'<rel type="{attr(family.type)}"/>')
        self.write_ref(3, "father", family.father_handle_id)
        self.write_ref(3, "mother", family.mother_handle_id)
        self.write_event_refs(3, family.event_ref_list)
        self.write_lds_ords(3, family.lds_ord_list)
        self.write_media_refs(3, family.media_list)
        for r in family.child_ref_list or []:
            if not isinstance(r, dict) or not self.keep(r):
                continue
            extra = ""
            if r.get("mrel") and r["mrel"] != "Birth":
                extra += f' mrel="{attr(r["mrel"])}"'
            if r.get("frel") and r["frel"] != "Birth":
                extra += f' frel="{attr(r["frel"])}"'
            self.write_cit_note_ref(3, "childref", r, extra)
        self.write_attributes(3, family.attribute_list)
        self.write_refs(3, "noteref", family.note_list)
        self.write_refs(3, "citationref", family.citation_list)
        self.write_refs(3, "tagref", family.tag_list)
        self.w(2, "</family>")

    def write_citation(self, citation):
        self.primary_tag(2, "citation", citation)
        self.write_date(3, citation.date)
        self.write_line(3, "page", citation.page)
        self.w(3, f"<confidence>{_int(citation.confidence, 2)}</confidence>")
        self.write_refs(3, "noteref", citation.note_list)
        self.write_media_refs(3, citation.media_list)
        self.write_attributes(3, citation.attribute_list, tag="srcattribute")
        self.write_ref(3, "sourceref", citation.source_handle_id)
        self.write_refs(3, "tagref", citation.tag_list)
        self.w(2, "</citation>")

    def write_source(self, source):
        self.primary_tag(2, "source", source)
        self.w(3, f"<stitle>{text(source.title or '')}</stitle>")
        self.write_line(3, "sauthor", source.author)
        self.write_line(3, "spubinfo", source.pubinfo)
        self.write_line(3, "sabbrev", source.abbrev)
        self.write_refs(3, "noteref", source.note_list)
        self.write_media_refs(3, source.media_list)
        self.write_attributes(3, source.attribute_list, tag="srcattribute")
        for r in source.reporef_list or []:
            if not isinstance(r, dict) or not self.keep(r):
                continue
            handle = self.ref(r.get("ref"))
            if handle is None:
                continue
            extra = self.priv(r)
            if r.get("call_number"):
                extra += f' callno="{attr(r["call_number"])}"'
            if r.get("media_type"):
                extra += f' medium="{attr(r["media_type"])}"'
            notes = [h for h in r.get("note_list") or [] if self.ref(h)]
            if not notes:
                self.write_ref(3, "reporef", handle, extra)
            else:
                self.write_ref(3, "reporef", handle, extra, close=False)
                self.write_refs(4, "noteref", notes)
                self.w(3, "</reporef>")
        self.write_refs(3, "tagref", source.tag_list)
        self.w(2, "</source>")

    def write_place_name(self, indent, name):
        if not isinstance(name, dict):
            return
        head = f'<pname value="{attr(name.get("value", ""))}"'
        if name.get("lang"):
            head += f' lang="{attr(name["lang"])}"'
        date = name.get("date")
        if isinstance(date, dict) and date:
            self.w(indent, head + ">")
            self.write_date(indent + 1, date)
            self.w(indent, "</pname>")
        else:
            self.w(indent, head + "/>")

    def write_place(self, place):
        self.primary_tag(2, "placeobj", place, f' type="{attr(place.place_type or "Unknown")}"')
        self.write_line(3, "ptitle", place.title)
        self.write_line(3, "code", place.code)
        self.write_place_name(3, place.name if place.name else {"value": ""})
        for name in place.alt_names or []:
            self.write_place_name(3, name)
        if place.long or place.lat:
            self.w(3, f'<coord long="{attr(place.long)}" lat="{attr(place.lat)}"/>')
        for r in place.placeref_list or []:
            if not isinstance(r, dict):
                continue
            handle = self.ref(r.get("ref"))
            if handle is None:
                continue
            date = r.get("date")
            if isinstance(date, dict) and date:
                self.write_ref(3, "placeref", handle, close=False)
                self.write_date(4, date)
                self.w(3, "</placeref>")
            else:
                self.write_ref(3, "placeref", handle)
        for loc in place.alt_loc or []:
            if not isinstance(loc, dict):
                continue
            parts = "".join(
                f' {key}="{attr(loc[key])}"'
                for key in ("street", "locality", "city", "parish", "county",
                            "state", "country", "postal", "phone")
                if loc.get(key)
            )
            if parts:
                self.w(3, f"<location{parts}/>")
        self.write_media_refs(3, place.media_list)
        self.write_urls(3, place.urls)
        self.write_refs(3, "noteref", place.note_list)
        self.write_refs(3, "citationref", place.citation_list)
        self.write_refs(3, "tagref", place.tag_list)
        self.w(2, "</placeobj>")

    def write_media(self, media):
        self.primary_tag(2, "object", media)
        line = f'<file src="{attr(media.path)}" mime="{attr(media.mime)}"'
        if media.checksum:
            line += f' checksum="{attr(media.checksum)}"'
        line += f' description="{attr(media.desc or "")}"/>'
        self.w(3, line)
        self.write_attributes(3, media.attribute_list)
        self.write_refs(3, "noteref", media.note_list)
        self.write_date(3, media.date)
        self.write_refs(3, "citationref", media.citation_list)
        self.write_refs(3, "tagref", media.tag_list)
        self.w(2, "</object>")

    def write_repository(self, repo):
        self.primary_tag(2, "repository", repo)
        self.w(3, f"<rname>{text(repo.name or '')}</rname>")
        self.write_line(3, "type", repo.type)
        self.write_addresses(3, repo.address_list)
        self.write_urls(3, repo.urls)
        self.write_refs(3, "noteref", repo.note_list)
        self.write_refs(3, "tagref", repo.tag_list)
        self.w(2, "</repository>")

    def write_note(self, note):
        extra = f' type="{attr(note.type or "General")}"'
        if _int(note.format):
            extra += f' format="{_int(note.format)}"'
        self.primary_tag(2, "note", note, extra)
        styled = note.text if isinstance(note.text, dict) else {"string": str(note.text or "")}
        self.w(3, f"<text>{text(styled.get('string', ''))}</text>")
        for style in styled.get("tags") or []:
            if not isinstance(style, dict):
                continue
            head = f'<style name="{attr(style.get("name", ""))}"'
            if style.get("value") not in (None, ""):
                head += f' value="{attr(style["value"])}"'
            self.w(3, head + ">")
            for r in style.get("ranges") or []:
                if isinstance(r, (list, tuple)) and len(r) == 2:
                    self.w(4, f'<range start="{_int(r[0])}" end="{_int(r[1])}"/>')
            self.w(3, "</style>")
        self.write_refs(3, "tagref", note.tag_list)
        self.w(2, "</note>")

    # --- document ------------------------------------------------------------

    def _queryset(self, model):
        qs = model.objects.all().order_by("handle")
        if self.exclude_private and model is not Tag:
            qs = qs.filter(private=False)
        return qs

    def _collect_excluded(self):
        if not self.exclude_private:
            return
        for model in (Person, Family, Event, Place, Source, Citation, Repository, MediaObject, Note):
            self.excluded.update(
                model.objects.filter(private=True).values_list("handle", flat=True)
            )

    def write_section(self, tag, model, writer):
        qs = self._queryset(model)
        if not qs.exists():
            return
        self.w(1, f"<{tag}>")
        for obj in qs.iterator(chunk_size=500):
            writer(obj)
        self.w(1, f"</{tag}>")

    def write(self):
        self._collect_excluded()
        today = time.strftime("%Y-%m-%d")
        self.out.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        self.out.write(
            f'<!DOCTYPE database PUBLIC "-//Gramps//DTD Gramps XML {XML_VERSION}//EN"\n'
            f'"http://gramps-project.org/xml/{XML_VERSION}/grampsxml.dtd">\n'
        )
        self.out.write(f'<database xmlns="{NAMESPACE}">\n')
        self.w(1, "<header>")
        self.w(2, f'<created date="{today}" version="{GRAMPS_VERSION}"/>')
        self.w(2, "<researcher>")
        self.w(2, "</researcher>")
        self.w(1, "</header>")
        self.write_section("tags", Tag, self.write_tag)
        self.write_section("events", Event, self.write_event)
        self.write_section("people", Person, self.write_person)
        self.write_section("families", Family, self.write_family)
        self.write_section("citations", Citation, self.write_citation)
        self.write_section("sources", Source, self.write_source)
        self.write_section("places", Place, self.write_place)
        self.write_section("objects", MediaObject, self.write_media)
        self.write_section("repositories", Repository, self.write_repository)
        self.write_section("notes", Note, self.write_note)
        self.out.write("</database>\n")


def export_gramps_xml(path, compress=True, exclude_private=False):
    """Write the database as Gramps XML to ``path`` (gzipped if ``compress``)."""
    if compress:
        with gzip.open(path, "wt", encoding="utf-8") as out:
            GrampsXmlWriter(out, exclude_private=exclude_private).write()
    else:
        with open(path, "w", encoding="utf-8") as out:
            GrampsXmlWriter(out, exclude_private=exclude_private).write()
    return path
