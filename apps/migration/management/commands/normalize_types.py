"""
Convert localized type names stored in the database to standard Gramps
values (e.g. event type "Syntymä" -> "Birth") and recompute the birth/death
reference indices of all people.

    python manage.py normalize_types [--dry-run]
"""

import copy

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.core.gramps_types import normalize_types
from apps.core.writes import CLASS_INFO, set_birth_death_index

TYPE_FIELDS = [
    "type", "place_type", "primary_name", "alternate_names", "event_ref_list",
    "child_ref_list", "attribute_list", "media_list", "urls", "reporef_list",
]


class Command(BaseCommand):
    help = "Normalize localized type names and recompute birth/death indices."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, dry_run=False, **options):
        with transaction.atomic():
            for class_name, (model, _, _) in CLASS_INFO.items():
                fields = [f.name for f in model._meta.fields if f.name in TYPE_FIELDS]
                if not fields:
                    continue
                for obj in model.objects.all():
                    data = {f: copy.deepcopy(getattr(obj, f)) for f in fields}
                    normalize_types(data, class_name)
                    changed = [f for f in fields if data[f] != getattr(obj, f)]
                    if changed:
                        self.stdout.write(f"{class_name} {obj.gramps_id or obj.pk}: {', '.join(changed)}")
                        for f in changed:
                            setattr(obj, f, data[f])
                        obj.save(update_fields=changed)

            model = CLASS_INFO["Person"][0]
            for person in model.objects.all():
                old = (person.birth_ref_index, person.death_ref_index)
                set_birth_death_index(person)
                new = (person.birth_ref_index, person.death_ref_index)
                if new != old:
                    self.stdout.write(f"Person {person.gramps_id}: birth/death index {old} -> {new}")
                    person.save(update_fields=["birth_ref_index", "death_ref_index"])

            if dry_run:
                transaction.set_rollback(True)
                self.stdout.write("Dry run: nothing saved.")
