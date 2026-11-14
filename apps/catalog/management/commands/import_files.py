from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.template.defaultfilters import filesizeformat

from apps.catalog.legacy.files import import_files


class Command(BaseCommand):
    help = (
        "Upload the pattern files of the MVP Patrons folder to storage and link them to the "
        "imported patterns. Run import_inventory first. Safe to re-run."
    )

    def add_arguments(self, parser):
        parser.add_argument("source", type=Path, help="Path to the Patrons folder")
        parser.add_argument("--owner", required=True, help="Username owning the patterns")
        parser.add_argument(
            "--ref", type=int, action="append", help="Only this inventory ref (repeatable)"
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="List what would be linked, upload nothing"
        )

    def handle(self, source, owner, ref, dry_run, **options):
        try:
            user = get_user_model().objects.get(username=owner)
        except get_user_model().DoesNotExist:
            raise CommandError(f"User {owner!r} does not exist") from None
        if not source.is_dir():
            raise CommandError(f"{source} is not a directory")

        report = import_files(source, user, refs=set(ref or ()), dry_run=dry_run)

        for warning in report.warnings:
            self.stderr.write(self.style.WARNING(warning))
        prefix = "[dry-run] " if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}patterns: {report.patterns}; files linked: {report.files_linked}, "
                f"unchanged: {report.files_unchanged}; total {filesizeformat(report.bytes_total)}; "
                f"uploaded: {report.blobs_uploaded} blobs "
                f"({filesizeformat(report.bytes_uploaded)}); warnings: {len(report.warnings)}"
            )
        )
