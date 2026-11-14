"""Import the MVP inventory (CSV files of the `Patrons` folder) into the database.

Re-runnable: patterns are matched on (owner, legacy_ref), where legacy_ref is the CSV data row
number used as ID by the MVP. Size charts coming from the CSV are replaced on every run.
"""

import csv
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from django.core.files import File
from django.db import transaction

from apps.catalog import normalize as n
from apps.catalog.models import (
    Brand,
    Difficulty,
    Family,
    InfoSource,
    MeasureType,
    Pattern,
    SeamAllowance,
    SizeChart,
    SizeChartRow,
    Tag,
)

INVENTORY_FILE = "inventaire-patrons-complet.csv"
SIZES_FILE = "tableaux-tailles.csv"
TAGLIST_FILE = "_moteur/taglist.json"
THUMBNAILS_DIR = "_vignettes"

MEASURE_COLUMNS = {
    "Poitrine (cm)": "chest",
    "Dessous de poitrine (cm)": "underbust",
    "Taille (cm)": "waist",
    "Hanches (cm)": "hip",
    "Carrure dos (cm)": "back_width",
    "Longueur taille-dos (cm)": "back_waist_length",
    "Entrejambe (cm)": "inseam",
    "Hauteur (stature) (cm)": "height",
}
BRAND_REF = "Marque"  # "Réf. inventaire" value of a brand-wide size chart


@dataclass
class ImportReport:
    patterns_created: int = 0
    patterns_updated: int = 0
    brands_created: int = 0
    size_charts: int = 0
    size_chart_rows: int = 0
    thumbnails: int = 0
    warnings: list[str] = field(default_factory=list)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _where(ref: int, row: dict[str, str]) -> str:
    # The user knows rows by their spreadsheet line: header is line 1, pattern n is line n + 1.
    return f"line {ref + 1} ({row['Modèle']})"


class _Importer:
    def __init__(self, source: Path, owner, with_thumbnails: bool):
        self.source = source
        self.owner = owner
        self.with_thumbnails = with_thumbnails
        self.report = ImportReport()
        self.brands: dict[str, Brand] = {}
        self.tags: dict[tuple[str, str], Tag] = {}
        self.patterns: dict[int, Pattern] = {}

    def run(self) -> ImportReport:
        inventory = _read_csv(self.source / INVENTORY_FILE)
        sizes = _read_csv(self.source / SIZES_FILE)
        self._import_tags()
        for ref, row in enumerate(inventory, start=1):
            self._import_pattern(ref, row)
        self._apply_brand_seam_rules(inventory)
        own_body_charts = self._import_size_charts(sizes)
        self._link_brand_charts(inventory, own_body_charts)
        if self.with_thumbnails:
            self._import_thumbnails(inventory)
        return self.report

    def _import_tags(self) -> None:
        taglist = json.loads((self.source / TAGLIST_FILE).read_text(encoding="utf-8"))
        for group, values in taglist.items():
            for position, value in enumerate(values):
                tag, _ = Tag.objects.update_or_create(
                    group=group, value=value, defaults={"position": position}
                )
                self.tags[(group, value)] = tag

    def _brand(self, name: str) -> Brand:
        key = name.strip().lower()
        if key not in self.brands:
            brand = Brand.objects.filter(name__iexact=name.strip()).first()
            if brand is None:
                brand = Brand.objects.create(name=name.strip())
                self.report.brands_created += 1
            self.brands[key] = brand
        return self.brands[key]

    def _warn(self, message: str) -> None:
        self.report.warnings.append(message)

    def _import_pattern(self, ref: int, row: dict[str, str]) -> None:
        where = _where(ref, row)
        seasons, inferred = n.seasons(row["Saison"])
        seam, seam_detail = n.seam_allowance(row["Marges de couture"])
        values = {
            "name": row["Modèle"].strip(),
            "brand": self._brand(row["Marque"]),
            "brand_confidence": n.brand_confidence(row["Marque : fiabilité"]),
            "garment_type": row["Type"].strip(),
            "family": n.family(row["Type"]),
            "audience": n.audience(row["Public"]),
            "seasons": seasons,
            "seasons_inferred": inferred,
            "yardage_raw": row["Métrage (laize 140-150)"].strip(),
            "yardage_max_m": n.yardage_max(row["Métrage (laize 140-150)"]),
            "fabrics_raw": row["Tissus conseillés"].strip(),
            "fabric_kind": n.fabric_kind(row["Tissus conseillés"]),
            "size_range": row["Tailles"].strip(),
            "difficulty_raw": row["Niveau"].strip(),
            "difficulty": n.difficulty(row["Niveau"]),
            "seam_allowance": seam,
            "seam_allowance_detail": seam_detail,
            "seam_allowance_source": n.info_source(row["Marges : source"]),
            "tags_source": row["Détails : source"].strip(),
            "sewn": n.sewn(row["Cousu ?"]),
            "notes": row["Mes notes"].strip(),
            "remarks": row["Remarques inventaire"].strip(),
            "legacy_location": row["Emplacement"].strip(),
        }
        self._check_normalization(where, row, values)

        pattern, created = Pattern.objects.update_or_create(
            owner=self.owner, legacy_ref=ref, defaults=values
        )
        if created:
            self.report.patterns_created += 1
        else:
            self.report.patterns_updated += 1

        tags = []
        for group, value in n.tags(row["Détails"]):
            tag = self.tags.get((group, value))
            if tag is None:
                self._warn(f"{where}: unknown detail tag '{group} : {value}'")
            else:
                tags.append(tag)
        pattern.tags.set(tags)
        pattern.save(update_fields=["search_text"])  # index the tags
        self.patterns[ref] = pattern

    def _check_normalization(self, where: str, row: dict[str, str], values: dict) -> None:
        """Report raw values that are present but could not be normalized."""
        checks = [
            ("Type", values["family"] == Family.UNCLASSIFIED),
            ("Niveau", values["difficulty"] == Difficulty.UNSPECIFIED),
            ("Métrage (laize 140-150)", values["yardage_max_m"] is None),
            ("Saison", not values["seasons"]),
            ("Marges de couture", values["seam_allowance"] == SeamAllowance.UNKNOWN),
            ("Marges : source", values["seam_allowance_source"] == InfoSource.UNKNOWN),
        ]
        for column, failed in checks:
            if failed and row[column].strip():
                self._warn(f"{where}: unrecognized {column!r} value {row[column]!r}")

    def _apply_brand_seam_rules(self, inventory: list[dict[str, str]]) -> None:
        """A brand's rule is the value shared by its patterns sourced from the brand rule."""
        rules: dict[str, set[tuple[str, str]]] = defaultdict(set)
        for row in inventory:
            if n.info_source(row["Marges : source"]) == InfoSource.BRAND_RULE:
                rules[row["Marque"].strip().lower()].add(n.seam_allowance(row["Marges de couture"]))
        for key, values in rules.items():
            brand = self.brands[key]
            if len(values) > 1:
                self._warn(f"brand {brand.name!r}: conflicting seam allowance rules {values}")
                continue
            brand.seam_allowance, brand.seam_allowance_detail = values.pop()
            brand.save(update_fields=["seam_allowance", "seam_allowance_detail"])

    def _import_size_charts(self, sizes: list[dict[str, str]]) -> set[int]:
        """Create the size charts; returns the refs of patterns that have their own body chart.

        Rows with the same (ref, measure type, precision) form one chart, in file order.
        """
        groups: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
        for row in sizes:
            ref = row["Réf. inventaire"].strip()
            owner_key = f"brand:{row['Marque'].strip().lower()}" if ref == BRAND_REF else ref
            groups[(owner_key, row["Type de mesure"].strip(), row["Précision"])].append(row)

        brands_in_file = {
            self._brand(r["Marque"]) for r in sizes if r["Réf. inventaire"] == "Marque"
        }
        SizeChart.objects.filter(brand__in=brands_in_file).delete()
        SizeChart.objects.filter(pattern__in=self.patterns.values()).delete()

        own_body: set[int] = set()
        for (owner_key, measure_type_raw, precision), rows in groups.items():
            target = {}
            if owner_key.startswith("brand:"):
                target["brand"] = self._brand(rows[0]["Marque"])
            else:
                pattern = self.patterns.get(int(owner_key)) if owner_key.isdigit() else None
                if pattern is None:
                    self._warn(f"size chart for unknown inventory ref {owner_key!r}: skipped")
                    continue
                target["pattern"] = pattern
            try:
                measure_type = n.measure_type(measure_type_raw)
            except ValueError as e:
                self._warn(f"size chart {owner_key!r}: {e}: skipped")
                continue

            chart = SizeChart.objects.create(measure_type=measure_type, note=precision, **target)
            self.report.size_charts += 1
            if measure_type == MeasureType.BODY and "pattern" in target:
                own_body.add(int(owner_key))
            for position, row in enumerate(rows):
                self._create_chart_row(chart, position, row, owner_key)
        return own_body

    def _create_chart_row(self, chart: SizeChart, position: int, row: dict, owner_key: str):
        measures = {}
        for column, field_name in MEASURE_COLUMNS.items():
            try:
                measures[field_name] = n.measure_range(row.get(column) or "")
            except ValueError as e:
                self._warn(f"size chart {owner_key!r}, size {row['Taille']!r}: {e}")
        SizeChartRow.objects.create(
            chart=chart, position=position, label=row["Taille"].strip(), **measures
        )
        self.report.size_chart_rows += 1

    def _link_brand_charts(self, inventory: list[dict[str, str]], own_body: set[int]) -> None:
        """Same rule as the MVP: brand charts apply to a pattern flagged "Marque" that has no
        body chart of its own."""
        for ref, row in enumerate(inventory, start=1):
            pattern = self.patterns[ref]
            if not row["Tableau de tailles"].startswith("Marque") or ref in own_body:
                pattern.brand_size_charts.clear()
                continue
            charts = list(pattern.brand.size_charts.all())
            if not charts:
                self._warn(f"{_where(ref, row)}: flagged with a brand size chart, none found")
            pattern.brand_size_charts.set(charts)

    def _import_thumbnails(self, inventory: list[dict[str, str]]) -> None:
        for ref, row in enumerate(inventory, start=1):
            name = row["Vignette"].strip()
            pattern = self.patterns[ref]
            if not name or pattern.thumbnail.name == f"thumbnails/{name}":
                continue
            path = self.source / THUMBNAILS_DIR / name
            if not path.is_file():
                self._warn(f"{_where(ref, row)}: thumbnail file not found: {name}")
                continue
            with open(path, "rb") as f:
                pattern.thumbnail.save(name, File(f), save=True)
            self.report.thumbnails += 1


def import_inventory(source: Path, owner, *, with_thumbnails: bool = True) -> ImportReport:
    with transaction.atomic():
        return _Importer(Path(source), owner, with_thumbnails).run()
