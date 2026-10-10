"""
DNA match helpers (port of gramps_webapi/api/dna.py and resources/dna.py).

DNA matches are stored as person associations (``person_ref_list`` entries
with ``rel == "DNA"``).  The matching chromosome segments are kept as raw
tabular text in notes attached to the association, or to a citation of the
association.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass

from apps.core.profile import get_person_profile

from .cache import TreeCache, ref_handle, type_string
from .relations import FEMALE, get_calculator

SIDE_UNKNOWN = "U"
SIDE_MATERNAL = "M"
SIDE_PATERNAL = "P"


@dataclass
class SegmentColumnOrder:
    """Order of the columns of a DNA match table."""

    chromosome: int
    start_position: int
    end_position: int
    centimorgans: int
    num_snps: int | None = None
    side: int | None = None
    comment: int | None = None


def get_delimiter(rows):
    """Guess the delimiter of a CSV-like table with at least 4 columns."""
    if rows[0].count("\t") >= 3:
        return "\t"
    if rows[0].count(",") >= 3:
        return ","
    if rows[0].count(";") >= 3:
        return ";"
    raise ValueError("Could not determine delimiter.")


def is_numeric(value):
    """Determine if a string is number-like."""
    if value == "":
        return False
    try:
        float(value)
        return True
    except ValueError:
        pass
    return bool(re.match(r"^\d[\d\.,]*$", value))


def cast_int(value):
    try:
        return int(value.replace(",", "").replace(".", ""))
    except (ValueError, TypeError):
        return 0


def cast_float(value):
    value = value.replace(" ", "")
    if value.count(".") > 1:
        value = value.replace(".", "")
    if value.count(",") > 1:
        value = value.replace(",", "")
    if value.count(",") == 1 and value.count(".") == 0:
        value = value.replace(",", ".")
    try:
        return float(value)
    except ValueError:
        return 0.0


def has_header(rows, delimiter):
    """Determine if the table has a header row."""
    if len(rows) < 2:
        return False
    header = rows[0]
    if len(header) < 4:
        return False
    return not any(is_numeric(column) for column in header.split(delimiter))


def find_column_position(column_names, condition, exclude_indices, allow_missing=False):
    for i, column in enumerate(column_names):
        if i in exclude_indices:
            continue
        if condition(column.lower().strip()):
            return i
    if allow_missing:
        return None
    raise ValueError("Column not found.")


def get_order(header, data_columns):
    """Get the order of the columns."""
    if header is None:
        # default ordering of the DNASegmentMap Gramplet
        if len(data_columns) >= 6:
            if all(
                (not value) or (value in {SIDE_MATERNAL, SIDE_PATERNAL, SIDE_UNKNOWN})
                for value in data_columns[5]
            ):
                return SegmentColumnOrder(0, 1, 2, 3, num_snps=4, side=5, comment=6)
        return SegmentColumnOrder(0, 1, 2, 3, num_snps=4, comment=5)
    exclude = []
    chromosome = find_column_position(header, lambda c: c.startswith("chr"), exclude)
    exclude.append(chromosome)
    start_position = find_column_position(header, lambda c: "start" in c, exclude)
    exclude.append(start_position)
    end_position = find_column_position(
        header,
        lambda c: "end" in c or "stop" in c or ("length" in c and "morgan" not in c),
        exclude,
    )
    exclude.append(end_position)
    centimorgans = find_column_position(
        header,
        lambda c: c.startswith("cm") or "centimorgan" in c or "length" in c,
        exclude,
    )
    exclude.append(centimorgans)
    num_snps = find_column_position(header, lambda c: "snp" in c, exclude, allow_missing=True)
    if num_snps is not None:
        exclude.append(num_snps)
    side = find_column_position(header, lambda c: c.startswith("side"), exclude, allow_missing=True)
    if side is not None:
        exclude.append(side)
    comment = find_column_position(header, lambda _c: True, exclude, allow_missing=True)
    return SegmentColumnOrder(
        chromosome=chromosome, start_position=start_position, end_position=end_position,
        centimorgans=centimorgans, num_snps=num_snps, side=side, comment=comment,
    )


def transpose_jagged_nested_list(data):
    return list(map(list, itertools.zip_longest(*data, fillvalue=None)))


def process_row(fields, order):
    """Process a row of a DNA match table."""
    if len(fields) < 4:
        return None
    try:
        chromo = fields[order.chromosome].strip()
        start = cast_int(fields[order.start_position].strip())
        stop = cast_int(fields[order.end_position].strip())
        cms = cast_float(fields[order.centimorgans].strip())
        if order.num_snps is not None and len(fields) >= order.num_snps + 1:
            snp = cast_int(fields[order.num_snps].strip())
        else:
            snp = 0
        if order.side is not None and len(fields) >= order.side + 1:
            side = fields[order.side].strip().upper()
            if side not in {SIDE_MATERNAL, SIDE_PATERNAL}:
                side = SIDE_UNKNOWN
        else:
            side = SIDE_UNKNOWN
        if order.comment is not None and len(fields) >= order.comment + 1:
            comment = fields[order.comment].strip()
        else:
            comment = ""
    except (ValueError, TypeError, IndexError):
        return None
    return {
        "chromosome": chromo,
        "start": start,
        "stop": stop,
        "side": side,
        "cM": cms,
        "SNPs": snp,
        "comment": comment,
    }


def parse_raw_dna_match_string(raw_string):
    """Parse a raw DNA match string into a list of segment dicts."""
    rows = (raw_string or "").strip().replace("\r\n", "\n").split("\n")
    try:
        delimiter = get_delimiter(rows)
    except (ValueError, IndexError):
        return []
    if has_header(rows, delimiter):
        header = rows[0].split(delimiter)
        rows = rows[1:]
    else:
        header = None
    data = [row.split(delimiter) for row in rows]
    data_columns = transpose_jagged_nested_list(data)
    try:
        order = get_order(header, data_columns=data_columns)
    except ValueError:
        return []
    segments = []
    for row in rows:
        if row.strip() == "":
            continue
        segment = process_row(fields=row.split(delimiter), order=order)
        if segment:
            segments.append(segment)
    return segments


def parse_raw_match_string_with_default_side(raw_string, side=None):
    """Parse a raw match string, filling in unknown sides with ``side``."""
    segments = parse_raw_dna_match_string(raw_string)
    if side is None:
        return segments
    return [
        dict(segment, side=side) if segment["side"] == SIDE_UNKNOWN else segment
        for segment in segments
    ]


def note_text(note):
    """Plain text of a Note (StyledText JSON or plain string)."""
    if note is None:
        return ""
    text = note.text
    if isinstance(text, dict):
        return text.get("string", "") or ""
    return str(text or "")


def get_segments_from_note(db, handle, side=None):
    note = db.get_note(handle)
    if note is None:
        return []
    return parse_raw_match_string_with_default_side(note_text(note), side=side)


def _match_side(db, calc, person, associate):
    """Determine which side (maternal/paternal) a match is on."""
    data, _msg = calc.get_relationship_distance_new(
        person, associate, all_families=False, all_dist=True, only_birth=True
    )
    try:
        if data[0][0] <= 0:
            return SIDE_UNKNOWN
        if data[0][0] == 1:
            ancestor = db.get_person(data[0][1])
            if ancestor is not None and ancestor.gender == FEMALE:
                return SIDE_MATERNAL
            return SIDE_PATERNAL
        if len(data) > 1 and data[0][0] == data[1][0] and data[0][2][0] != data[1][2][0]:
            return SIDE_UNKNOWN
        return {"m": SIDE_MATERNAL, "f": SIDE_PATERNAL}.get(data[0][2][0], SIDE_UNKNOWN)
    except (IndexError, TypeError):
        return SIDE_UNKNOWN


def get_match_data(db, person, association_index, lang="en", include_raw_data=False):
    """Get the DNA match data for one association of a person."""
    association = (person.person_ref_list or [])[association_index]
    associate = db.get_person(ref_handle(association))
    if associate is None:
        return None
    calc = get_calculator(db, lang)
    side = _match_side(db, calc, person, associate)

    note_handles = list(association.get("note_list") or []) if isinstance(association, dict) else []
    for citation_handle in (association.get("citation_list") or []) if isinstance(association, dict) else []:
        citation = db.get_citation(citation_handle)
        if citation is not None:
            note_handles += list(citation.note_list or [])

    segments = []
    note_handles_with_segments = []
    for note_handle in note_handles:
        note_segments = get_segments_from_note(db, note_handle, side=side)
        if note_segments:
            segments += note_segments
            note_handles_with_segments.append(note_handle)

    rel_strings, common_ancestors = calc.get_all_relationships(person, associate)
    if not rel_strings:
        rel_string = ""
        ancestor_handles = []
    else:
        rel_string = rel_strings[0]
        ancestor_handles = list(dict.fromkeys(common_ancestors[0]))
    ancestor_profiles = []
    for handle in ancestor_handles:
        ancestor = db.get_person(handle)
        if ancestor is not None:
            ancestor_profiles.append(get_person_profile(ancestor, {"self"}))
    result = {
        "handle": ref_handle(association),
        "segments": segments,
        "relation": rel_string,
        "ancestor_handles": ancestor_handles,
        "ancestor_profiles": ancestor_profiles,
        "person_ref_idx": association_index,
        "note_handles": note_handles_with_segments,
    }
    if include_raw_data:
        result["raw_data"] = [
            note_text(db.get_note(handle)) for handle in note_handles_with_segments
        ]
    return result


def get_dna_matches(person, lang="en", include_raw_data=False, db=None):
    """All DNA matches (associations of type DNA) of a person."""
    db = db or TreeCache()
    matches = []
    for index, association in enumerate(person.person_ref_list or []):
        if type_string(association.get("rel") if isinstance(association, dict) else None) != "DNA":
            continue
        match = get_match_data(db, person, index, lang=lang, include_raw_data=include_raw_data)
        if match is not None:
            matches.append(match)
    return matches


def get_ydna_data(person, include_raw_data=False):
    """
    Y-DNA clade lineage of a person from a 'Y-DNA' attribute.

    Uses the ``yclade`` package when it is installed; otherwise only the raw
    data is returned (with an empty lineage).
    """
    attribute = None
    for attr in person.attribute_list or []:
        if isinstance(attr, dict) and type_string(attr.get("type")) == "Y-DNA":
            attribute = attr
            break
    if attribute is None:
        return {}
    snp_string = attribute.get("value") or ""
    result = {"clade_lineage": [], "tree_version": ""}
    try:
        import yclade  # type: ignore
        from dataclasses import asdict

        snp_results = yclade.snps.parse_snp_results(snp_string)
        tree_data = yclade.tree.get_yfull_tree_data()
        snp_results = yclade.snps.normalize_snp_results(
            snp_results=snp_results, snp_aliases=tree_data.snp_aliases
        )
        ordered = yclade.find.get_ordered_clade_details(tree=tree_data, snps=snp_results)
        if ordered:
            lineage = yclade.find.get_clade_lineage(tree=tree_data, node=ordered[0].name)
            result = {
                "clade_lineage": [asdict(info) for info in lineage],
                "tree_version": tree_data.version,
            }
    except ImportError:
        result["error"] = "yclade is not installed"
    except Exception as exc:  # pragma: no cover - depends on external data
        result["error"] = str(exc)
    if include_raw_data:
        result["raw_data"] = snp_string
    return result
