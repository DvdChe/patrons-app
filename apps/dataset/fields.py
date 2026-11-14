"""The pattern fields audited, how to read them, show them and correct them."""

from dataclasses import dataclass

from apps.catalog import normalize as n
from apps.catalog.models import (
    Audience,
    Brand,
    Difficulty,
    FabricKind,
    Family,
    Pattern,
    SeamAllowance,
    Season,
    Tag,
)

TEXT, CHOICE, BRAND, SEASONS, TAGS, CHECK = "text", "choice", "brand", "seasons", "tags", "check"


@dataclass(frozen=True)
class AuditedField:
    name: str
    label: str
    kind: str
    choices: type | None = None

    def stored(self, p: Pattern):
        """JSON-able value as stored in the inventory."""
        if self.kind == BRAND:
            return p.brand_id
        if self.kind == TAGS:
            return sorted(f"{t.group}|{t.value}" for t in p.tags.all())
        if self.kind == CHECK:
            return _chart_summary(p)
        return getattr(p, self.name)

    def display(self, value) -> str:
        if value in (None, "", []):
            return "—"
        if self.kind == BRAND:
            return Brand.objects.filter(pk=value).values_list("name", flat=True).first() or "?"
        if self.kind == CHOICE:
            return str(self.choices(value).label)
        if self.kind == SEASONS:
            return ", ".join(str(Season(v).label) for v in value)
        if self.kind == TAGS:
            return ", ".join(v.replace("|", " : ") for v in value)
        return str(value)

    def parse(self, post) -> object:
        """Corrected value from the audit form; ValueError when invalid."""
        key = f"value-{self.name}"
        if self.kind == SEASONS:
            values = post.getlist(key)
            if not set(values) <= set(Season.values):
                raise ValueError("saison inconnue")
            return values
        if self.kind == TAGS:
            ids = [int(v) for v in post.getlist(key)]
            tags = Tag.objects.filter(pk__in=ids)
            return sorted(f"{t.group}|{t.value}" for t in tags)
        raw = post.get(key, "").strip()
        if self.kind == BRAND:
            if not raw.isdigit() or not Brand.objects.filter(pk=raw).exists():
                raise ValueError("marque inconnue")
            return int(raw)
        if self.kind == CHOICE:
            if raw not in self.choices.values:
                raise ValueError("valeur inconnue")
            return raw
        return raw[:500]

    def apply(self, p: Pattern, value) -> None:
        if self.kind == BRAND:
            p.brand_id = value
        elif self.kind == TAGS:
            wanted = {tuple(v.split("|", 1)) for v in value}
            p.tags.set([t for t in Tag.objects.all() if (t.group, t.value) in wanted])
        elif self.kind != CHECK:
            setattr(p, self.name, value)
            if self.name == "yardage_raw":  # keep the filter value consistent with the text
                p.yardage_max_m = n.yardage_max(value)


def _chart_summary(p: Pattern) -> str:
    charts = list(p.size_charts.all()) + list(p.brand_size_charts.all())
    body = [c for c in charts if c.measure_type == "body"]
    if not body:
        return "Absent"
    chart = body[0]
    labels = [r.label for r in chart.rows.all()]
    owner = "de la marque" if chart.brand_id else "du patron"
    return f"Tableau {owner} ({labels[0]} à {labels[-1]})" if labels else f"Tableau {owner}"


FIELDS = [
    AuditedField("name", "Modèle", TEXT),
    AuditedField("brand", "Marque", BRAND),
    AuditedField("garment_type", "Type", TEXT),
    AuditedField("family", "Famille", CHOICE, Family),
    AuditedField("audience", "Public", CHOICE, Audience),
    AuditedField("size_range", "Tailles", TEXT),
    AuditedField("difficulty_raw", "Niveau (tel qu’écrit)", TEXT),
    AuditedField("difficulty", "Niveau", CHOICE, Difficulty),
    AuditedField("seam_allowance", "Marges de couture", CHOICE, SeamAllowance),
    AuditedField("seam_allowance_detail", "Marges : largeur", TEXT),
    AuditedField("fabrics_raw", "Tissus conseillés", TEXT),
    AuditedField("fabric_kind", "Nature du tissu", CHOICE, FabricKind),
    AuditedField("yardage_raw", "Métrage (laize 140-150)", TEXT),
    AuditedField("seasons", "Saison", SEASONS),
    AuditedField("tags", "Détails", TAGS),
    AuditedField("size_charts", "Tableau de tailles", CHECK),
]
BY_NAME = {f.name: f for f in FIELDS}
