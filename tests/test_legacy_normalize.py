"""Normalization of the legacy CSV inventory values.

Cases come from values observed in the real inventory; expected results match the behaviour
of the MVP generator (`regenerer_moteur.py`) unless stated otherwise.
"""

from decimal import Decimal

import pytest

from apps.catalog.legacy import normalize as n
from apps.catalog.models import (
    Audience,
    BrandConfidence,
    Difficulty,
    FabricKind,
    Family,
    InfoSource,
    MeasureType,
    SeamAllowance,
    Season,
)


@pytest.mark.parametrize(
    ("garment_type", "expected"),
    [
        ("Robe", Family.DRESSES),
        ("Robe / blouse", Family.DRESSES),
        ("Top / robe", Family.DRESSES),
        # Deliberate fix: the MVP classified it as a dress (rule order bug), the procedure
        # document says lingerie.
        ("Robe de chambre", Family.LINGERIE),
        ("Déshabillé", Family.LINGERIE),
        ("Pantalon de pyjama", Family.LINGERIE),
        ("Chemise / veste de pyjama", Family.LINGERIE),
        ("Culotte", Family.LINGERIE),
        ("Body (lingerie)", Family.LINGERIE),
        ("Body", Family.TOPS),
        ("Ensemble", Family.SETS),
        ("Combishort", Family.JUMPSUITS),
        ("Salopette", Family.JUMPSUITS),
        ("Recueil", Family.RESOURCES),
        ("Sac", Family.ACCESSORIES),
        ("Élément de col", Family.ACCESSORIES),
        ("Col", Family.ACCESSORIES),
        ("Veste", Family.OUTERWEAR),
        ("Gilet", Family.OUTERWEAR),
        ("Cardigan", Family.OUTERWEAR),
        ("Pull", Family.KNITWEAR),
        ("Sweat", Family.KNITWEAR),
        ("Pantalon / jean", Family.BOTTOMS),
        ("Jupe-culotte", Family.BOTTOMS),
        ("Short", Family.BOTTOMS),
        ("Blouse", Family.TOPS),
        ("Chemisette", Family.TOPS),
        ("Tunique", Family.TOPS),
        ("Polo", Family.TOPS),
        ("À préciser", Family.UNCLASSIFIED),
        ("", Family.UNCLASSIFIED),
    ],
)
def test_family(garment_type, expected):
    assert n.family(garment_type) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", Difficulty.UNSPECIFIED),
        ("Débutant", Difficulty.BEGINNER),
        ("Intermédiaire", Difficulty.INTERMEDIATE),
        ("Avancé", Difficulty.ADVANCED),
        ("1/5", Difficulty.BEGINNER),
        ("2/5", Difficulty.BEGINNER),
        ("3/5", Difficulty.INTERMEDIATE),
        ("4/5", Difficulty.ADVANCED),
        ("2/4", Difficulty.BEGINNER),
        ("3/4", Difficulty.INTERMEDIATE),
        ("2/3", Difficulty.INTERMEDIATE),
        ("Difficulté 2/4 (cœurs)", Difficulty.BEGINNER),
        ("« Il va falloir se concentrer ! » (3,5/5)", Difficulty.INTERMEDIATE),
        ("« C’est du gâteau ! »", Difficulty.BEGINNER),
        ("« Facile, Émile ! »", Difficulty.BEGINNER),
        ("« Ça vaut le coup ! »", Difficulty.INTERMEDIATE),
        ("« Patience, patience ! »", Difficulty.INTERMEDIATE),
        ("« Difficile mais vaut le coup ! »", Difficulty.ADVANCED),
        ("« Facile mais long ! »", Difficulty.INTERMEDIATE),
        ("Moyen (« Average »)", Difficulty.INTERMEDIATE),
        ("Facile (« Easy »)", Difficulty.BEGINNER),
        ("Débutant confirmé + (« experienced beginner+ »)", Difficulty.BEGINNER),
        ("Débutant / intermédiaire", Difficulty.BEGINNER),
        ("Intermédiaire avancé", Difficulty.INTERMEDIATE),
        ("Ultra-débutant", Difficulty.BEGINNER),
        ("4 points (échelle graphique)", Difficulty.UNSPECIFIED),
        ("4 cœurs (échelle non précisée)", Difficulty.UNSPECIFIED),
        # Phrasings missing from the MVP rules, mapping agreed on 2026-11-14
        ("« Simple comme bonjour ! »", Difficulty.BEGINNER),
        ("« Tranquille Émile ! »", Difficulty.BEGINNER),
        ("« Idéal pour une 1ère fois ! »", Difficulty.BEGINNER),
        ("« Ça va bien se passer »", Difficulty.BEGINNER),
        ("Légères notions de couture exigées", Difficulty.BEGINNER),
        ("« Doucement mais sûrement ! »", Difficulty.INTERMEDIATE),
        ("« Pas froid aux yeux ! »", Difficulty.ADVANCED),
        ("Confirmé", Difficulty.ADVANCED),
    ],
)
def test_difficulty(raw, expected):
    assert n.difficulty(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", FabricKind.UNSPECIFIED),
        ("Viscose, tencel, popeline de coton, double gaze (chaîne et trame)", FabricKind.WOVEN),
        ("Jersey, interlock (maille)", FabricKind.KNIT),
        ("Jersey (maille) ; popeline de coton, viscose, crêpe (chaîne et trame)", FabricKind.BOTH),
        ("Maille stretch (30 à 50 % d’extensibilité)", FabricKind.KNIT),
        ("Lin, coton (non extensible)", FabricKind.WOVEN),
        ("Tissu au choix", FabricKind.UNSPECIFIED),
    ],
)
def test_fabric_kind(raw, expected):
    assert n.fabric_kind(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected", "inferred"),
    [
        ("", [], False),
        ("Été", [Season.SUMMER], False),
        ("Été (déduite)", [Season.SUMMER], True),
        ("Mi-saison (déduite)", [Season.MIDSEASON], True),
        ("Toutes saisons", [Season.ALL], False),
        ("Hiver (déduite)", [Season.WINTER], True),
        ("Automne", [Season.MIDSEASON], False),
        ("Été / mi-saison", [Season.SUMMER, Season.MIDSEASON], False),
    ],
)
def test_seasons(raw, expected, inferred):
    assert n.seasons(raw) == (expected, inferred)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", None),
        ("2,30 m", Decimal("2.30")),
        ("1,90 – 2,70 m", Decimal("2.70")),
        ("1,50 m (toutes tailles ; 1,80 m en laize 110)", Decimal("1.50")),
        ("2,05 – 2,65 m (2,05 m en 34-36 ; 2,65 m du 38 au 56)", Decimal("2.65")),
        ("80 cm", Decimal("0.80")),
        ("Non indiqué", None),
    ],
)
def test_yardage_max(raw, expected):
    assert n.yardage_max(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", (SeamAllowance.UNKNOWN, "")),
        ("Comprises", (SeamAllowance.INCLUDED, "")),
        ("Comprises (1 cm)", (SeamAllowance.INCLUDED, "1 cm")),
        (
            "Comprises (1 cm ; 2 cm aux ourlets)",
            (SeamAllowance.INCLUDED, "1 cm ; 2 cm aux ourlets"),
        ),
        ("Non comprises", (SeamAllowance.NOT_INCLUDED, "")),
    ],
)
def test_seam_allowance(raw, expected):
    assert n.seam_allowance(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", InfoSource.UNKNOWN),
        ("Livret", InfoSource.BOOKLET),
        ("Planches", InfoSource.PIECES),
        ("Règle de la marque (vérifiée sur 3 patrons)", InfoSource.BRAND_RULE),
    ],
)
def test_info_source(raw, expected):
    assert n.info_source(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Femme", Audience.WOMEN),
        ("Homme", Audience.MEN),
        ("Mixte", Audience.UNISEX),
        ("Enfant", Audience.KIDS),
        ("À préciser", Audience.UNSPECIFIED),
        ("—", Audience.UNSPECIFIED),
        ("", Audience.UNSPECIFIED),
    ],
)
def test_audience(raw, expected):
    assert n.audience(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Certaine", BrandConfidence.CERTAIN),
        ("Probable", BrandConfidence.PROBABLE),
        ("À identifier", BrandConfidence.UNKNOWN),
        ("", BrandConfidence.UNKNOWN),
    ],
)
def test_brand_confidence(raw, expected):
    assert n.brand_confidence(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Corps", MeasureType.BODY), ("Vêtement fini", MeasureType.FINISHED)],
)
def test_measure_type(raw, expected):
    assert n.measure_type(raw) == expected


def test_measure_type_rejects_unknown():
    with pytest.raises(ValueError, match="Inconnu"):
        n.measure_type("Inconnu")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", None),
        ("88", (Decimal("88"), Decimal("88"))),
        ("80–84", (Decimal("80"), Decimal("84"))),
        ("80-84", (Decimal("80"), Decimal("84"))),
        ("62,5", (Decimal("62.5"), Decimal("62.5"))),
        ("97,5–101,5", (Decimal("97.5"), Decimal("101.5"))),
    ],
)
def test_measure_range(raw, expected):
    result = n.measure_range(raw)
    if expected is None:
        assert result is None
    else:
        assert (result.lower, result.upper) == expected
        assert result.bounds == "[]"


def test_measure_range_rejects_garbage():
    with pytest.raises(ValueError, match="environ"):
        n.measure_range("environ 80")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", []),
        ("Manches : courtes", [("Manches", "courtes")]),
        (
            "Manches : courtes ; Col / encolure : col chemise",
            [("Manches", "courtes"), ("Col / encolure", "col chemise")],
        ),
    ],
)
def test_tags(raw, expected):
    assert n.tags(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("", False), ("Oui", True), ("oui", True), ("x", True), ("Non", False)],
)
def test_sewn(raw, expected):
    assert n.sewn(raw) == expected
