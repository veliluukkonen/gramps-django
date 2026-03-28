"""
Gramps genealogical data models.

10 primary objects stored as Django models with PostgreSQL.
Handle-based references between objects.
Secondary objects (Name, EventRef, MediaRef, etc.) stored as JSONField.
"""

from django.db import models


class GrampsBaseModel(models.Model):
    """Abstract base for all Gramps primary objects."""

    handle = models.CharField(max_length=50, primary_key=True)
    gramps_id = models.CharField(max_length=50, unique=True, blank=True, null=True, default=None)
    change = models.FloatField(default=0, help_text="Unix timestamp of last modification")
    private = models.BooleanField(default=False)
    tag_list = models.JSONField(default=list, blank=True, help_text="List of Tag handles")

    class Meta:
        abstract = True

    def __str__(self):
        return f"{self.__class__.__name__} {self.gramps_id} ({self.handle})"


class Person(GrampsBaseModel):
    """
    Represents an individual person.

    primary_name: Name object as JSON
        {first_name, surname_list: [{surname, prefix, connector, origintype, primary}],
         suffix, title, call, nick, famnick, group_as, display_as, sort_as,
         date, note_list, citation_list, private, type}
    """

    UNKNOWN = 2
    MALE = 1
    FEMALE = 0
    OTHER = 3

    GENDER_CHOICES = [
        (FEMALE, "Female"),
        (MALE, "Male"),
        (UNKNOWN, "Unknown"),
        (OTHER, "Other"),
    ]

    gender = models.IntegerField(choices=GENDER_CHOICES, default=UNKNOWN)
    primary_name = models.JSONField(default=dict, blank=True)
    alternate_names = models.JSONField(default=list, blank=True)
    event_ref_list = models.JSONField(
        default=list, blank=True,
        help_text='[{ref: event_handle, role, private, note_list, attribute_list}]',
    )
    family_list = models.JSONField(
        default=list, blank=True,
        help_text="List of Family handles where person is a parent",
    )
    parent_family_list = models.JSONField(
        default=list, blank=True,
        help_text="List of Family handles where person is a child",
    )
    person_ref_list = models.JSONField(
        default=list, blank=True,
        help_text='[{ref: person_handle, rel, private, note_list, citation_list}]',
    )
    media_list = models.JSONField(
        default=list, blank=True,
        help_text='[{ref: media_handle, rect, private, note_list, citation_list, attribute_list}]',
    )
    address_list = models.JSONField(default=list, blank=True)
    attribute_list = models.JSONField(default=list, blank=True)
    citation_list = models.JSONField(
        default=list, blank=True,
        help_text="List of Citation handles",
    )
    note_list = models.JSONField(
        default=list, blank=True,
        help_text="List of Note handles",
    )
    urls = models.JSONField(default=list, blank=True)
    lds_ord_list = models.JSONField(default=list, blank=True)
    birth_ref_index = models.IntegerField(default=-1)
    death_ref_index = models.IntegerField(default=-1)

    class Meta:
        db_table = "gramps_person"
        verbose_name_plural = "people"


class Family(GrampsBaseModel):
    """
    Represents a family unit (parents + children).
    """

    father_handle = models.ForeignKey(
        Person,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="families_as_father",
        db_column="father_handle",
    )
    mother_handle = models.ForeignKey(
        Person,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="families_as_mother",
        db_column="mother_handle",
    )
    type = models.CharField(max_length=50, default="Married", blank=True)
    child_ref_list = models.JSONField(
        default=list, blank=True,
        help_text='[{ref: person_handle, frel, mrel, private, note_list, citation_list}]',
    )
    event_ref_list = models.JSONField(default=list, blank=True)
    media_list = models.JSONField(default=list, blank=True)
    attribute_list = models.JSONField(default=list, blank=True)
    citation_list = models.JSONField(default=list, blank=True)
    note_list = models.JSONField(default=list, blank=True)
    lds_ord_list = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "gramps_family"
        verbose_name_plural = "families"


class Place(GrampsBaseModel):
    """
    Represents a geographic location with hierarchical structure.
    """

    name = models.JSONField(
        default=dict, blank=True,
        help_text='{value, lang, date}',
    )
    title = models.CharField(max_length=500, default="", blank=True)
    alt_names = models.JSONField(default=list, blank=True)
    place_type = models.CharField(max_length=100, default="", blank=True)
    code = models.CharField(max_length=50, default="", blank=True)
    lat = models.CharField(max_length=50, default="", blank=True)
    long = models.CharField(max_length=50, default="", blank=True)
    placeref_list = models.JSONField(
        default=list, blank=True,
        help_text='[{ref: place_handle, date}] - parent places in hierarchy',
    )
    alt_loc = models.JSONField(default=list, blank=True)
    urls = models.JSONField(default=list, blank=True)
    media_list = models.JSONField(default=list, blank=True)
    citation_list = models.JSONField(default=list, blank=True)
    note_list = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "gramps_place"


class Event(GrampsBaseModel):
    """
    Represents a life event (birth, death, marriage, etc.).
    """

    type = models.CharField(max_length=100, default="", blank=True)
    date = models.JSONField(
        default=dict, blank=True,
        help_text='{calendar, dateval, modifier, quality, year, sortval, text, newyear}',
    )
    place = models.ForeignKey(
        Place,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
        db_column="place",
    )
    description = models.TextField(default="", blank=True)
    citation_list = models.JSONField(default=list, blank=True)
    media_list = models.JSONField(default=list, blank=True)
    note_list = models.JSONField(default=list, blank=True)
    attribute_list = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "gramps_event"


class Source(GrampsBaseModel):
    """
    Represents a source of genealogical information.
    """

    title = models.CharField(max_length=500, default="", blank=True)
    author = models.CharField(max_length=500, default="", blank=True)
    pubinfo = models.TextField(default="", blank=True)
    abbrev = models.CharField(max_length=200, default="", blank=True)
    reporef_list = models.JSONField(
        default=list, blank=True,
        help_text='[{ref: repo_handle, call_number, media_type, private, note_list}]',
    )
    media_list = models.JSONField(default=list, blank=True)
    note_list = models.JSONField(default=list, blank=True)
    attribute_list = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "gramps_source"


class Citation(GrampsBaseModel):
    """
    Represents a citation of a source (specific reference).
    """

    CONF_VERY_LOW = 0
    CONF_LOW = 1
    CONF_NORMAL = 2
    CONF_HIGH = 3
    CONF_VERY_HIGH = 4

    source_handle = models.ForeignKey(
        Source,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="citations",
        db_column="source_handle",
    )
    page = models.CharField(max_length=500, default="", blank=True)
    date = models.JSONField(default=dict, blank=True)
    confidence = models.IntegerField(default=CONF_NORMAL)
    media_list = models.JSONField(default=list, blank=True)
    note_list = models.JSONField(default=list, blank=True)
    attribute_list = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "gramps_citation"


class Repository(GrampsBaseModel):
    """
    Represents a repository (archive, library, etc.) holding sources.
    """

    name = models.CharField(max_length=500, default="", blank=True)
    type = models.CharField(max_length=100, default="", blank=True)
    address_list = models.JSONField(default=list, blank=True)
    urls = models.JSONField(default=list, blank=True)
    note_list = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "gramps_repository"
        verbose_name_plural = "repositories"


class MediaObject(GrampsBaseModel):
    """
    Represents a media object (photo, document, etc.).
    """

    path = models.CharField(max_length=1000, default="", blank=True)
    mime = models.CharField(max_length=100, default="", blank=True)
    desc = models.CharField(max_length=500, default="", blank=True)
    checksum = models.CharField(max_length=100, default="", blank=True)
    date = models.JSONField(default=dict, blank=True)
    attribute_list = models.JSONField(default=list, blank=True)
    citation_list = models.JSONField(default=list, blank=True)
    note_list = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "gramps_media"
        verbose_name = "media object"
        verbose_name_plural = "media objects"


class Note(GrampsBaseModel):
    """
    Represents a textual note with optional formatting.

    text: StyledText as JSON {string, tags: [{name, value, ranges: [[start, end]]}]}
    """

    text = models.JSONField(
        default=dict, blank=True,
        help_text='{string: "text content", tags: [{name, value, ranges}]}',
    )
    format = models.IntegerField(default=0, help_text="0=flowed, 1=formatted")
    type = models.CharField(max_length=100, default="General", blank=True)

    class Meta:
        db_table = "gramps_note"


class Tag(models.Model):
    """
    Represents a tag for categorizing objects.

    Note: Tag does not have gramps_id or private fields.
    """

    handle = models.CharField(max_length=50, primary_key=True)
    name = models.CharField(max_length=200)
    color = models.CharField(max_length=20, default="#000000000000", blank=True)
    priority = models.IntegerField(default=0)
    change = models.FloatField(default=0)

    class Meta:
        db_table = "gramps_tag"

    def __str__(self):
        return self.name


class BacklinkIndex(models.Model):
    """
    Index table for tracking references between objects.
    Enables fast backlink lookups.
    Populated during writes and data imports.
    """

    source_handle = models.CharField(max_length=50, db_index=True)
    source_type = models.CharField(max_length=20, db_index=True)
    target_handle = models.CharField(max_length=50, db_index=True)
    target_type = models.CharField(max_length=20, db_index=True)

    class Meta:
        db_table = "gramps_backlink_index"
        indexes = [
            models.Index(fields=["target_handle", "target_type"]),
            models.Index(fields=["source_handle", "source_type"]),
        ]
        unique_together = [
            ("source_handle", "source_type", "target_handle", "target_type"),
        ]

    def __str__(self):
        return f"{self.source_type}:{self.source_handle} -> {self.target_type}:{self.target_handle}"
