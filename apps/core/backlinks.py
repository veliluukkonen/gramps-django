"""
Backlinks implementation.

Populates and queries the BacklinkIndex table to find
which objects reference a given object.
"""

from .models import BacklinkIndex


def get_backlinks(handle, obj_type=None):
    """
    Get all objects that reference the given handle.

    Returns dict: {type_name: [handle1, handle2, ...]}
    """
    qs = BacklinkIndex.objects.filter(target_handle=handle)
    if obj_type:
        qs = qs.filter(target_type=obj_type)

    result = {}
    for bl in qs:
        type_key = bl.source_type.lower()
        if type_key not in result:
            result[type_key] = []
        result[type_key].append(bl.source_handle)

    return result


def populate_backlinks_for_object(obj, obj_type):
    """
    Populate BacklinkIndex for a given object.
    Scans all handle references in the object and creates index entries.
    """
    source_handle = obj.handle
    source_type = obj_type

    # Remove existing backlinks for this source
    BacklinkIndex.objects.filter(
        source_handle=source_handle, source_type=source_type
    ).delete()

    entries = []

    # Helper to add backlink entries
    def add_link(target_handle, target_type):
        if target_handle:
            entries.append(BacklinkIndex(
                source_handle=source_handle,
                source_type=source_type,
                target_handle=target_handle,
                target_type=target_type,
            ))

    def add_ref_list(ref_list, target_type):
        for ref in (ref_list or []):
            if isinstance(ref, dict):
                add_link(ref.get("ref"), target_type)
            elif isinstance(ref, str):
                add_link(ref, target_type)

    # Scan handle references based on object type
    if obj_type == "Person":
        add_ref_list(obj.event_ref_list, "Event")
        add_ref_list(obj.media_list, "Media")
        add_ref_list(obj.person_ref_list, "Person")
        for h in (obj.family_list or []):
            add_link(h, "Family")
        for h in (obj.parent_family_list or []):
            add_link(h, "Family")
        for h in (obj.citation_list or []):
            add_link(h, "Citation")
        for h in (obj.note_list or []):
            add_link(h, "Note")
        for h in (obj.tag_list or []):
            add_link(h, "Tag")

    elif obj_type == "Family":
        add_link(obj.father_handle_id, "Person")
        add_link(obj.mother_handle_id, "Person")
        add_ref_list(obj.child_ref_list, "Person")
        add_ref_list(obj.event_ref_list, "Event")
        add_ref_list(obj.media_list, "Media")
        for h in (obj.citation_list or []):
            add_link(h, "Citation")
        for h in (obj.note_list or []):
            add_link(h, "Note")
        for h in (obj.tag_list or []):
            add_link(h, "Tag")

    elif obj_type == "Event":
        add_link(obj.place_id, "Place")
        add_ref_list(obj.media_list, "Media")
        for h in (obj.citation_list or []):
            add_link(h, "Citation")
        for h in (obj.note_list or []):
            add_link(h, "Note")
        for h in (obj.tag_list or []):
            add_link(h, "Tag")

    elif obj_type == "Place":
        add_ref_list(obj.placeref_list, "Place")
        add_ref_list(obj.media_list, "Media")
        for h in (obj.citation_list or []):
            add_link(h, "Citation")
        for h in (obj.note_list or []):
            add_link(h, "Note")
        for h in (obj.tag_list or []):
            add_link(h, "Tag")

    elif obj_type == "Source":
        add_ref_list(obj.reporef_list, "Repository")
        add_ref_list(obj.media_list, "Media")
        for h in (obj.note_list or []):
            add_link(h, "Note")
        for h in (obj.tag_list or []):
            add_link(h, "Tag")

    elif obj_type == "Citation":
        add_link(obj.source_handle_id, "Source")
        add_ref_list(obj.media_list, "Media")
        for h in (obj.note_list or []):
            add_link(h, "Note")
        for h in (obj.tag_list or []):
            add_link(h, "Tag")

    elif obj_type == "Repository":
        for h in (obj.note_list or []):
            add_link(h, "Note")
        for h in (obj.tag_list or []):
            add_link(h, "Tag")

    elif obj_type == "Media":
        for h in (obj.citation_list or []):
            add_link(h, "Citation")
        for h in (obj.note_list or []):
            add_link(h, "Note")
        for h in (obj.tag_list or []):
            add_link(h, "Tag")

    elif obj_type == "Note":
        for h in (obj.tag_list or []):
            add_link(h, "Tag")

    if entries:
        BacklinkIndex.objects.bulk_create(entries, ignore_conflicts=True)
