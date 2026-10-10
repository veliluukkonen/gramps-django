"""
Check & repair of the tree (the equivalent of gramps-web-api ``check.py``).

Fixes applied:

- references to objects that no longer exist are removed (including
  references nested in names, attributes, event/media/child/person refs,
  addresses and LDS ordinances),
- family links are made consistent in both directions (``family_list`` /
  ``parent_family_list`` on people versus father/mother/children on
  families),
- birth/death reference indices of people are recomputed,
- the backlink index is rebuilt for all objects.

Object changes go through ``TransactionBuilder`` so that they appear in
the revision history. The result has the gramps-web-api shape
``{"num_errors": int, "message": str}``.
"""

import copy

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
from apps.core.writes import CLASS_INFO, run_transaction, serialize, set_birth_death_index

MODELS = [
    ("Person", Person), ("Family", Family), ("Event", Event), ("Place", Place),
    ("Source", Source), ("Citation", Citation), ("Repository", Repository),
    ("Media", MediaObject), ("Note", Note), ("Tag", Tag),
]

# JSON keys holding lists of plain handles -> target class
HANDLE_LIST_KEYS = {
    "citation_list": "Citation",
    "note_list": "Note",
    "tag_list": "Tag",
    "family_list": "Family",
    "parent_family_list": "Family",
}
# JSON keys holding lists of {"ref": handle, ...} dicts -> target class
REF_LIST_KEYS = {
    "event_ref_list": "Event",
    "media_list": "Media",
    "person_ref_list": "Person",
    "child_ref_list": "Person",
    "placeref_list": "Place",
    "reporef_list": "Repository",
}
# JSON keys holding a single handle string -> target class
HANDLE_KEYS = {"place": "Place", "sealed_to": "Family"}
# Top-level keys exposed by the serializers as plain handle strings (FKs)
FK_KEYS = {
    "Family": {"father_handle": "Person", "mother_handle": "Person"},
    "Event": {"place": "Place"},
    "Citation": {"source_handle": "Source"},
}
# Keys whose values are lists of dicts to recurse into (no own ref)
NESTED_LIST_KEYS = {
    "alternate_names", "attribute_list", "address_list", "urls",
    "lds_ord_list", "alt_names", "surname_list",
}
NESTED_DICT_KEYS = {"primary_name"}


class RefCleaner:
    """Removes references to missing objects from serialized object dicts."""

    def __init__(self, existing):
        self.existing = existing  # class -> set of handles
        self.removed = 0

    def _ok(self, class_name, handle):
        return handle in self.existing.get(class_name, ())

    def clean(self, data, class_name):
        """Return a cleaned copy of ``data`` (a serialized object dict)."""
        data = copy.deepcopy(data)
        for key, target in FK_KEYS.get(class_name, {}).items():
            if data.get(key) and not self._ok(target, data[key]):
                data[key] = None
                self.removed += 1
        self._clean_dict(data, top_level_class=class_name)
        return data

    def _clean_dict(self, data, top_level_class=None):
        for key, value in list(data.items()):
            if key in HANDLE_LIST_KEYS and isinstance(value, list):
                target = HANDLE_LIST_KEYS[key]
                kept = [h for h in value if self._ok(target, h)]
                self.removed += len(value) - len(kept)
                data[key] = kept
            elif key in REF_LIST_KEYS and isinstance(value, list):
                target = REF_LIST_KEYS[key]
                kept = []
                for ref in value:
                    if isinstance(ref, dict):
                        if not self._ok(target, ref.get("ref")):
                            self.removed += 1
                            continue
                        self._clean_dict(ref)
                        kept.append(ref)
                    elif isinstance(ref, str):
                        if self._ok(target, ref):
                            kept.append(ref)
                        else:
                            self.removed += 1
                data[key] = kept
            elif key in HANDLE_KEYS and top_level_class is None and isinstance(value, str):
                # 'place' at top level is an Event FK handled in clean(); here
                # it is the place of an LDS ordinance.
                if value and not self._ok(HANDLE_KEYS[key], value):
                    data[key] = ""
                    self.removed += 1
            elif key in NESTED_LIST_KEYS and isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        self._clean_dict(item)
            elif key in NESTED_DICT_KEYS and isinstance(value, dict):
                self._clean_dict(value)


def _existing_handles():
    return {name: set(model.objects.values_list("handle", flat=True)) for name, model in MODELS}


def _fix_family_links(families, people_links):
    """
    Make person<->family links consistent. ``people_links`` is a dict
    handle -> {"family_list": set, "parent_family_list": set} that is
    updated in place with the links implied by the families.
    """
    expected = {h: {"family_list": set(), "parent_family_list": set()} for h in people_links}
    for fam in families:
        for parent in (fam.father_handle_id, fam.mother_handle_id):
            if parent in expected:
                expected[parent]["family_list"].add(fam.handle)
        for ref in fam.child_ref_list or []:
            child = ref.get("ref") if isinstance(ref, dict) else ref
            if child in expected:
                expected[child]["parent_family_list"].add(fam.handle)
    return expected


def repair_tree(user=None):
    """Run all checks; returns ``{"num_errors": n, "message": text}``."""
    existing = _existing_handles()
    cleaner = RefCleaner(existing)
    lines = []
    num_errors = 0

    updates = []  # (class_name, handle, new_data)
    for class_name, model in MODELS:
        if class_name == "Tag":
            continue
        for obj in model.objects.all().iterator(chunk_size=500):
            data = serialize(obj)
            before = cleaner.removed
            cleaned = cleaner.clean(data, class_name)
            if cleaner.removed != before:
                updates.append((class_name, obj.handle, cleaned))
    if cleaner.removed:
        lines.append(f"{cleaner.removed} references to missing objects removed")
        num_errors += cleaner.removed

    # Family link consistency (based on the cleaned data where available).
    pending = {(c, h): d for c, h, d in updates}
    families = []
    for fam in Family.objects.all().iterator(chunk_size=500):
        data = pending.get(("Family", fam.handle))
        if data is not None:
            fam.father_handle_id = data.get("father_handle") or None
            fam.mother_handle_id = data.get("mother_handle") or None
            fam.child_ref_list = data.get("child_ref_list") or []
        families.append(fam)
    people_links = {}
    for handle in existing["Person"]:
        people_links[handle] = None
    expected = _fix_family_links(families, people_links)
    link_fixes = 0
    for person in Person.objects.all().iterator(chunk_size=500):
        data = pending.get(("Person", person.handle)) or serialize(person)
        exp = expected[person.handle]
        new_family_list = [h for h in data.get("family_list") or [] if h in exp["family_list"]]
        new_family_list += sorted(exp["family_list"] - set(new_family_list))
        new_parent_list = [
            h for h in data.get("parent_family_list") or [] if h in exp["parent_family_list"]
        ]
        new_parent_list += sorted(exp["parent_family_list"] - set(new_parent_list))
        changed = False
        if new_family_list != list(data.get("family_list") or []):
            data["family_list"] = new_family_list
            changed = True
        if new_parent_list != list(data.get("parent_family_list") or []):
            data["parent_family_list"] = new_parent_list
            changed = True
        if changed:
            link_fixes += 1
            pending[("Person", person.handle)] = data
    if link_fixes:
        lines.append(f"{link_fixes} people with inconsistent family links fixed")
        num_errors += link_fixes

    # Birth/death indices.
    index_fixes = 0
    for person in Person.objects.all().iterator(chunk_size=500):
        data = pending.get(("Person", person.handle))
        probe = Person(handle=person.handle, event_ref_list=(data or {}).get("event_ref_list", person.event_ref_list))
        set_birth_death_index(probe)
        current = (data or serialize(person))
        if (probe.birth_ref_index, probe.death_ref_index) != (
            current.get("birth_ref_index", -1), current.get("death_ref_index", -1)
        ):
            if data is None:
                data = serialize(person)
                pending[("Person", person.handle)] = data
            data["birth_ref_index"] = probe.birth_ref_index
            data["death_ref_index"] = probe.death_ref_index
            index_fixes += 1
    if index_fixes:
        lines.append(f"{index_fixes} birth/death references updated")
        num_errors += index_fixes

    # Apply all changes in one recorded transaction.
    if pending:
        def func(builder):
            for (class_name, handle), data in pending.items():
                builder.update(data, class_name, handle)

        run_transaction(user, "Check and repair", func)

    # Rebuild the backlink index.
    before = BacklinkIndex.objects.count()
    BacklinkIndex.objects.all().delete()
    for class_name, model in MODELS:
        if class_name == "Tag":
            continue
        for obj in model.objects.all().iterator(chunk_size=500):
            populate_backlinks_for_object(obj, class_name)
    after = BacklinkIndex.objects.count()
    if before != after:
        lines.append(f"Backlink index rebuilt ({before} -> {after} entries)")
        num_errors += abs(after - before)
    else:
        lines.append(f"Backlink index rebuilt ({after} entries)")

    if num_errors == 0:
        message = "No errors were found: the database has passed internal checks.\n"
    else:
        message = "\n".join(lines) + "\n"
    return {"num_errors": num_errors, "message": message}


__all__ = ["repair_tree", "RefCleaner", "CLASS_INFO"]
