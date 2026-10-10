"""
Minimal GEDCOM 5.5.1 exporter.

Writes individuals, families, events (with dates and places), sources,
citations, notes and repositories. Attributes, media and LDS ordinances
are intentionally left out; use the Gramps XML export for a full backup.
"""

from apps.core.models import (
    Citation,
    Event,
    Family,
    Note,
    Person,
    Place,
    Repository,
    Source,
)

MOD_BEFORE, MOD_AFTER, MOD_ABOUT, MOD_RANGE, MOD_SPAN = 1, 2, 3, 4, 5
MOD_TEXTONLY, MOD_FROM, MOD_TO = 6, 7, 8
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

# Gramps event type -> GEDCOM tag (individual and family events)
EVENT_TAGS = {
    "Birth": "BIRT", "Death": "DEAT", "Burial": "BURI", "Cremation": "CREM",
    "Baptism": "BAPM", "Christening": "CHR", "Adult Christening": "CHRA",
    "Adopted": "ADOP", "Bar Mitzvah": "BARM", "Bas Mitzvah": "BASM",
    "Blessing": "BLES", "Confirmation": "CONF", "First Communion": "FCOM",
    "Emigration": "EMIG", "Immigration": "IMMI", "Naturalization": "NATU",
    "Census": "CENS", "Graduation": "GRAD", "Retirement": "RETI",
    "Probate": "PROB", "Will": "WILL", "Occupation": "OCCU",
    "Residence": "RESI", "Religion": "RELI", "Education": "EDUC",
    "Nobility Title": "TITL", "Cause Of Death": "CAUS", "Ordination": "ORDN",
    "Marriage": "MARR", "Marriage Banns": "MARB", "Marriage Contract": "MARC",
    "Marriage License": "MARL", "Marriage Settlement": "MARS",
    "Divorce": "DIV", "Divorce Filing": "DIVF", "Engagement": "ENGA",
    "Annulment": "ANUL",
}
FAMILY_EVENT_TAGS = {"MARR", "MARB", "MARC", "MARL", "MARS", "DIV", "DIVF", "ENGA", "ANUL", "CENS", "RESI"}
GENDER_GED = {Person.MALE: "M", Person.FEMALE: "F"}


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clean(value):
    """Collapse line breaks; GEDCOM values are single-line (CONT handles the rest)."""
    return str(value or "").replace("\r", "")


def gedcom_date(date):
    """Format a Gramps JSON date dict as a GEDCOM date string ('' if empty)."""
    if not isinstance(date, dict) or not date:
        return ""
    modifier = _int(date.get("modifier"))
    if modifier == MOD_TEXTONLY:
        return f"({_clean(date.get('text'))})" if date.get("text") else ""
    dateval = date.get("dateval") or []
    if len(dateval) < 3:
        return ""

    def fmt(day, month, year):
        parts = []
        if day:
            parts.append(str(day))
        if 1 <= month <= 12:
            parts.append(MONTHS[month - 1])
        if year:
            parts.append(str(year))
        return " ".join(parts)

    start = fmt(_int(dateval[0]), _int(dateval[1]), _int(dateval[2]))
    stop = fmt(_int(dateval[4]), _int(dateval[5]), _int(dateval[6])) if len(dateval) >= 7 else ""
    if modifier == MOD_RANGE and (start or stop):
        return f"BET {start} AND {stop}".strip()
    if modifier == MOD_SPAN and (start or stop):
        return f"FROM {start} TO {stop}".strip()
    if not start:
        return ""
    quality = _int(date.get("quality"))
    prefix = {MOD_BEFORE: "BEF", MOD_AFTER: "AFT", MOD_ABOUT: "ABT", MOD_FROM: "FROM", MOD_TO: "TO"}.get(modifier)
    if not prefix:
        prefix = {1: "EST", 2: "CAL"}.get(quality, "")
    return f"{prefix} {start}".strip()


class GedcomWriter:
    """Serialises the database as GEDCOM 5.5.1 text."""

    def __init__(self, out, exclude_private=False):
        self.out = out
        self.exclude_private = exclude_private
        self.ids = {}  # (class, handle) -> xref id
        self.people = {}
        self.families = {}
        self.events = {}
        self.places = {}
        self.sources = {}
        self.citations = {}
        self.notes = {}
        self.repos = {}

    # --- helpers ------------------------------------------------------------

    def line(self, level, tag, value=""):
        value = _clean(value)
        if not value:
            self.out.write(f"{level} {tag}\n")
            return
        chunks = value.split("\n")
        self.out.write(f"{level} {tag} {chunks[0]}\n")
        for chunk in chunks[1:]:
            self.out.write(f"{level + 1} CONT {chunk}\n")

    def xref(self, prefix, handle, table):
        obj = table.get(handle)
        if obj is None:
            return None
        key = (prefix, handle)
        if key not in self.ids:
            self.ids[key] = f"@{obj.gramps_id or prefix + str(len(self.ids) + 1)}@"
        return self.ids[key]

    def load(self):
        def table(model):
            qs = model.objects.all()
            if self.exclude_private:
                qs = qs.filter(private=False)
            return {o.handle: o for o in qs}

        self.people = table(Person)
        self.families = table(Family)
        self.events = table(Event)
        self.places = table(Place)
        self.sources = table(Source)
        self.citations = table(Citation)
        self.notes = table(Note)
        self.repos = table(Repository)

    def keep(self, obj):
        return not (self.exclude_private and isinstance(obj, dict) and obj.get("private"))

    # --- shared sub-structures ------------------------------------------

    def place_title(self, place):
        names = []
        seen = set()
        current = place
        while current is not None and current.handle not in seen:
            seen.add(current.handle)
            value = (current.name or {}).get("value") if isinstance(current.name, dict) else ""
            if value:
                names.append(value)
            refs = current.placeref_list or []
            parent = refs[0].get("ref") if refs and isinstance(refs[0], dict) else None
            current = self.places.get(parent) if parent else None
        return ", ".join(names) or place.title

    def write_citations(self, level, handles):
        for handle in handles or []:
            citation = self.citations.get(handle)
            if citation is None:
                continue
            source_id = self.xref("S", citation.source_handle_id, self.sources)
            if source_id is None:
                continue
            self.line(level, "SOUR", source_id)
            if citation.page:
                self.line(level + 1, "PAGE", citation.page)
            self.line(level + 1, "QUAY", str(_int(citation.confidence, 2)))
            date = gedcom_date(citation.date)
            if date:
                self.line(level + 1, "DATA")
                self.line(level + 2, "DATE", date)
            self.write_notes(level + 1, citation.note_list)

    def write_notes(self, level, handles):
        for handle in handles or []:
            note_id = self.xref("N", handle, self.notes)
            if note_id:
                self.line(level, "NOTE", note_id)

    def write_event(self, level, event, role=""):
        tag = EVENT_TAGS.get(event.type, "EVEN")
        # Attribute-like tags carry their value on the tag line itself.
        value_tags = ("OCCU", "TITL", "RELI", "EDUC")
        self.line(level, tag, event.description if tag in value_tags else "")
        if tag == "EVEN" and event.type:
            self.line(level + 1, "TYPE", event.type)
        date = gedcom_date(event.date)
        if date:
            self.line(level + 1, "DATE", date)
        place = self.places.get(event.place_id)
        if place is not None:
            self.line(level + 1, "PLAC", self.place_title(place))
            if place.lat or place.long:
                self.line(level + 2, "MAP")
                self.line(level + 3, "LATI", place.lat)
                self.line(level + 3, "LONG", place.long)
        if event.description and tag not in value_tags:
            self.line(level + 1, "NOTE", event.description)
        if role and role not in ("Primary", "Family"):
            self.line(level + 1, "NOTE", f"Role: {role}")
        self.write_citations(level + 1, event.citation_list)
        self.write_notes(level + 1, event.note_list)

    # --- records -------------------------------------------------------------

    def write_person(self, person):
        self.line(0, f"{self.xref('I', person.handle, self.people)} INDI")
        names = [person.primary_name] + list(person.alternate_names or [])
        for name in names:
            if not isinstance(name, dict) or not name or not self.keep(name):
                continue
            surnames = [
                sn for sn in name.get("surname_list") or [] if isinstance(sn, dict)
            ]
            surname = " ".join(
                " ".join(filter(None, [sn.get("prefix"), sn.get("surname")])) for sn in surnames
            ).strip()
            first = name.get("first_name", "")
            suffix = name.get("suffix", "")
            full = f"{first} /{surname}/"
            if suffix:
                full += f" {suffix}"
            self.line(1, "NAME", full.strip())
            if name.get("type") and name["type"] != "Birth Name":
                self.line(2, "TYPE", name["type"])
            if first:
                self.line(2, "GIVN", first)
            if surname:
                self.line(2, "SURN", surname)
            if name.get("nick"):
                self.line(2, "NICK", name["nick"])
            if suffix:
                self.line(2, "NSFX", suffix)
            if name.get("title"):
                self.line(2, "NPFX", name["title"])
            self.write_citations(2, name.get("citation_list"))
            self.write_notes(2, name.get("note_list"))
        self.line(1, "SEX", GENDER_GED.get(person.gender, "U"))
        for ref in person.event_ref_list or []:
            if not isinstance(ref, dict) or not self.keep(ref):
                continue
            event = self.events.get(ref.get("ref"))
            if event is None:
                continue
            role = ref.get("role", "Primary")
            if isinstance(role, dict):
                role = role.get("string", "")
            self.write_event(1, event, role)
        for family_handle in person.parent_family_list or []:
            fam_id = self.xref("F", family_handle, self.families)
            if fam_id:
                self.line(1, "FAMC", fam_id)
        for family_handle in person.family_list or []:
            fam_id = self.xref("F", family_handle, self.families)
            if fam_id:
                self.line(1, "FAMS", fam_id)
        for addr in person.address_list or []:
            if not isinstance(addr, dict) or not self.keep(addr):
                continue
            self.line(1, "RESI")
            date = gedcom_date(addr.get("date"))
            if date:
                self.line(2, "DATE", date)
            self.line(2, "ADDR", addr.get("street", ""))
            for key, tag in (("city", "CITY"), ("state", "STAE"), ("postal", "POST"), ("country", "CTRY")):
                if addr.get(key):
                    self.line(3, tag, addr[key])
        for url in person.urls or []:
            if isinstance(url, dict) and self.keep(url) and url.get("path"):
                self.line(1, "WWW", url["path"])
        self.write_citations(1, person.citation_list)
        self.write_notes(1, person.note_list)
        self.line(1, "CHAN")
        self.line(2, "DATE", _change_date(person.change))

    def write_family(self, family):
        self.line(0, f"{self.xref('F', family.handle, self.families)} FAM")
        husb = self.xref("I", family.father_handle_id, self.people)
        wife = self.xref("I", family.mother_handle_id, self.people)
        if husb:
            self.line(1, "HUSB", husb)
        if wife:
            self.line(1, "WIFE", wife)
        for ref in family.child_ref_list or []:
            if not isinstance(ref, dict) or not self.keep(ref):
                continue
            child = self.xref("I", ref.get("ref"), self.people)
            if child:
                self.line(1, "CHIL", child)
        for ref in family.event_ref_list or []:
            if not isinstance(ref, dict) or not self.keep(ref):
                continue
            event = self.events.get(ref.get("ref"))
            if event is None:
                continue
            self.write_event(1, event)
        if family.type and family.type not in ("Married", "Unknown"):
            self.line(1, "NOTE", f"Relationship type: {family.type}")
        self.write_citations(1, family.citation_list)
        self.write_notes(1, family.note_list)
        self.line(1, "CHAN")
        self.line(2, "DATE", _change_date(family.change))

    def write_source(self, source):
        self.line(0, f"{self.xref('S', source.handle, self.sources)} SOUR")
        self.line(1, "TITL", source.title or "")
        if source.author:
            self.line(1, "AUTH", source.author)
        if source.pubinfo:
            self.line(1, "PUBL", source.pubinfo)
        if source.abbrev:
            self.line(1, "ABBR", source.abbrev)
        for ref in source.reporef_list or []:
            if not isinstance(ref, dict) or not self.keep(ref):
                continue
            repo_id = self.xref("R", ref.get("ref"), self.repos)
            if repo_id:
                self.line(1, "REPO", repo_id)
                if ref.get("call_number"):
                    self.line(2, "CALN", ref["call_number"])
        self.write_notes(1, source.note_list)

    def write_repository(self, repo):
        self.line(0, f"{self.xref('R', repo.handle, self.repos)} REPO")
        self.line(1, "NAME", repo.name or "")
        for addr in repo.address_list or []:
            if not isinstance(addr, dict) or not self.keep(addr):
                continue
            self.line(1, "ADDR", addr.get("street", ""))
            for key, tag in (("city", "CITY"), ("state", "STAE"), ("postal", "POST"), ("country", "CTRY")):
                if addr.get(key):
                    self.line(2, tag, addr[key])
        for url in repo.urls or []:
            if isinstance(url, dict) and self.keep(url) and url.get("path"):
                self.line(1, "WWW", url["path"])
        self.write_notes(1, repo.note_list)

    def write_note(self, note):
        styled = note.text if isinstance(note.text, dict) else {"string": str(note.text or "")}
        self.line(0, f"{self.xref('N', note.handle, self.notes)} NOTE", styled.get("string", ""))

    def write(self):
        self.load()
        self.line(0, "HEAD")
        self.line(1, "SOUR", "gramps-django")
        self.line(2, "NAME", "Gramps Django")
        self.line(1, "DEST", "GEDCOM")
        self.line(1, "DATE", _change_date(None))
        self.line(1, "CHAR", "UTF-8")
        self.line(1, "GEDC")
        self.line(2, "VERS", "5.5.1")
        self.line(2, "FORM", "LINEAGE-LINKED")
        for person in sorted(self.people.values(), key=lambda p: p.gramps_id or ""):
            self.write_person(person)
        for family in sorted(self.families.values(), key=lambda f: f.gramps_id or ""):
            self.write_family(family)
        for source in sorted(self.sources.values(), key=lambda s: s.gramps_id or ""):
            self.write_source(source)
        for repo in sorted(self.repos.values(), key=lambda r: r.gramps_id or ""):
            self.write_repository(repo)
        for note in sorted(self.notes.values(), key=lambda n: n.gramps_id or ""):
            self.write_note(note)
        self.line(0, "TRLR")


def _change_date(timestamp):
    import time

    try:
        ts = time.localtime(float(timestamp)) if timestamp else time.localtime()
    except (TypeError, ValueError, OverflowError):
        ts = time.localtime()
    return f"{ts.tm_mday} {MONTHS[ts.tm_mon - 1]} {ts.tm_year}"


def export_gedcom(path, exclude_private=False):
    """Write the database as a GEDCOM 5.5.1 file to ``path``."""
    with open(path, "w", encoding="utf-8", newline="\n") as out:
        GedcomWriter(out, exclude_private=exclude_private).write()
    return path
