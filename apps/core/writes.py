"""
Write operations with transaction recording.

All creates, updates and deletes of Gramps objects go through a
``TransactionBuilder`` so that:

- handles and Gramps IDs are generated when missing,
- family membership lists on people are kept in sync,
- birth/death reference indices on people are kept in sync,
- references to deleted objects are removed from other objects,
- the backlink index is updated,
- a ``Transaction`` with its ``TransactionChange`` rows is stored
  (for the revision history and undo), and
- the gramps-web-api style transaction JSON is returned, i.e. a list of
  ``{"type": "add"|"update"|"delete", "handle", "_class", "old", "new"}``.
"""

import re
import time
import uuid

from django.db import transaction as db_transaction

from . import serializers as ser
from .backlinks import populate_backlinks_for_object
from .models import (
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
    Transaction,
    TransactionChange,
)


class WriteError(ValueError):
    """Raised for invalid write requests (maps to HTTP 400)."""


class NotFound(WriteError):
    """Raised when an object to update/delete does not exist (HTTP 404)."""


# Gramps class name -> (model, serializer, gramps_id prefix)
CLASS_INFO = {
    "Person": (Person, ser.PersonSerializer, "I"),
    "Family": (Family, ser.FamilySerializer, "F"),
    "Event": (Event, ser.EventSerializer, "E"),
    "Place": (Place, ser.PlaceSerializer, "P"),
    "Source": (Source, ser.SourceSerializer, "S"),
    "Citation": (Citation, ser.CitationSerializer, "C"),
    "Repository": (Repository, ser.RepositorySerializer, "R"),
    "Media": (MediaObject, ser.MediaObjectSerializer, "O"),
    "Note": (Note, ser.NoteSerializer, "N"),
    "Tag": (Tag, ser.TagSerializer, None),
}

MODEL_TO_CLASS = {info[0]: name for name, info in CLASS_INFO.items()}

# Order in which objects must be created so that foreign keys resolve.
CLASS_ORDER = [
    "Tag", "Note", "Repository", "Source", "Citation", "Place",
    "Media", "Event", "Person", "Family",
]

# Foreign key model fields that the API exposes as plain handle strings.
FK_FIELDS = {
    "Family": ["father_handle", "mother_handle"],
    "Event": ["place"],
    "Citation": ["source_handle"],
}

# Keys that are computed on read and must never be written.
READ_ONLY_KEYS = {"_class", "extended", "profile", "backlinks", "formatted"}

_ID_RE_CACHE = {}


def class_name_for_model(model):
    return MODEL_TO_CLASS[model]


def model_for_class(class_name):
    try:
        return CLASS_INFO[class_name][0]
    except KeyError:
        raise WriteError(f"Unknown object class '{class_name}'")


def normalize_class_name(name):
    """Accept 'media', 'MediaObject', 'people' style names as well."""
    if not name:
        return name
    aliases = {
        "mediaobject": "Media", "media": "Media", "people": "Person",
        "person": "Person", "families": "Family", "family": "Family",
        "events": "Event", "event": "Event", "places": "Place", "place": "Place",
        "sources": "Source", "source": "Source", "citations": "Citation",
        "citation": "Citation", "repositories": "Repository",
        "repository": "Repository", "notes": "Note", "note": "Note",
        "tags": "Tag", "tag": "Tag",
    }
    return aliases.get(name.lower(), name)


def serialize(instance):
    """Serialize a model instance to a plain dict (no request context)."""
    serializer_class = CLASS_INFO[class_name_for_model(type(instance))][1]
    return dict(serializer_class(instance).data)


def generate_handle():
    """Create a new unique handle (same style as gramps create_id)."""
    return uuid.uuid4().hex


def generate_gramps_id(class_name):
    """Return the next free Gramps ID for a class, e.g. I0042."""
    model, _, prefix = CLASS_INFO[class_name]
    if prefix is None:
        return None
    regex = _ID_RE_CACHE.get(prefix)
    if regex is None:
        regex = re.compile(rf"^{prefix}(\d+)$")
        _ID_RE_CACHE[prefix] = regex
    highest = -1
    for gid in model.objects.filter(gramps_id__startswith=prefix).values_list(
        "gramps_id", flat=True
    ):
        m = regex.match(gid or "")
        if m:
            highest = max(highest, int(m.group(1)))
    return f"{prefix}{highest + 1:04d}"


def _model_field_names(model):
    return {f.name for f in model._meta.fields}


def prepare_data(obj_dict, class_name):
    """
    Convert an API object dict into model field values.

    Unknown and read-only keys are dropped, FK handles are mapped to
    ``<field>_id`` and the handle is left untouched (None if missing).
    """
    model = model_for_class(class_name)
    fields = _model_field_names(model)
    fk_fields = set(FK_FIELDS.get(class_name, []))
    data = {}
    for key, value in obj_dict.items():
        if key in READ_ONLY_KEYS:
            continue
        if key in fk_fields:
            data[f"{key}_id"] = value or None
        elif key in fields:
            data[key] = value
    if "gramps_id" in data and data["gramps_id"] == "":
        data["gramps_id"] = None
    return data


def _event_type(handle, cache):
    if handle in cache:
        return cache[handle]
    try:
        etype = Event.objects.only("type").get(pk=handle).type
    except Event.DoesNotExist:
        etype = None
    cache[handle] = etype
    return etype


def _role_is_primary(ref):
    role = ref.get("role", "Primary") if isinstance(ref, dict) else "Primary"
    if isinstance(role, dict):
        role = role.get("string", "Primary")
    return role in ("Primary", "", None)


def set_birth_death_index(person):
    """Compute birth_ref_index / death_ref_index from the event refs."""
    birth = -1
    death = -1
    cache = {}
    for i, ref in enumerate(person.event_ref_list or []):
        if not isinstance(ref, dict) or not _role_is_primary(ref):
            continue
        etype = _event_type(ref.get("ref"), cache)
        if etype == "Birth" and birth < 0:
            birth = i
        elif etype == "Death" and death < 0:
            death = i
    person.birth_ref_index = birth
    person.death_ref_index = death


class TransactionBuilder:
    """
    Collects object changes and commits them as one Transaction.

    Usage::

        builder = TransactionBuilder(user, "Add person")
        builder.add({"_class": "Person", ...})
        result = builder.commit()   # -> transaction JSON list
    """

    def __init__(self, user=None, description=""):
        self.user = user if (user is not None and getattr(user, "is_authenticated", False)) else None
        self.description = description
        self.changes = []  # list of dicts: type, handle, _class, old, new
        self._touched = {}  # (class_name, handle) -> instance or None (deleted)

    # --- recording -----------------------------------------------------

    def _record(self, change_type, class_name, handle, old, new):
        self.changes.append({
            "type": change_type,
            "handle": handle,
            "_class": class_name,
            "old": old,
            "new": new,
        })

    def _get(self, class_name, handle):
        model = model_for_class(class_name)
        try:
            return model.objects.get(pk=handle)
        except model.DoesNotExist:
            return None

    # --- public operations -------------------------------------------

    def add(self, obj_dict, class_name=None):
        """Create a new object. Returns the saved instance."""
        class_name = normalize_class_name(class_name or obj_dict.get("_class"))
        if not class_name:
            raise WriteError("Missing _class")
        model = model_for_class(class_name)
        data = prepare_data(obj_dict, class_name)

        handle = data.get("handle") or generate_handle()
        data["handle"] = handle
        if model.objects.filter(pk=handle).exists():
            raise WriteError(f"Handle {handle} already exists")

        if class_name != "Tag":
            gramps_id = data.get("gramps_id") or generate_gramps_id(class_name)
            if model.objects.filter(gramps_id=gramps_id).exists():
                raise WriteError(f"Gramps ID {gramps_id} already exists")
            data["gramps_id"] = gramps_id

        data["change"] = time.time()
        instance = model(**data)
        if class_name == "Person":
            set_birth_death_index(instance)
        instance.save()

        if class_name == "Family":
            self._family_added(instance)

        self._record("add", class_name, handle, None, serialize(instance))
        self._touched[(class_name, handle)] = instance
        return instance

    def update(self, obj_dict, class_name=None, handle=None):
        """Replace an existing object with the given data."""
        class_name = normalize_class_name(class_name or obj_dict.get("_class"))
        handle = handle or obj_dict.get("handle")
        if not handle:
            raise WriteError("Missing handle")
        old_instance = self._get(class_name, handle)
        if old_instance is None:
            raise NotFound(f"{class_name} {handle} not found")
        old_data = serialize(old_instance)

        data = prepare_data(obj_dict, class_name)
        data.pop("handle", None)
        if class_name != "Tag" and not data.get("gramps_id"):
            data["gramps_id"] = old_instance.gramps_id
        if class_name != "Tag":
            model = model_for_class(class_name)
            if model.objects.filter(gramps_id=data["gramps_id"]).exclude(pk=handle).exists():
                raise WriteError(f"Gramps ID {data['gramps_id']} already exists")
        data["change"] = time.time()

        instance = old_instance
        for key, value in data.items():
            setattr(instance, key, value)
        if class_name == "Person":
            set_birth_death_index(instance)
        instance.save()

        if class_name == "Family":
            self._family_updated(old_data, instance)

        self._record("update", class_name, handle, old_data, serialize(instance))
        self._touched[(class_name, handle)] = instance
        return instance

    def delete(self, class_name, handle):
        """Delete an object and remove references to it from other objects."""
        class_name = normalize_class_name(class_name)
        instance = self._get(class_name, handle)
        if instance is None:
            raise NotFound(f"{class_name} {handle} not found")
        old_data = serialize(instance)

        self._remove_references(class_name, instance)

        if class_name == "Source":
            # Citations of a deleted source are deleted as well (as in Gramps).
            for citation in Citation.objects.filter(source_handle_id=handle):
                self.delete("Citation", citation.handle)

        instance.delete()
        BacklinkIndex.objects.filter(source_handle=handle).delete()
        BacklinkIndex.objects.filter(target_handle=handle).delete()
        self._record("delete", class_name, handle, old_data, None)
        self._touched[(class_name, handle)] = None
        return old_data

    def commit(self):
        """Store the transaction and refresh backlinks. Returns the JSON list."""
        for (class_name, handle), instance in self._touched.items():
            if instance is not None:
                populate_backlinks_for_object(instance, class_name)
        if self.changes:
            txn = Transaction.objects.create(
                timestamp=time.time(),
                description=self.description,
                user=self.user,
            )
            TransactionChange.objects.bulk_create([
                TransactionChange(
                    transaction=txn,
                    order=i,
                    obj_class=c["_class"],
                    obj_handle=c["handle"],
                    trans_type={"add": 0, "update": 1, "delete": 2}[c["type"]],
                    old_data=c["old"],
                    new_data=c["new"],
                )
                for i, c in enumerate(self.changes)
            ])
            self.transaction_id = txn.id
        return self.changes

    # --- internal helpers ---------------------------------------------

    def _save_person(self, person, old):
        """Save a person whose lists were changed; ``old`` is the snapshot
        taken *before* the change so that undo can restore it."""
        person.change = time.time()
        person.save()
        new = serialize(person)
        if old != new:
            self._record("update", "Person", person.handle, old, new)
            self._touched[("Person", person.handle)] = person

    def _change_person_list(self, person, field, add=None, remove=None):
        """Add/remove a handle in a person's list field and record the update."""
        old = serialize(person)
        values = list(getattr(person, field) or [])
        if remove is not None:
            values = [h for h in values if h != remove]
        if add is not None and add not in values:
            values.append(add)
        setattr(person, field, values)
        self._save_person(person, old)

    def _person(self, handle):
        if not handle:
            return None
        try:
            return Person.objects.get(pk=handle)
        except Person.DoesNotExist:
            return None

    def _family_added(self, family):
        for parent_handle in (family.father_handle_id, family.mother_handle_id):
            parent = self._person(parent_handle)
            if parent is not None and family.handle not in (parent.family_list or []):
                self._change_person_list(parent, "family_list", add=family.handle)
        for ref in family.child_ref_list or []:
            child = self._person(ref.get("ref") if isinstance(ref, dict) else None)
            if child is not None and family.handle not in (child.parent_family_list or []):
                self._change_person_list(child, "parent_family_list", add=family.handle)

    def _family_updated(self, old_data, family):
        fh = family.handle
        for old_h, new_h in (
            (old_data.get("father_handle") or None, family.father_handle_id),
            (old_data.get("mother_handle") or None, family.mother_handle_id),
        ):
            if old_h == new_h:
                continue
            old_p = self._person(old_h)
            if old_p is not None and fh in (old_p.family_list or []):
                self._change_person_list(old_p, "family_list", remove=fh)
            new_p = self._person(new_h)
            if new_p is not None and fh not in (new_p.family_list or []):
                self._change_person_list(new_p, "family_list", add=fh)

        old_children = {
            r.get("ref") for r in (old_data.get("child_ref_list") or []) if isinstance(r, dict)
        }
        new_children = {
            r.get("ref") for r in (family.child_ref_list or []) if isinstance(r, dict)
        }
        for h in old_children - new_children:
            child = self._person(h)
            if child is not None and fh in (child.parent_family_list or []):
                self._change_person_list(child, "parent_family_list", remove=fh)
        for h in new_children - old_children:
            child = self._person(h)
            if child is not None and fh not in (child.parent_family_list or []):
                self._change_person_list(child, "parent_family_list", add=fh)

    def _remove_references(self, class_name, instance):
        """Strip references to ``instance`` from all objects that point to it."""
        handle = instance.handle
        target_type = class_name
        links = BacklinkIndex.objects.filter(target_handle=handle, target_type=target_type)
        for link in links:
            src_class = normalize_class_name(link.source_type)
            src = self._get(src_class, link.source_handle)
            if src is None:
                continue
            old = serialize(src)
            self._strip_handle(src, src_class, class_name, handle)
            new = serialize(src)
            if old != new:
                src.change = time.time()
                src.save()
                self._record("update", src_class, src.handle, old, serialize(src))
                self._touched[(src_class, src.handle)] = src

        if class_name == "Person":
            # Families may reference the person as parent (FK) or child.
            for fam in Family.objects.filter(father_handle_id=handle):
                self._update_family_field(fam, "father_handle_id", None)
            for fam in Family.objects.filter(mother_handle_id=handle):
                self._update_family_field(fam, "mother_handle_id", None)
        elif class_name == "Place":
            for evt in Event.objects.filter(place_id=handle):
                old = serialize(evt)
                evt.place_id = None
                evt.change = time.time()
                evt.save()
                self._record("update", "Event", evt.handle, old, serialize(evt))
                self._touched[("Event", evt.handle)] = evt

    def _update_family_field(self, fam, field, value):
        old = serialize(fam)
        setattr(fam, field, value)
        fam.change = time.time()
        fam.save()
        self._record("update", "Family", fam.handle, old, serialize(fam))
        self._touched[("Family", fam.handle)] = fam

    @staticmethod
    def _strip_handle(obj, obj_class, target_class, handle):
        """Remove every reference to ``handle`` of ``target_class`` from ``obj``."""

        def filter_handles(lst):
            return [h for h in (lst or []) if h != handle]

        def filter_refs(lst):
            return [
                r for r in (lst or [])
                if not (isinstance(r, dict) and r.get("ref") == handle)
                and r != handle
            ]

        if target_class == "Tag" and hasattr(obj, "tag_list"):
            obj.tag_list = filter_handles(obj.tag_list)
        elif target_class == "Note" and hasattr(obj, "note_list"):
            obj.note_list = filter_handles(obj.note_list)
        elif target_class == "Citation" and hasattr(obj, "citation_list"):
            obj.citation_list = filter_handles(obj.citation_list)
        elif target_class == "Media" and hasattr(obj, "media_list"):
            obj.media_list = filter_refs(obj.media_list)
        elif target_class == "Event" and hasattr(obj, "event_ref_list"):
            obj.event_ref_list = filter_refs(obj.event_ref_list)
            if obj_class == "Person":
                set_birth_death_index(obj)
        elif target_class == "Repository" and hasattr(obj, "reporef_list"):
            obj.reporef_list = filter_refs(obj.reporef_list)
        elif target_class == "Place" and hasattr(obj, "placeref_list"):
            obj.placeref_list = filter_refs(obj.placeref_list)
        elif target_class == "Person":
            if hasattr(obj, "person_ref_list"):
                obj.person_ref_list = filter_refs(obj.person_ref_list)
            if obj_class == "Family":
                obj.child_ref_list = filter_refs(obj.child_ref_list)
        elif target_class == "Family" and obj_class == "Person":
            obj.family_list = filter_handles(obj.family_list)
            obj.parent_family_list = filter_handles(obj.parent_family_list)


def sort_for_creation(obj_dicts):
    """Order object dicts so that FK targets are created first."""
    order = {name: i for i, name in enumerate(CLASS_ORDER)}
    return sorted(
        obj_dicts,
        key=lambda d: order.get(normalize_class_name(d.get("_class", "")), len(order)),
    )


def run_transaction(user, description, func):
    """
    Run ``func(builder)`` inside a DB transaction and commit the builder.

    Returns the transaction JSON list.
    """
    with db_transaction.atomic():
        builder = TransactionBuilder(user, description)
        func(builder)
        return builder.commit()
