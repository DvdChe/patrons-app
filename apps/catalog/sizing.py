"""Size recommendation from body measurements.

Ported from the MVP page (`template.html`). Pure functions over `Chart` objects, plus
`recommend()` which reads a pattern's charts from the database.

Vocabulary:
- body chart: body measurements per size; drives the recommendation.
- finished chart: finished garment measurements; used to compute the ease.
- main measures: the measures that decide the size for a garment (chest for a dress...).
"""

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from .models import MEASURES, Family, MeasureType, Pattern, SizeChart

Range = tuple[Decimal, Decimal]  # inclusive bounds; an exact value is (v, v)
Profile = dict[str, Decimal]  # measure name -> body measurement in cm

GRADING_MEASURES = ("chest", "waist", "hip")
EASE_MEASURES = ("chest", "waist", "hip")
UPPER_BODY_FAMILIES = {
    Family.DRESSES,
    Family.TOPS,
    Family.JUMPSUITS,
    Family.OUTERWEAR,
    Family.KNITWEAR,
    Family.SETS,
}
ELASTIC_WAIST_TAGS = {("Taille", "taille élastique"), ("Taille", "taille coulissée")}


@dataclass(frozen=True)
class Chart:
    labels: list[str]
    measures: dict[str, list[Range | None]]  # one entry per label, in size order

    @classmethod
    def from_model(cls, chart: SizeChart) -> "Chart":
        rows = list(chart.rows.all())
        measures = {}
        for name in MEASURES:
            values = [getattr(r, name) for r in rows]
            if any(v is not None for v in values):
                measures[name] = [None if v is None else (v.lower, v.upper) for v in values]
        return cls(labels=[r.label for r in rows], measures=measures)


class Fit(StrEnum):
    EXACT = "exact"  # within one size
    BETWEEN = "between"  # between two consecutive sizes
    OUTSIDE = "outside"  # smaller or larger than every size


@dataclass(frozen=True)
class Location:
    fit: Fit
    index: int | None = None  # size to use; the upper one when between two sizes
    labels: tuple[str, ...] = ()  # one label, or (lower, upper) when between


def locate(values: list[Range | None], labels: list[str], measure: Decimal) -> Location | None:
    """Find where a body measure falls in a chart column (sizes in ascending order)."""
    known = [i for i, v in enumerate(values) if v is not None]
    if not known:
        return None
    if measure < values[known[0]][0]:
        return Location(Fit.OUTSIDE)
    for n, i in enumerate(known):
        low, high = values[i]
        if high >= measure:
            if low <= measure:
                return Location(Fit.EXACT, i, (labels[i],))
            previous = known[n - 1]
            return Location(Fit.BETWEEN, i, (labels[previous], labels[i]))
    return Location(Fit.OUTSIDE)


def main_measures(family: str, tags: set[tuple[str, str]], first_body_chart: Chart) -> list[str]:
    if family == Family.BOTTOMS:
        return ["hip"] if tags & ELASTIC_WAIST_TAGS else ["hip", "waist"]
    if family in UPPER_BODY_FAMILIES:
        return ["chest"]
    return ["chest"] if "chest" in first_body_chart.measures else ["hip"]


@dataclass(frozen=True)
class Evaluation:
    results: dict[str, Location]  # every measure present in both the chart and the profile
    main: dict[str, Location]  # main measures available in the chart and the profile
    needs_grading: bool  # chest, waist and hip point to different sizes
    recommended_index: int | None


def evaluate(chart: Chart, profile: Profile, main: list[str]) -> Evaluation:
    results = {}
    for name in MEASURES:
        if name in chart.measures and profile.get(name) is not None:
            loc = locate(chart.measures[name], chart.labels, profile[name])
            if loc:
                results[name] = loc
    main_results = {k: results[k] for k in main if k in results}
    distinct = {results[k] for k in GRADING_MEASURES if k in results}
    recommended = next((loc.index for loc in main_results.values() if loc.index is not None), None)
    return Evaluation(results, main_results, len(distinct) > 1, recommended)


def fits(body_charts: list[Chart], profile: Profile, main: list[str]) -> bool | None:
    """True if one body chart has every main measure inside it, None when it cannot be told."""
    known = False
    for chart in body_charts:
        ev = evaluate(chart, profile, main)
        if not ev.main:
            continue
        known = True
        if all(loc.fit != Fit.OUTSIDE for loc in ev.main.values()):
            return True
    return False if known else None


def _size_key(label: str) -> str:
    """'38 (M)' and '38' designate the same size: compare the first word only."""
    return label.strip().replace("(", " ").split()[0].lower() if label.strip() else ""


def ease(
    body: Chart, recommended_index: int, finished_charts: list[Chart], profile: Profile
) -> list[tuple[str, str, Decimal]]:
    """(size label, measure, finished - body) for the recommended size, rounded to 0.1 cm."""
    label = body.labels[recommended_index]
    out = []
    for finished in finished_charts:
        keys = [_size_key(x) for x in finished.labels]
        if _size_key(label) not in keys:
            continue
        j = keys.index(_size_key(label))
        for name in EASE_MEASURES:
            values = finished.measures.get(name)
            if values and values[j] is not None and profile.get(name) is not None:
                diff = (values[j][1] - profile[name]).quantize(Decimal("0.1"), ROUND_HALF_UP)
                out.append((label, name, diff))
    return out


@dataclass
class Recommendation:
    body_charts: list[tuple[SizeChart, Evaluation]] = field(default_factory=list)
    finished_charts: list[SizeChart] = field(default_factory=list)
    main_measures: list[str] = field(default_factory=list)
    fits: bool | None = None

    @property
    def primary(self) -> Evaluation | None:
        """Evaluation of the first body chart, shown on the catalogue card."""
        return self.body_charts[0][1] if self.body_charts else None

    @property
    def recommended_label(self) -> str | None:
        if not self.primary or self.primary.recommended_index is None:
            return None
        chart = Chart.from_model(self.body_charts[0][0])
        return chart.labels[self.primary.recommended_index]


def recommend(pattern: Pattern, profile: Profile) -> Recommendation:
    """Evaluate a pattern against a profile. Own charts win; brand charts are the fallback
    (they are only linked to patterns without a body chart of their own)."""
    charts = list(pattern.size_charts.all()) + list(pattern.brand_size_charts.all())
    body = [c for c in charts if c.measure_type == MeasureType.BODY]
    finished = [c for c in charts if c.measure_type == MeasureType.FINISHED]
    rec = Recommendation(finished_charts=finished)
    if not body:
        return rec
    body_charts = [Chart.from_model(c) for c in body]
    tags = {(t.group, t.value) for t in pattern.tags.all()}
    rec.main_measures = main_measures(pattern.family, tags, body_charts[0])
    rec.body_charts = [
        (m, evaluate(c, profile, rec.main_measures)) for m, c in zip(body, body_charts, strict=True)
    ]
    rec.fits = fits(body_charts, profile, rec.main_measures)
    return rec
