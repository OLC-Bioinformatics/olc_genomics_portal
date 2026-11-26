"""Report or apply the configured local media retention policy."""

from django.conf import settings
from django.core.management.base import BaseCommand

from olc_webportalv2.common.media_retention import clean_local_media


class Command(BaseCommand):
    help = (
        "Report stale per-run local media; use --apply only after reviewing "
        "the report."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Delete stale local media files without modifying blob storage.",
        )
        parser.add_argument(
            "--report-csv",
            help="Write the per-file decisions to this CSV path.",
        )
    def handle(self, *args, **options):
        report = clean_local_media(
            dry_run=not options["apply"],
            retention_days=settings.MEDIA_RETENTION_DAYS,
            report_csv=options["report_csv"],
        )
        mode = "Applied" if options["apply"] else "Dry run"
        self.stdout.write(f"{mode}: {report.summary()}")
        if options["report_csv"]:
            self.stdout.write(f"Detailed report: {options['report_csv']}")
