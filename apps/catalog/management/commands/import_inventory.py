from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.catalog.legacy.importer import import_inventory


class Command(BaseCommand):
    help = "Import the MVP inventory (CSV files of the Patrons folder). Safe to re-run."

    def add_arguments(self, parser):
        parser.add_argument("source", type=Path, help="Path to the Patrons folder")
        parser.add_argument("--owner", required=True, help="Username owning the patterns")
        parser.add_argument("--no-thumbnails", action="store_true", help="Do not upload thumbnails")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Run the import and roll it back (implies --no-thumbnails)",
        )

    def handle(self, source, owner, no_thumbnails, dry_run, **options):
        try:
            user = get_user_model().objects.get(username=owner)
        except get_user_model().DoesNotExist:
            raise CommandError(f"User {owner!r} does not exist") from None
        if not source.is_dir():
            raise CommandError(f"{source} is not a directory")

        # Uploaded files are not rolled back with the transaction: no thumbnails in dry-run.
        with transaction.atomic():
            report = import_inventory(source, user, with_thumbnails=not (no_thumbnails or dry_run))
            if dry_run:
                transaction.set_rollback(True)

        for warning in report.warnings:
            self.stderr.write(self.style.WARNING(warning))
        prefix = "[dry-run] " if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}patterns: {report.patterns_created} created, "
                f"{report.patterns_updated} updated; brands created: {report.brands_created}; "
                f"size charts: {report.size_charts} ({report.size_chart_rows} rows); "
                f"thumbnails: {report.thumbnails}; warnings: {len(report.warnings)}"
            )
        )
