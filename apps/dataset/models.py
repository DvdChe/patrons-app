"""Human audit of the inventory built by an agent: how reliable is each field?"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import Pattern


class AuditSample(models.Model):
    """A pattern drawn at random for the audit, in audit order."""

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    pattern = models.OneToOneField(Pattern, on_delete=models.CASCADE, related_name="audit")
    rank = models.PositiveIntegerField()
    done = models.BooleanField(default=False)

    class Meta:
        ordering = ["rank"]

    def __str__(self):
        return f"{self.rank}. {self.pattern}"


class AuditRecord(models.Model):
    class Verdict(models.TextChoices):
        OK = "ok", _("Correct")
        WRONG = "wrong", _("Faux")
        UNKNOWN = "unknown", _("Je ne sais pas")

    sample = models.ForeignKey(AuditSample, on_delete=models.CASCADE, related_name="records")
    field = models.CharField(max_length=50)
    verdict = models.CharField(max_length=10, choices=Verdict)
    stored = models.JSONField(null=True)  # value in the inventory before the audit
    corrected = models.JSONField(null=True, blank=True)  # value given by the auditor
    created_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["sample", "field"], name="auditrecord_unique"),
        ]

    def __str__(self):
        return f"{self.sample.pattern} — {self.field}: {self.verdict}"
