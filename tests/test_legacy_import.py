"""Import of the legacy CSV inventory. All data here is synthetic (public repository)."""

import csv
import json
from decimal import Decimal

import pytest
from django.core.management import CommandError, call_command

from apps.catalog.legacy.importer import import_inventory
from apps.catalog.models import (
    Brand,
    Difficulty,
    Family,
    InfoSource,
    MeasureType,
    Pattern,
    SeamAllowance,
    SizeChart,
    Tag,
)

INVENTORY_COLUMNS = [
    "Modèle", "Marque", "Type", "Public", "Saison", "Métrage (laize 140-150)",
    "Tissus conseillés", "Tailles", "Niveau", "Cousu ?", "Mes notes", "Marque : fiabilité",
    "Remarques inventaire", "Rangé dans", "Emplacement", "Vignette", "Tableau de tailles",
    "Détails", "Détails : source", "Marges de couture", "Marges : source",
]  # fmt: skip
SIZES_COLUMNS = [
    "Marque", "Patron", "Taille", "Type de mesure", "Poitrine (cm)", "Taille (cm)",
    "Hanches (cm)", "Carrure dos (cm)", "Longueur taille-dos (cm)", "Entrejambe (cm)",
    "Hauteur (stature) (cm)", "Précision", "Réf. inventaire",
]  # fmt: skip


def pattern_row(**values):
    row = dict.fromkeys(INVENTORY_COLUMNS, "")
    row.update(
        {
            "Modèle": "Alpha",
            "Marque": "Atelier Test",
            "Type": "Robe",
            "Public": "Femme",
            "Marque : fiabilité": "Certaine",
            "Emplacement": "Documents/Patrons/Robes/Alpha",
            "Tableau de tailles": "Absent",
        }
    )
    row.update(values)
    return row


def size_row(ref, size, measure_type="Corps", brand="Atelier Test", precision="", **m):
    row = dict.fromkeys(SIZES_COLUMNS, "")
    row.update(
        {
            "Marque": brand,
            "Patron": "Tous" if ref == "Marque" else "x",
            "Taille": size,
            "Type de mesure": measure_type,
            "Précision": precision,
            "Réf. inventaire": ref,
        }
    )
    row.update(m)
    return row


@pytest.fixture
def source(tmp_path):
    """Build a fake `Patrons` folder; returns a writer taking pattern and size rows."""

    def write(patterns, sizes=(), thumbnails=()):
        moteur = tmp_path / "_moteur"
        moteur.mkdir(exist_ok=True)
        (moteur / "taglist.json").write_text(
            json.dumps({"Manches": ["courtes", "longues"], "Fermeture": ["nouée"]}),
            encoding="utf-8",
        )
        for name, columns, rows in [
            ("inventaire-patrons-complet.csv", INVENTORY_COLUMNS, patterns),
            ("tableaux-tailles.csv", SIZES_COLUMNS, sizes),
        ]:
            with open(tmp_path / name, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=columns)
                w.writeheader()
                w.writerows(rows)
        (tmp_path / "_vignettes").mkdir(exist_ok=True)
        for name in thumbnails:
            (tmp_path / "_vignettes" / name).write_bytes(b"\xff\xd8\xff fake jpeg")
        return tmp_path

    return write


@pytest.fixture(autouse=True)
def media_root(settings, tmp_path_factory):
    settings.MEDIA_ROOT = tmp_path_factory.mktemp("media")
    settings.STORAGES = {
        **settings.STORAGES,
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    }


def test_imports_normalized_pattern(user, source):
    path = source(
        [
            pattern_row(
                Niveau="2/5",
                Saison="Été (déduite)",
                **{
                    "Métrage (laize 140-150)": "1,90 – 2,70 m",
                    "Tissus conseillés": "Viscose (chaîne et trame)",
                    "Marges de couture": "Comprises (1 cm)",
                    "Marges : source": "Livret",
                    "Détails": "Manches : courtes ; Fermeture : nouée",
                    "Mes notes": "à refaire en lin",
                    "Cousu ?": "Oui",
                },
            )
        ]
    )

    report = import_inventory(path, user, with_thumbnails=False)

    p = Pattern.objects.get(owner=user, legacy_ref=1)
    assert report.patterns_created == 1
    assert p.name == "Alpha"
    assert p.brand.name == "Atelier Test"
    assert p.family == Family.DRESSES
    assert p.difficulty == Difficulty.BEGINNER
    assert p.difficulty_raw == "2/5"
    assert p.seasons == ["summer"]
    assert p.seasons_inferred is True
    assert p.yardage_max_m == Decimal("2.70")
    assert p.seam_allowance == SeamAllowance.INCLUDED
    assert p.seam_allowance_detail == "1 cm"
    assert p.seam_allowance_source == InfoSource.BOOKLET
    assert p.sewn is True
    assert p.notes == "à refaire en lin"
    assert p.legacy_location == "Documents/Patrons/Robes/Alpha"
    assert {(t.group, t.value) for t in p.tags.all()} == {
        ("Manches", "courtes"),
        ("Fermeture", "nouée"),
    }
    assert "nouee" in p.search_text  # tags are searchable
    assert Tag.objects.count() == 3


def test_import_is_idempotent(user, source):
    path = source([pattern_row(), pattern_row(Modèle="Beta")])
    import_inventory(path, user, with_thumbnails=False)

    path = source([pattern_row(Niveau="Avancé"), pattern_row(Modèle="Beta")])
    report = import_inventory(path, user, with_thumbnails=False)

    assert report.patterns_created == 0
    assert report.patterns_updated == 2
    assert Pattern.objects.count() == 2
    assert Brand.objects.count() == 1
    assert Pattern.objects.get(legacy_ref=1).difficulty == Difficulty.ADVANCED


def test_reuses_existing_brand_case_insensitively(user, source):
    Brand.objects.create(name="ATELIER TEST")
    import_inventory(source([pattern_row()]), user, with_thumbnails=False)
    assert Brand.objects.count() == 1


def test_brand_seam_allowance_rule_comes_from_brand_rule_rows(user, source):
    rule = {"Marges de couture": "Comprises (1 cm)", "Marges : source": "Règle de la marque"}
    path = source([pattern_row(**rule), pattern_row(Modèle="Beta", **rule)])

    import_inventory(path, user, with_thumbnails=False)

    brand = Brand.objects.get()
    assert brand.seam_allowance == SeamAllowance.INCLUDED
    assert brand.seam_allowance_detail == "1 cm"


def test_pattern_size_charts_are_grouped_by_type_and_precision(user, source):
    path = source(
        [pattern_row(**{"Tableau de tailles": "Patron ; Vêtement fini aussi"})],
        [
            size_row("1", "36", **{"Poitrine (cm)": "84", "Hanches (cm)": "92"}),
            size_row("1", "38", **{"Poitrine (cm)": "88–90"}),
            size_row("1", "36", "Vêtement fini", **{"Poitrine (cm)": "96"}),
        ],
    )

    import_inventory(path, user, with_thumbnails=False)

    pattern = Pattern.objects.get()
    body = pattern.size_charts.get(measure_type=MeasureType.BODY)
    assert [r.label for r in body.rows.all()] == ["36", "38"]
    first, second = body.rows.all()
    assert (first.chest.lower, first.hip.lower) == (84, 92)
    assert (second.chest.lower, second.chest.upper) == (88, 90)
    assert second.hip is None
    assert pattern.size_charts.filter(measure_type=MeasureType.FINISHED).count() == 1
    assert pattern.brand_size_charts.count() == 0


def test_brand_chart_applies_when_pattern_has_no_body_chart(user, source):
    path = source(
        [
            pattern_row(**{"Tableau de tailles": "Marque"}),
            pattern_row(Modèle="Beta", **{"Tableau de tailles": "Absent"}),
        ],
        [size_row("Marque", "36", **{"Poitrine (cm)": "84"})],
    )

    import_inventory(path, user, with_thumbnails=False)

    brand_chart = SizeChart.objects.get(brand__name="Atelier Test")
    assert list(Pattern.objects.get(name="Alpha").brand_size_charts.all()) == [brand_chart]
    assert Pattern.objects.get(name="Beta").brand_size_charts.count() == 0


def test_reimport_replaces_size_charts(user, source):
    sizes = [size_row("1", "36", **{"Poitrine (cm)": "84"})]
    path = source([pattern_row(**{"Tableau de tailles": "Patron"})], sizes)
    import_inventory(path, user, with_thumbnails=False)
    import_inventory(path, user, with_thumbnails=False)
    assert SizeChart.objects.count() == 1


def test_reports_values_it_cannot_normalize(user, source):
    path = source(
        [pattern_row(Type="Objet mystère", Niveau="très dur", **{"Détails": "Manches : ailes"})],
        [size_row("99", "36", **{"Poitrine (cm)": "84"})],
    )

    report = import_inventory(path, user, with_thumbnails=False)

    text = "\n".join(report.warnings)
    assert "Objet mystère" in text
    assert "très dur" in text
    assert "Manches : ailes" in text
    assert "99" in text


def test_imports_thumbnails_and_reports_missing_ones(user, source):
    path = source(
        [pattern_row(Vignette="0001-alpha.jpg"), pattern_row(Modèle="Beta", Vignette="nope.jpg")],
        thumbnails=["0001-alpha.jpg"],
    )

    report = import_inventory(path, user)
    import_inventory(path, user)  # second run must not duplicate the file

    alpha = Pattern.objects.get(name="Alpha")
    assert alpha.thumbnail.name == "thumbnails/0001-alpha.jpg"
    assert alpha.thumbnail.read() == b"\xff\xd8\xff fake jpeg"
    assert report.thumbnails == 1
    assert any("nope.jpg" in w for w in report.warnings)


def test_command_dry_run_writes_nothing(user, source, capsys):
    path = source([pattern_row()])
    call_command("import_inventory", str(path), "--owner", user.username, "--dry-run")
    assert Pattern.objects.count() == 0
    assert "1 created" in capsys.readouterr().out


def test_command_requires_existing_owner(source, db):
    with pytest.raises(CommandError, match="nobody"):
        call_command("import_inventory", str(source([pattern_row()])), "--owner", "nobody")
