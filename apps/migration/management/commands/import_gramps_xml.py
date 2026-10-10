"""
Management command to import a Gramps XML (.gramps) file.

Usage:
    python manage.py import_gramps_xml /path/to/export.gramps
    python manage.py import_gramps_xml /path/to/export.gramps --clear

The .gramps file can be gzipped or plain XML. The import logic lives in
``apps.migration.importer`` and is shared with the import API endpoint.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.migration.importer import ImportError_, import_gramps_xml, object_counts


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
        try:
            import_gramps_xml(options["file"], clear=options["clear"], log=self.stdout.write)
        except (OSError, ImportError_) as exc:
            raise CommandError(str(exc))

        self.stdout.write(self.style.SUCCESS("Import complete!"))
        self._print_counts()

    def _print_counts(self):
        counts = object_counts()
        self.stdout.write("\nDatabase totals:")
        for key in (
            "person", "family", "event", "place", "source", "citation",
            "repository", "media", "note", "tag", "backlinks",
        ):
            self.stdout.write(f"  {key.capitalize():13s} {counts[key]}")
