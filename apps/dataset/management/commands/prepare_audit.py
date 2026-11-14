from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.catalog.legacy.files import import_files
from apps.dataset.sampling import create_sample


class Command(BaseCommand):
    help = "Draw patterns to audit and import their files so their booklets can be read."

    def add_arguments(self, parser):
        parser.add_argument("source", type=Path, help="Path to the MVP Patrons folder")
        parser.add_argument("--owner", required=True)
        parser.add_argument("--size", type=int, default=50)
        parser.add_argument("--seed", type=int, help="Reproducible draw")

    def handle(self, source, owner, size, seed, **options):
        try:
            user = get_user_model().objects.get(username=owner)
        except get_user_model().DoesNotExist:
            raise CommandError(f"User {owner!r} does not exist") from None
        samples = create_sample(user, size, seed)
        refs = {s.pattern.legacy_ref for s in samples}
        report = import_files(source, user, refs=refs)
        for warning in report.warnings:
            self.stderr.write(self.style.WARNING(warning))
        self.stdout.write(
            self.style.SUCCESS(
                f"{len(samples)} patterns to audit; files linked: {report.files_linked}, "
                f"uploaded: {report.blobs_uploaded}"
            )
        )
