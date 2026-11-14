"""Build a pattern proposal from its files: what the review form will be pre-filled with."""

import re
from dataclasses import dataclass, field
from decimal import Decimal

from apps.catalog.models import (
    Audience,
    Brand,
    Difficulty,
    FabricKind,
    Family,
    InfoSource,
    SeamAllowance,
)

from . import extractors as x
from .documents import DocumentText, SourceFile, read_text


@dataclass
class Proposal:
    brand_id: int | None = None
    name: str = ""
    garment_type: str = ""
    family: Family = Family.UNCLASSIFIED
    audience: Audience = Audience.WOMEN
    size_range: str = ""
    difficulty: Difficulty = Difficulty.UNSPECIFIED
    difficulty_raw: str = ""
    seam_allowance: SeamAllowance = SeamAllowance.UNKNOWN
    seam_allowance_detail: str = ""
    seam_allowance_source: InfoSource = InfoSource.UNKNOWN
    yardage_raw: str = ""
    yardage_max_m: Decimal | None = None
    fabrics_raw: str = ""
    fabric_kind: FabricKind = FabricKind.UNSPECIFIED
    document: DocumentText = field(default_factory=DocumentText)


def brand_table() -> list[tuple[int, str, list[str]]]:
    return [(b.pk, b.name, b.aliases) for b in Brand.objects.all()]


def propose(
    files: list[SourceFile],
    brands: list[tuple[int, str, list[str]]],
    brand_rules: dict[int, tuple[str, str]] | None = None,
) -> Proposal:
    """`brand_rules`: brand id -> (seam allowance, detail) used when the booklet is silent."""
    doc = read_text(files)
    text = x.prepare(doc.text)
    first_page = x.prepare(doc.first_page)
    names = [f.name for f in files]
    p = Proposal(document=doc)

    p.brand_id = x.brand(brands, names, text)
    brand_words: set[str] = set()
    for pk, name, aliases in brands:
        if pk == p.brand_id:
            for label in [name, *aliases]:
                brand_words |= set(re.split(r"[^a-z0-9]+", x.prepare(label))) - {""}
    p.name = x.name_candidate(names, brand_words)
    p.garment_type, p.family = x.garment_type(p.name, first_page)
    p.audience = x.audience(x.prepare(" ".join(names)) + " " + first_page[:800])
    p.size_range = x.size_range(text)
    p.difficulty, p.difficulty_raw = x.difficulty(text)
    p.seam_allowance, p.seam_allowance_detail = x.seam_allowance(text)
    if p.seam_allowance != SeamAllowance.UNKNOWN:
        p.seam_allowance_source = InfoSource.BOOKLET
    elif brand_rules and p.brand_id in brand_rules:
        p.seam_allowance, p.seam_allowance_detail = brand_rules[p.brand_id]
        p.seam_allowance_source = InfoSource.BRAND_RULE
    p.yardage_raw, p.yardage_max_m = x.yardage(text)
    p.fabrics_raw, p.fabric_kind = x.fabrics(text)
    return p
