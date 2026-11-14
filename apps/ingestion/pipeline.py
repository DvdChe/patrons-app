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
    new_brand: str = ""  # brand named in the files but unknown: suggested for creation
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
    seasons: list[str] = field(default_factory=list)  # always inferred from the wording
    tags: list[tuple[str, str]] = field(default_factory=list)
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
    text, index = x.prepare_map(doc.text)
    opening = x.prepare(doc.opening)
    names = [f.name for f in files]
    p = Proposal(document=doc)

    def shown(snippet: str) -> str:
        """Snippet as written in the booklet (accents, capitals)."""
        restored = x.original(doc.text, text, index, snippet)
        return restored[:1].upper() + restored[1:]

    p.brand_id = x.brand(brands, names, text)
    if p.brand_id is None and (holder := x.copyright_holder(doc.text)):
        known = {name.lower(): pk for pk, name, _ in brands}
        p.brand_id = known.get(holder.lower())
        p.new_brand = "" if p.brand_id else holder
    brand_words: set[str] = set()
    for pk, name, aliases in brands:
        if pk == p.brand_id:
            for label in [name, *aliases]:
                brand_words |= set(re.split(r"[^a-z0-9]+", x.prepare(label))) - {""}

    # File names are the best source (91-96 % in evaluate_extraction); the booklet text
    # names the pattern when the files have generic names ("Livret.pdf", "A0.pdf").
    p.name = x.name_candidate(names, brand_words) or x.name_from_text(doc.text, brand_words)
    p.garment_type, p.family = x.garment_type(p.name, opening)
    p.audience = x.audience(x.prepare(" ".join(names)) + " " + opening[:800])
    p.size_range = x.size_range(text)
    p.difficulty, p.difficulty_raw = x.difficulty(text)
    if p.difficulty_raw.startswith("« "):
        p.difficulty_raw = f"« {shown(x.prepare(p.difficulty_raw[2:-2]))} »"
    p.seam_allowance, p.seam_allowance_detail = x.seam_allowance(text)
    if p.seam_allowance != SeamAllowance.UNKNOWN:
        p.seam_allowance_source = InfoSource.BOOKLET
    elif brand_rules and p.brand_id in brand_rules:
        p.seam_allowance, p.seam_allowance_detail = brand_rules[p.brand_id]
        p.seam_allowance_source = InfoSource.BRAND_RULE
    yardage_snippet, p.yardage_max_m = x.yardage(text)
    p.yardage_raw = shown(yardage_snippet)
    fabrics_snippet, p.fabric_kind = x.fabrics(text)
    p.fabrics_raw = shown(fabrics_snippet)
    p.seasons = x.seasons(text)
    p.tags = x.tags(text)
    return p
