"""Build a pattern proposal from its files: what the review form will be pre-filled with."""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal

from apps.catalog import normalize as n
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
from . import llm
from .documents import DocumentText, SourceFile, read_text
from .llm import LlmAnswer


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
    yardage_raw: str = ""  # pre-filled only when the LLM read it (rules: 71 % precision)
    yardage_max_m: Decimal | None = None
    yardage_hint: str = ""  # booklet passage found by the rules, shown under the field
    fabrics_raw: str = ""
    fabric_kind: FabricKind = FabricKind.UNSPECIFIED
    seasons: list[str] = field(default_factory=list)  # always inferred from the wording
    tags: list[tuple[str, str]] = field(default_factory=list)
    llm: LlmAnswer | None = None  # raw LLM answer, when the LLM was used
    llm_fields: set[str] = field(default_factory=set)  # fields filled by the LLM
    document: DocumentText = field(default_factory=DocumentText)


def brand_table() -> list[tuple[int, str, list[str]]]:
    return [(b.pk, b.name, b.aliases) for b in Brand.objects.all()]


def propose(
    files: list[SourceFile],
    brands: list[tuple[int, str, list[str]]],
    brand_rules: dict[int, tuple[str, str]] | None = None,
    use_llm: bool = False,
    llm_model: str | None = None,
    examples_for: Callable[[int], llm.Examples] | None = None,
) -> Proposal:
    """`brand_rules`: brand id -> (seam allowance, detail) used when the booklet is silent.
    `examples_for`: worked examples for the LLM from known patterns of the detected brand."""
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
    yardage_snippet, p.yardage_max_m = x.yardage(text)
    p.yardage_hint = shown(yardage_snippet)
    fabrics_snippet, p.fabric_kind = x.fabrics(text)
    p.fabrics_raw = shown(fabrics_snippet)
    p.seasons = x.seasons(text)
    p.tags = x.tags(text)
    if use_llm:
        examples = examples_for(p.brand_id) if examples_for and p.brand_id else None
        complete_with_llm(p, llm_model, examples, brands)
    # Brand rule last: the booklet (rules, then LLM) wins when it says something.
    if p.seam_allowance == SeamAllowance.UNKNOWN and brand_rules and p.brand_id in brand_rules:
        p.seam_allowance, p.seam_allowance_detail = brand_rules[p.brand_id]
        p.seam_allowance_source = InfoSource.BRAND_RULE
    return p


def complete_with_llm(
    p: Proposal,
    model: str | None = None,
    examples: llm.Examples | None = None,
    brands: list[tuple[int, str, list[str]]] | None = None,
) -> None:
    """Ask the LLM and merge its answers field by field.

    Policy measured with `evaluate_extraction --llm --few-shot` (40 unseen patterns, qwen3.5
    9B, examples of the same brand), precision / recall against the inventory:
    - yardage (LLM 92/63 vs rules 71/53) and seam width (100/79 vs 94/52): the LLM wins;
    - family, size range, difficulty, seam allowance, fabrics: the rules win when they
      answer, the LLM fills what they left empty;
    - brand: rules first (92 % precision), the LLM only suggests a known brand.
    """
    p.llm = llm.ask(p.document.opening, p.document.text, model, examples)
    a = p.llm
    if a is None:
        return

    def fill(name: str, value, empty) -> None:
        if value != empty and getattr(p, name) == empty:
            setattr(p, name, value)
            p.llm_fields.add(name)

    if p.family == Family.UNCLASSIFIED and a.garment_type:
        p.garment_type, p.family = a.garment_type, n.family(a.garment_type)
        p.llm_fields |= {"garment_type", "family"}
    fill("size_range", a.size_range, "")
    if p.difficulty == Difficulty.UNSPECIFIED and a.difficulty != Difficulty.UNSPECIFIED:
        p.difficulty, p.difficulty_raw = a.difficulty, a.difficulty_raw
        p.llm_fields |= {"difficulty", "difficulty_raw"}
    if p.seam_allowance == SeamAllowance.UNKNOWN and a.seam_allowance != SeamAllowance.UNKNOWN:
        p.seam_allowance = a.seam_allowance
        p.seam_allowance_source = InfoSource.BOOKLET
        p.llm_fields.add("seam_allowance")
    if a.seam_width and p.seam_allowance == SeamAllowance.INCLUDED:
        p.seam_allowance_detail = a.seam_width
        p.llm_fields.add("seam_allowance_detail")
    if a.yardage_m is not None:
        p.yardage_max_m = a.yardage_m
        p.yardage_raw = f"{a.yardage_m:.2f} m".replace(".", ",")  # "2,10 m", as in the catalogue
        p.llm_fields |= {"yardage_raw", "yardage_max_m"}
    fill("fabrics_raw", a.fabrics, "")
    fill("fabric_kind", a.fabric_kind, FabricKind.UNSPECIFIED)
    if p.brand_id is None and brands and (pk := x.brand_from_name(a.brand, brands)):
        p.brand_id = pk
        p.new_brand = ""
        p.llm_fields.add("brand")
