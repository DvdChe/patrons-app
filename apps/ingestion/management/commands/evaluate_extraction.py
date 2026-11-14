"""Measure the extractors against the hand-filled MVP inventory.

Runs the ingestion pipeline on the original files of a sample of imported patterns (read from
the MVP folder, nothing is uploaded) and compares each proposed field with the stored value.
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
from apps.catalog.models import (
    Brand,
    Difficulty,
    FabricKind,
    Family,
    Pattern,
    SeamAllowance,
)
from apps.catalog.text import normalize
from apps.ingestion.documents import SourceFile
from apps.ingestion.pipeline import brand_table, propose

MAX_FILE_BYTES = 80 * 1024 * 1024


def _compact(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", normalize(re.sub(r"\(.*?\)", " ", s)))


def _size_bounds(s: str) -> tuple[str, ...]:
    tokens = re.findall(r"\d{2}|xxxl|xxl|xl|xxs|xs|\bs\b|\bm\b|\bl\b|unique", normalize(s))
    return tuple(tokens[:2])


def _width(s: str) -> str:
    m = re.search(r"\d+(?:[.,]\d+)?", s)
    return m.group(0).replace(".", ",") if m else ""


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
    "seasons": (
        lambda t, p: bool(t.seasons),
        lambda t, p: bool(p.seasons),
        lambda t, p: set(p.seasons) == set(t.seasons),
    ),
}


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument("source", type=Path, help="Path to the MVP Patrons folder")
        parser.add_argument("--owner", required=True)
        parser.add_argument("--sample", type=int, default=60)
        parser.add_argument("--seed", type=int, default=1)
        parser.add_argument("--errors", help="Print the mismatches of this field")
        parser.add_argument(
            "--missed", action="store_true", help="With --errors: also print values not proposed"
        )

    def handle(self, source, owner, sample, seed, errors, missed, **options):
        try:
            user = get_user_model().objects.get(username=owner)
        except get_user_model().DoesNotExist:
            raise CommandError(f"User {owner!r} does not exist") from None
        patterns = list(
            Pattern.objects.filter(owner=user, legacy_ref__isnull=False)
            .exclude(family=Family.RESOURCES)
            .select_related("brand")
        )
        random.Random(seed).shuffle(patterns)
        brands = brand_table()
        rules = {
            b.pk: (b.seam_allowance, b.seam_allowance_detail)
            for b in Brand.objects.exclude(seam_allowance=SeamAllowance.UNKNOWN)
        }
        shared = defaultdict(int)
        for p in Pattern.objects.filter(owner=user):
            shared[p.legacy_location] += 1

        stats = defaultdict(lambda: [0, 0, 0])  # known, proposed, correct
        tag_stats = defaultdict(lambda: [0, 0])  # tag -> [proposed, correct]
        tag_truth = 0
        mismatches = []
        done = ocr_pages = 0
        started = time.monotonic()
        for pattern in patterns:
            if done >= sample:
                break
            files = self._files(source, pattern, shared[pattern.legacy_location] > 1)
            if not files:
                continue
            proposal = propose(files, brands, rules)
            done += 1
            ocr_pages += proposal.document.ocr_pages
            if pattern.tags_source:  # tags were filled for this pattern
                truth = {(t.group, t.value) for t in pattern.tags.all()}
                tag_truth += len(truth)
                for tag in proposal.tags:
                    tag_stats[tag][0] += 1
                    tag_stats[tag][1] += tag in truth
            for field, (known, proposed, correct) in CHECKS.items():
                if not known(pattern, proposal):
                    continue
                stats[field][0] += 1
                if proposed(pattern, proposal):
                    stats[field][1] += 1
                    if correct(pattern, proposal):
                        stats[field][2] += 1
                    elif field == errors:
                        mismatches.append(("wrong", pattern, proposal))
                elif field == errors and missed:
                    mismatches.append(("missed", pattern, proposal))

        elapsed = time.monotonic() - started
        per_pattern = elapsed / max(done, 1)
        self.stdout.write(
            f"{done} patterns, {ocr_pages} OCR pages, {elapsed:.0f} s ({per_pattern:.1f} s each)\n"
        )
        header = ("field", "known", "proposed", "correct", "precision", "recall")
        self.stdout.write("{:22} {:>6} {:>9} {:>8} {:>10} {:>7}".format(*header))
        for field in CHECKS:
            known, proposed, correct = stats[field]
            precision = f"{correct / proposed:.0%}" if proposed else "-"
            recall = f"{correct / known:.0%}" if known else "-"
            self.stdout.write(
                f"{field:22} {known:6} {proposed:9} {correct:8} {precision:>10} {recall:>7}"
            )
        proposed = sum(v[0] for v in tag_stats.values())
        correct = sum(v[1] for v in tag_stats.values())
        precision = correct / proposed if proposed else 0
        recall = correct / tag_truth if tag_truth else 0
        self.stdout.write(
            f"{'tags (each)':22} {tag_truth:6} {proposed:9} {correct:8} "
            f"{precision:>10.0%} {recall:>7.0%}"
        )
        if errors == "tags":
            for (group, value), (n_prop, n_ok) in sorted(
                tag_stats.items(), key=lambda kv: kv[1][1] / kv[1][0]
            ):
                self.stdout.write(f"   {n_ok:3}/{n_prop:3} {group} : {value}")
        for kind, pattern, proposal in mismatches:
            self.stdout.write(
                f"\n✗ {kind}: {pattern.name} ({pattern.brand.name}) — {pattern.legacy_location}"
            )
            self.stdout.write(f"   stored:   {self._value(errors, pattern)}")
            self.stdout.write(f"   proposed: {self._value(errors, proposal)}")

    @staticmethod
    def _value(field, obj):
        attr = {
            "brand": "brand_id", "name": "name", "family": "family", "audience": "audience",
            "size_range": "size_range", "difficulty": "difficulty",
            "seam (booklet only)": "seam_allowance", "seam (+ brand rule)": "seam_allowance",
            "seam width": "seam_allowance_detail", "yardage max": "yardage_max_m",
            "fabric kind": "fabric_kind",
        }[field]  # fmt: skip
        extra = getattr(obj, "difficulty_raw", "") if field == "difficulty" else ""
        return f"{getattr(obj, attr)!r} {extra}"

    @staticmethod
    def _files(source: Path, pattern: Pattern, shared: bool) -> list[SourceFile]:
        path = resolve(source, pattern.legacy_location)
        if path is None:
            return []
        out = []
        with entries(path) as found:
            if shared:
                found = [e for e in found if matches_pattern(pattern.name, e.name)]
            # The upload name (zip or folder) comes first, as when a user uploads a zip.
            if not shared:
                out.append(SourceFile(PurePosixPath(path.name).name, b""))
            for e in found:
                if e.name.lower().endswith(".pdf") and e.size <= MAX_FILE_BYTES:
                    with e.open() as fh:
                        out.append(SourceFile(e.name, fh.read()))
                else:
                    out.append(SourceFile(e.name, b""))
        return out
