#!/usr/bin/env python3
"""
Development-time generator for the static Gramps type and translation data
used by ``apps/special/types.py`` and ``apps/special/translations.py``.

The backend does not depend on the ``gramps`` package at runtime. Instead this
script runs the Gramps core source tree (not installed, just checked out) in a
subprocess per language and dumps the type tables and gettext catalogs into
JSON files that are committed to the repository:

* ``apps/special/data/types.json``
    For every default type category of gramps-web-api (``event_types``,
    ``place_types``, ...) the XML (English) names in Gramps order, the
    ``map`` (integer code -> XML name) and the translated names per language.
* ``apps/special/data/translations/<lang>.json``
    ``msgid -> msgstr`` dictionaries derived from ``po/<lang>.po``. Fuzzy,
    obsolete and untranslated entries are skipped. Entries with a ``msgctxt``
    are stored under ``"<ctx>|<msgid>"`` and, if that msgid has no
    context-free translation, also under the plain ``msgid``. For plural
    entries the singular translation is stored.

Usage (from the repository root, system python 3.10+, no Django needed)::

    python3 -I scripts/generate_gramps_data.py --gramps-core /path/to/gramps-core

or with the ``GRAMPS_CORE`` environment variable pointing to the checkout.
Optional ``--languages fi,sv,de`` (default) selects the translation catalogs.

The gettext ``.po`` files are compiled to ``.mo`` by this script itself (no
``msgfmt`` needed) into a temporary resource directory, so Gramps resolves
translations exactly like it would in production. Re-run the script whenever
the Gramps core checkout is updated and commit the regenerated data files.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "apps" / "special" / "data"
TRANSLATIONS_DIR = DATA_DIR / "translations"
DEFAULT_LANGUAGES = ["fi", "sv", "de"]

# Code run inside the Gramps source tree, once per language, with the
# LANGUAGE environment variable selecting the catalog. It prints JSON.
_DUMP_CODE = r"""
import json, sys
sys.path.insert(0, ".")
from gramps.gen.lib.attrtype import AttributeType
from gramps.gen.lib.childreftype import ChildRefType
from gramps.gen.lib.eventroletype import EventRoleType
from gramps.gen.lib.eventtype import EventType
from gramps.gen.lib.familyreltype import FamilyRelType
from gramps.gen.lib.nameorigintype import NameOriginType
from gramps.gen.lib.nametype import NameType
from gramps.gen.lib.notetype import NoteType
from gramps.gen.lib.placetype import PlaceType
from gramps.gen.lib.repotype import RepositoryType
from gramps.gen.lib.srcattrtype import SrcAttributeType
from gramps.gen.lib.srcmediatype import SourceMediaType
from gramps.gen.lib.urltype import UrlType
from gramps.gen.const import GRAMPS_LOCALE

classes = {
    "attribute_types": AttributeType,
    "child_reference_types": ChildRefType,
    "event_role_types": EventRoleType,
    "event_types": EventType,
    "family_relation_types": FamilyRelType,
    "name_origin_types": NameOriginType,
    "name_types": NameType,
    "note_types": NoteType,
    "place_types": PlaceType,
    "repository_types": RepositoryType,
    "source_attribute_types": SrcAttributeType,
    "source_media_types": SourceMediaType,
    "url_types": UrlType,
}
out = {"lang": GRAMPS_LOCALE.translation.language(), "types": {}}
for key, cls in classes.items():
    inst = cls()
    i2e = cls._I2EMAP
    i2s = cls._I2SMAP
    xml = inst.get_standard_xml()
    e2i = {e: i for i, e in i2e.items()}
    names = [i2s[e2i[e]] for e in xml]
    assert names == inst.get_standard_names(), key
    out["types"][key] = {
        "xml": xml,
        "names": names,
        "map": {str(i): e for i, e in i2e.items()},
    }
_ = GRAMPS_LOCALE.translation.gettext
out["gender"] = {"Male": _("Male"), "Female": _("Female"), "Unknown": _("Unknown")}
print(json.dumps(out, ensure_ascii=False))
"""

# gramps-web-api: {Person.MALE: "Male", Person.FEMALE: "Female", Person.UNKNOWN: "Unknown"}
_GENDER_MAP = {"1": "Male", "0": "Female", "2": "Unknown"}


# --------------------------------------------------------------------------
# .po parsing
# --------------------------------------------------------------------------


def _unquote(line: str) -> str:
    """Decode one quoted .po string line (``"..."``) to a Python string."""
    line = line.strip()
    if not (line.startswith('"') and line.endswith('"')):
        raise ValueError(f"bad .po string: {line!r}")
    return json.loads(line) if "\\" in line else line[1:-1]


def parse_po(path: Path) -> list[dict]:
    """
    Parse a .po file into a list of entries.

    Each entry is ``{"ctx": str|None, "id": str, "plural": str|None,
    "str": [str, ...], "fuzzy": bool}``. Obsolete (``#~``) entries are skipped.
    """
    entries: list[dict] = []
    cur: dict | None = None
    field: str | None = None
    fuzzy = False

    def flush():
        nonlocal cur, field
        if cur is not None:
            entries.append(cur)
        cur, field = None, None

    with path.open(encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                flush()
                fuzzy = False
                continue
            if line.startswith("#"):
                if line.startswith("#~"):
                    # obsolete entry: drop any partial entry and skip
                    cur, field = None, None
                    continue
                if line.startswith("#,") and "fuzzy" in line:
                    fuzzy = True
                continue
            if line.startswith("msgctxt "):
                flush()
                cur = {"ctx": _unquote(line[8:]), "id": "", "plural": None,
                       "str": [], "fuzzy": fuzzy}
                field = "ctx"
            elif line.startswith("msgid_plural "):
                cur["plural"] = _unquote(line[13:])
                field = "plural"
            elif line.startswith("msgid "):
                if cur is None or field != "ctx":
                    flush()
                    cur = {"ctx": None, "id": "", "plural": None,
                           "str": [], "fuzzy": fuzzy}
                cur["id"] = _unquote(line[6:])
                field = "id"
            elif line.startswith("msgstr["):
                idx = int(line[7:line.index("]")])
                while len(cur["str"]) <= idx:
                    cur["str"].append("")
                cur["str"][idx] = _unquote(line[line.index("]") + 1:])
                field = ("str", idx)
            elif line.startswith("msgstr "):
                cur["str"] = [_unquote(line[7:])]
                field = ("str", 0)
            elif line.startswith('"'):
                text = _unquote(line)
                if field == "ctx":
                    cur["ctx"] += text
                elif field == "id":
                    cur["id"] += text
                elif field == "plural":
                    cur["plural"] += text
                elif isinstance(field, tuple):
                    cur["str"][field[1]] += text
    flush()
    return entries


# --------------------------------------------------------------------------
# .mo writing (minimal msgfmt replacement)
# --------------------------------------------------------------------------


def write_mo(entries: list[dict], path: Path) -> None:
    """Compile parsed .po entries to a GNU gettext .mo file."""
    messages: dict[bytes, bytes] = {}
    for e in entries:
        if e["fuzzy"] or not any(e["str"]):
            continue
        if e["id"] == "" and e["ctx"] is None:
            messages[b""] = e["str"][0].encode("utf-8")
            continue
        key = e["id"]
        if e["ctx"] is not None:
            key = f"{e['ctx']}\x04{key}"
        if e["plural"] is not None:
            key = f"{key}\x00{e['plural']}"
            value = "\x00".join(e["str"])
        else:
            value = e["str"][0]
        messages[key.encode("utf-8")] = value.encode("utf-8")
    if b"" not in messages:
        messages[b""] = b"Content-Type: text/plain; charset=UTF-8\n"

    keys = sorted(messages)
    ids = b""
    strs = b""
    offsets = []
    for k in keys:
        v = messages[k]
        offsets.append((len(ids), len(k), len(strs), len(v)))
        ids += k + b"\0"
        strs += v + b"\0"
    n = len(keys)
    keystart = 7 * 4 + 16 * n
    valuestart = keystart + len(ids)
    koffsets = []
    voffsets = []
    for o1, l1, o2, l2 in offsets:
        koffsets += [l1, o1 + keystart]
        voffsets += [l2, o2 + valuestart]
    header = struct.pack(
        "Iiiiiii", 0x950412DE, 0, n, 7 * 4, 7 * 4 + n * 8, 0, 0
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        fh.write(header)
        fh.write(struct.pack(f"{len(koffsets)}i", *koffsets))
        fh.write(struct.pack(f"{len(voffsets)}i", *voffsets))
        fh.write(ids)
        fh.write(strs)


# --------------------------------------------------------------------------
# JSON dictionaries
# --------------------------------------------------------------------------


def build_dictionary(entries: list[dict]) -> dict[str, str]:
    """Build the msgid -> msgstr dictionary stored in translations/<lang>.json."""
    result: dict[str, str] = {}
    # context-free entries first so they win over context-derived fallbacks
    for e in entries:
        if e["fuzzy"] or e["id"] == "" or not e["str"] or not e["str"][0]:
            continue
        if e["ctx"] is None:
            result[e["id"]] = e["str"][0]
    for e in entries:
        if e["fuzzy"] or e["id"] == "" or not e["str"] or not e["str"][0]:
            continue
        if e["ctx"] is not None:
            result[f"{e['ctx']}|{e['id']}"] = e["str"][0]
            result.setdefault(e["id"], e["str"][0])
    return result


def dump_types(gramps_core: Path, resources: Path, lang: str) -> dict:
    """Run the Gramps type dump in a subprocess for one language."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "GRAMPS_RESOURCES": str(resources),
        "LANGUAGE": lang,
        "LANG": f"{lang}.UTF-8" if lang != "en" else "en_US.UTF-8",
        "LC_ALL": f"{lang}.UTF-8" if lang != "en" else "en_US.UTF-8",
        "PYTHONIOENCODING": "utf-8",
    }
    proc = subprocess.run(
        [sys.executable, "-I", "-c", _DUMP_CODE],
        cwd=gramps_core,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"Gramps type dump failed for language {lang}")
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    got = data["lang"]
    if got.split("_")[0] != lang.split("_")[0]:
        sys.stderr.write(proc.stderr)
        raise SystemExit(
            f"Gramps loaded catalog {got!r} instead of {lang!r}; translations "
            "could not be resolved"
        )
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--gramps-core",
        default=os.environ.get("GRAMPS_CORE"),
        help="path to the gramps-core source checkout (or $GRAMPS_CORE)",
    )
    parser.add_argument(
        "--languages",
        default=",".join(DEFAULT_LANGUAGES),
        help="comma separated language codes with po/<lang>.po catalogs",
    )
    args = parser.parse_args()
    if not args.gramps_core:
        parser.error("--gramps-core (or GRAMPS_CORE) is required")
    gramps_core = Path(args.gramps_core).resolve()
    authors = gramps_core / "data" / "authors.xml"
    if not (gramps_core / "gramps" / "gen" / "lib" / "grampstype.py").exists():
        parser.error(f"{gramps_core} does not look like a Gramps source tree")
    languages = [l.strip() for l in args.languages.split(",") if l.strip()]

    TRANSLATIONS_DIR.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="gramps-res-") as tmp:
        resources = Path(tmp)
        (resources / "gramps").mkdir()
        shutil.copy(authors, resources / "gramps" / "authors.xml")

        for lang in languages:
            po = gramps_core / "po" / f"{lang}.po"
            if not po.exists():
                raise SystemExit(f"missing catalog {po}")
            entries = parse_po(po)
            write_mo(entries, resources / "locale" / lang / "LC_MESSAGES" / "gramps.mo")
            dictionary = build_dictionary(entries)
            target = TRANSLATIONS_DIR / f"{lang}.json"
            with target.open("w", encoding="utf-8") as fh:
                json.dump(dictionary, fh, ensure_ascii=False, separators=(",", ":"),
                          sort_keys=True)
                fh.write("\n")
            print(f"wrote {target.relative_to(REPO_ROOT)} ({len(dictionary)} entries)")

        dumps = {lang: dump_types(gramps_core, resources, lang)
                 for lang in ["en"] + languages}

    base = dumps["en"]["types"]
    types: dict = {}
    for key, info in base.items():
        names = {}
        for lang, dump in dumps.items():
            assert dump["types"][key]["xml"] == info["xml"], (key, lang)
            names[lang] = dump["types"][key]["names"]
        types[key] = {"xml": info["xml"], "map": info["map"], "names": names}
    gender_xml = list(_GENDER_MAP.values())
    types["gender_types"] = {
        "xml": gender_xml,
        "map": dict(_GENDER_MAP),
        "names": {lang: [dump["gender"][g] for g in gender_xml]
                  for lang, dump in dumps.items()},
    }

    types_json = {
        "languages": ["en"] + languages,
        "types": dict(sorted(types.items())),
    }
    target = DATA_DIR / "types.json"
    with target.open("w", encoding="utf-8") as fh:
        json.dump(types_json, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write("\n")
    print(f"wrote {target.relative_to(REPO_ROOT)} ({len(types)} categories)")


if __name__ == "__main__":
    main()
