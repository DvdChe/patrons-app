"""Measure the extractors against the MVP inventory (filled by an agent: a silver reference).

Runs the ingestion pipeline on the original files of a sample of imported patterns (read from
the MVP folder, nothing is uploaded) and compares each proposed field with the stored value.
With --llm, the LLM field answers are measured on their own ("LLM ..." rows); --few-shot
adds worked examples from other patterns of the same brand (never the evaluated pattern).
"""

import random
import re
import time
from collections import defaultdict
from decimal import Decimal
from pathlib import Path, PurePosixPath

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.catalog.legacy.files import entries, matches_pattern, resolve
from apps.catalog.models import Brand, Difficulty, FabricKind, Family, Pattern, SeamAllowance
from apps.catalog.normalize import family as normalize_family
from apps.catalog.text import normalize
from apps.ingestion import extractors as x
from apps.ingestion.documents import DocumentText, SourceFile, read_text
from apps.ingestion.llm import TASKS
from apps.ingestion.pipeline import brand_table, propose

MAX_FILE_BYTES = 80 * 1024 * 1024
EXAMPLES_PER_TASK = 2


def _compact(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", normalize(re.sub(r"\(.*?\)", " ", s)))


def _size_bounds(s: str) -> tuple[str, ...]:
    tokens = re.findall(r"\d{2}|xxxl|xxl|xl|xxs|xs|\bs\b|\bm\b|\bl\b|unique", normalize(s))
    return tuple(tokens[:2])


def _width(s: str) -> str:
    m = re.search(r"\d+(?:[.,]\d+)?", s or "")
    return m.group(0).replace(".", ",").rstrip("0").rstrip(",") if m else ""


def _always(t, p):
    return True


def _same(attr: str, unknown):
    """Check of a field where `unknown` means "not filled", in the inventory and the proposal."""
    return (
        lambda t, p: getattr(t, attr) != unknown,
        lambda t, p: getattr(p, attr) != unknown,
        lambda t, p: getattr(p, attr) == getattr(t, attr),
    )


def _name_ok(t, p):
    a, b = _compact(t.name), _compact(p.name)
    return bool(a and b) and (a in b or b in a)


def _llm(check):
    """An LLM check only applies when the LLM answered."""
    known, proposed, correct = check
    return (lambda t, p: p.llm is not None and known(t, p), proposed, correct)


def _llm_same(attr: str, truth_attr: str, unknown):
    return _llm(
        (
            lambda t, p: getattr(t, truth_attr) != unknown,
            lambda t, p: getattr(p.llm, attr) != unknown,
            lambda t, p: getattr(p.llm, attr) == getattr(t, truth_attr),
        )
    )


# field -> (is the stored value known?, is a value proposed?, is the proposal correct?)
CHECKS = {
    "brand": (_always, lambda t, p: p.brand_id is not None, lambda t, p: p.brand_id == t.brand_id),
    "name": (_always, lambda t, p: bool(p.name), _name_ok),
    "family": _same("family", Family.UNCLASSIFIED),
    "audience": _same("audience", "unspecified"),
    "size_range": (
        lambda t, p: bool(_size_bounds(t.size_range)),
        lambda t, p: bool(p.size_range),
        lambda t, p: _size_bounds(p.size_range) == _size_bounds(t.size_range),
    ),
    "difficulty": _same("difficulty", Difficulty.UNSPECIFIED),
    "seam (booklet only)": (
        lambda t, p: t.seam_allowance != SeamAllowance.UNKNOWN,
        lambda t, p: p.seam_allowance_source == "booklet",
        lambda t, p: p.seam_allowance == t.seam_allowance,
    ),
    "seam (+ brand rule)": _same("seam_allowance", SeamAllowance.UNKNOWN),
    "seam width": (
        lambda t, p: bool(_width(t.seam_allowance_detail)),
        lambda t, p: bool(p.seam_allowance_detail),
        lambda t, p: _width(p.seam_allowance_detail) == _width(t.seam_allowance_detail),
    ),
    "yardage max": (
        lambda t, p: t.yardage_max_m is not None,
        lambda t, p: p.yardage_max_m is not None,
        lambda t, p: abs(p.yardage_max_m - t.yardage_max_m) <= Decimal("0.1"),
    ),
    "fabric kind": _same("fabric_kind", FabricKind.UNSPECIFIED),
    # LLM answers alone (with --llm)
    "LLM brand": _llm(
        (
            _always,
            lambda t, p: bool(p.llm.brand),
            lambda t, p: x.brand_from_name(p.llm.brand, BRANDS) == t.brand_id,
        )
    ),
    "LLM family": _llm(
        (
            lambda t, p: t.family != Family.UNCLASSIFIED,
            lambda t, p: bool(p.llm.garment_type),
            lambda t, p: normalize_family(p.llm.garment_type) == t.family,
        )
    ),
    "LLM size_range": _llm(
        (
            lambda t, p: bool(_size_bounds(t.size_range)),
            lambda t, p: bool(p.llm.size_range),
            lambda t, p: _size_bounds(p.llm.size_range) == _size_bounds(t.size_range),
        )
    ),
    "LLM difficulty": _llm_same("difficulty", "difficulty", Difficulty.UNSPECIFIED),
    "LLM seam": _llm_same("seam_allowance", "seam_allowance", SeamAllowance.UNKNOWN),
    "LLM seam width": _llm(
        (
            lambda t, p: bool(_width(t.seam_allowance_detail)),
            lambda t, p: bool(p.llm.seam_width),
            lambda t, p: _width(p.llm.seam_width) == _width(t.seam_allowance_detail),
        )
    ),
    "LLM yardage max": _llm(
        (
            lambda t, p: t.yardage_max_m is not None,
            lambda t, p: p.llm.yardage_m is not None,
            lambda t, p: abs(p.llm.yardage_m - t.yardage_max_m) <= Decimal("0.1"),
        )
    ),
    "LLM fabric kind": _llm_same("fabric_kind", "fabric_kind", FabricKind.UNSPECIFIED),
}
BRANDS: list = []  # brand table, filled by the command (used by the "LLM brand" check)

VALUE_OF = {
    "brand": "brand_id", "name": "name", "family": "family", "audience": "audience",
    "size_range": "size_range", "difficulty": "difficulty", "seam (booklet only)": "seam_allowance",
    "seam (+ brand rule)": "seam_allowance", "seam width": "seam_allowance_detail",
    "yardage max": "yardage_max_m", "fabric kind": "fabric_kind",
}  # fmt: skip


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument("source", type=Path, help="Path to the MVP Patrons folder")
        parser.add_argument("--owner", required=True)
        parser.add_argument("--sample", type=int, default=60)
        parser.add_argument("--seed", type=int, default=1)
        parser.add_argument("--errors", help="Print the mismatches of this field")
        parser.add_argument("--missed", action="store_true", help="With --errors: also misses")
        parser.add_argument("--llm", action="store_true", help="Also ask the LLM (LLM_URL)")
        parser.add_argument("--model", help="LLM model (default: LLM_MODEL)")
        parser.add_argument("--few-shot", action="store_true", help="LLM examples, same brand")

    def handle(self, source, owner, sample, seed, errors, missed, llm, model, few_shot, **o):
        try:
            user = get_user_model().objects.get(username=owner)
        except get_user_model().DoesNotExist:
            raise CommandError(f"User {owner!r} does not exist") from None
        self.source = source
        self.texts: dict[int, DocumentText | None] = {}
        all_patterns = list(
            Pattern.objects.filter(owner=user, legacy_ref__isnull=False).select_related("brand")
        )
        self.shared = defaultdict(int)
        for p in all_patterns:
            self.shared[p.legacy_location] += 1
        patterns = [p for p in all_patterns if p.family != Family.RESOURCES]
        patterns.sort(key=lambda p: p.legacy_ref)
        random.Random(seed).shuffle(patterns)
        BRANDS[:] = brand_table()
        rules = {
            b.pk: (b.seam_allowance, b.seam_allowance_detail)
            for b in Brand.objects.exclude(seam_allowance=SeamAllowance.UNKNOWN)
        }

        stats = defaultdict(lambda: [0, 0, 0])  # known, proposed, correct
        mismatches = []
        done = ocr_pages = 0
        llm_seconds = 0.0
        started = time.monotonic()
        for pattern in patterns:
            if done >= sample:
                break
            files = self._files(pattern)
            if not files:
                continue
            examples = self._examples(pattern, all_patterns) if llm and few_shot else None
            proposal = propose(
                files, BRANDS, rules, use_llm=llm, llm_model=model, llm_examples=examples
            )
            if llm and proposal.llm is None:
                raise CommandError("The LLM stopped answering: results would be misleading.")
            done += 1
            ocr_pages += proposal.document.ocr_pages
            llm_seconds += proposal.llm.seconds if proposal.llm else 0
            for name, (known, proposed, correct) in CHECKS.items():
                if not known(pattern, proposal):
                    continue
                stats[name][0] += 1
                if proposed(pattern, proposal):
                    stats[name][1] += 1
                    if correct(pattern, proposal):
                        stats[name][2] += 1
                    elif name == errors:
                        mismatches.append(("wrong", pattern, proposal))
                elif name == errors and missed:
                    mismatches.append(("missed", pattern, proposal))

        elapsed = time.monotonic() - started
        self.stdout.write(
            f"{done} patterns, {ocr_pages} OCR pages, {elapsed:.0f} s "
            f"(LLM {llm_seconds / max(done, 1):.1f} s per pattern)\n"
        )
        header = ("field", "known", "proposed", "correct", "precision", "recall")
        self.stdout.write("{:22} {:>6} {:>9} {:>8} {:>10} {:>7}".format(*header))
        for name in CHECKS:
            known, proposed, correct = stats[name]
            if not known:
                continue
            precision = f"{correct / proposed:.0%}" if proposed else "-"
            recall = f"{correct / known:.0%}" if known else "-"
            self.stdout.write(
                f"{name:22} {known:6} {proposed:9} {correct:8} {precision:>10} {recall:>7}"
            )
        for kind, pattern, proposal in mismatches:
            self.stdout.write(
                f"\n✗ {kind}: {pattern.name} ({pattern.brand.name}) — {pattern.legacy_location}"
            )
            attr = VALUE_OF.get(errors)
            stored = getattr(pattern, attr) if attr else ""
            proposed = getattr(proposal, attr) if attr else proposal.llm
            self.stdout.write(f"   stored:   {stored!r}\n   proposed: {proposed!r}")

    def _files(self, pattern: Pattern) -> list[SourceFile]:
        path = resolve(self.source, pattern.legacy_location)
        if path is None:
            return []
        shared = self.shared[pattern.legacy_location] > 1
        out = []
        with entries(path) as found:
            if shared:
                found = [e for e in found if matches_pattern(pattern.name, e.name)]
            else:  # the upload name (zip or folder) comes first, as for a user upload
                out.append(SourceFile(PurePosixPath(path.name).name, b""))
            for e in found:
                if e.name.lower().endswith(".pdf") and e.size <= MAX_FILE_BYTES:
                    with e.open() as fh:
                        out.append(SourceFile(e.name, fh.read()))
                else:
                    out.append(SourceFile(e.name, b""))
        return out

    def _text(self, pattern: Pattern) -> DocumentText | None:
        if pattern.pk not in self.texts:
            files = self._files(pattern)
            self.texts[pattern.pk] = read_text(files) if files else None
        return self.texts[pattern.pk]

    def _examples(self, pattern: Pattern, all_patterns: list[Pattern]) -> dict:
        """Up to two worked examples per task from other patterns of the same brand."""
        same_brand = [
            p for p in all_patterns if p.brand_id == pattern.brand_id and p.pk != pattern.pk
        ]
        random.Random(pattern.pk).shuffle(same_brand)
        examples: dict[str, list] = {}
        for task in TASKS:
            for other in same_brand:
                if len(examples.get(task.name, [])) >= EXAMPLES_PER_TASK:
                    break
                answer = task.example(other)
                doc = self._text(other) if answer else None
                passage = task.source(doc.text, doc.opening).strip() if doc else ""
                if passage:
                    examples.setdefault(task.name, []).append((passage, answer))
        return examples
