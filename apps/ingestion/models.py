from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import Pattern, PatternFile, blob_name


def ingestion_file_path(instance, filename):
    return blob_name(instance.job.owner_id, instance.sha256, filename)


class IngestionJob(models.Model):
    """A pattern being added: uploaded files, the extracted proposal, then the created pattern."""

    class Status(models.TextChoices):
        ANALYSED = "analysed", _("À valider")
        DONE = "done", _("Ajouté")

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    status = models.CharField(max_length=20, choices=Status, default=Status.ANALYSED)
    # Field values and hints proposed by apps.ingestion.pipeline, as JSON (see analysis.py).
    proposal = models.JSONField(default=dict)
    # Pages and images offered as thumbnail candidates: [{"file": id, "page": n | null}, ...]
    thumbnail_candidates = models.JSONField(default=list)
    # Values the user validated, same keys as `proposal`: proposal vs validated is a
    # human-checked example for measuring (and maybe one day training) the extraction.
    validated = models.JSONField(default=dict, blank=True)
    pattern = models.ForeignKey(
        Pattern, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.proposal.get("name") or f"Ajout {self.pk}"

    def corrections(self) -> list[tuple[str, object, object]]:
        """(field, proposed, validated) for every proposed field the user changed."""
        return [
            (k, self.proposal.get(k), v)
            for k, v in self.validated.items()
            if k in self.proposal and _comparable(self.proposal.get(k)) != _comparable(v)
        ]


def _comparable(value):
    if isinstance(value, list):
        return sorted(str(v) for v in value)
    return "" if value in (None, "") else str(value)


class IngestionFile(models.Model):
    """An uploaded file, stored content-addressed like PatternFile (the blob is reused when
    the pattern is created)."""

    job = models.ForeignKey(IngestionJob, on_delete=models.CASCADE, related_name="files")
    file = models.FileField(upload_to=ingestion_file_path, max_length=500)
    original_name = models.CharField(max_length=500)
    kind = models.CharField(max_length=20, choices=PatternFile.Kind)
    sha256 = models.CharField(max_length=64)
    size = models.PositiveBigIntegerField()

    class Meta:
        ordering = ["kind", "original_name"]
        constraints = [
            models.UniqueConstraint(fields=["job", "original_name"], name="ingestionfile_unique"),
        ]

    def __str__(self):
        return self.original_name


class BookletText(models.Model):
    """Text extracted from a set of PDFs, keyed by their content: booklets of known patterns
    are read once to serve as LLM examples."""

    key = models.CharField(max_length=64, unique=True)  # sha256 of the sorted PDF hashes
    text = models.TextField()
    opening = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.key
