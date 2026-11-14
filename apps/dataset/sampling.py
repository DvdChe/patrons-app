import random

from apps.catalog.models import Family, Pattern

from .models import AuditSample


def create_sample(owner, size: int, seed: int | None = None) -> list[AuditSample]:
    """Draw `size` imported patterns not sampled yet (resources such as manuals excluded)."""
    candidates = list(
        Pattern.objects.filter(owner=owner, legacy_ref__isnull=False, audit__isnull=True)
        .exclude(family=Family.RESOURCES)
        .values_list("pk", flat=True)
    )
    random.Random(seed).shuffle(candidates)
    start = AuditSample.objects.filter(owner=owner).count()
    return AuditSample.objects.bulk_create(
        AuditSample(owner=owner, pattern_id=pk, rank=start + i + 1)
        for i, pk in enumerate(candidates[:size])
    )
