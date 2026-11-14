"""Field extractors of the pattern ingestion. Phrasings mimic layouts seen in real booklets
(synthetic text: no booklet content is copied)."""

from decimal import Decimal as D

import pytest

from apps.catalog.models import Audience, Difficulty, FabricKind, Family, SeamAllowance
from apps.ingestion import extractors as x


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Taille / Size : 34-46", "34 – 46"),
        ("TAILLES\ndu 34 au 46\nDIFFICULTÉ", "34 – 46"),
        ("Taille : du 34 au 48", "34 – 48"),
        ("Disponible du T32 au T52", "32 – 52"),
        ("Sizes 36 to 50 included", "36 – 50"),
        ("Tailles XS - XL", "XS – XL"),
        ("il est proposé en taille unique", "Taille unique"),
        # Pairs from a measurement table are not the size range; the table header is.
        ("Taille 34/36 38/40 tour de poitrine", ""),
        ("Taille 34 36 38 40 42 44 46 48 Poitrine 80 84", "34 – 48"),
        ("Tailles du 34 au 48. Tableau : taille 34 - 36", "34 – 48"),
        ("Coudre à 1 cm, taille 38 conseillée", ""),
        ("taille 12 - 14 ans", ""),  # kids ages are not garment sizes
        ("", ""),
    ],
)
def test_size_range(text, expected):
    assert x.size_range(x.prepare(text)) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Niveau couture : intermédiaire", Difficulty.INTERMEDIATE),
        ("Simple comme bonjour NIVEAU / LEVEL :\nLuisa", Difficulty.BEGINNER),
        ("NIVEAU / LEV\nEL : ça vaut le coup !", Difficulty.INTERMEDIATE),
        ("Difficulté : 3/5", Difficulty.INTERMEDIATE),
        ("Skill level: beginner", Difficulty.BEGINNER),
        ("Surfiler au niveau des épaules puis repasser.", Difficulty.UNSPECIFIED),
        ("Rien à voir ici", Difficulty.UNSPECIFIED),
    ],
)
def test_difficulty(text, expected):
    assert x.difficulty(x.prepare(text))[0] == expected


@pytest.mark.parametrize(
    ("text", "status", "detail"),
    [
        ("Marges de couture de 1cm incluses.", SeamAllowance.INCLUDED, "1 cm"),
        (
            "Les marges de couture sont comprises : 1 cm pour les coutures et 4 cm pour les ourlets.",
            SeamAllowance.INCLUDED,
            "1 cm",
        ),
        ("Les marges ne sont pas incluses et sont indiquées.", SeamAllowance.NOT_INCLUDED, ""),
        ("This pattern includes 1cm seam allowances.", SeamAllowance.INCLUDED, "1 cm"),
        ("3/8\" seam allowance is included in the pattern.", SeamAllowance.INCLUDED, "1 cm"),
        ("1.5-cm (5/8-in.) seam allowance included", SeamAllowance.INCLUDED, "1,5 cm"),
        ("Seam allowances are not included, add 1 cm.", SeamAllowance.NOT_INCLUDED, ""),
        ("Pas d'information ici.", SeamAllowance.UNKNOWN, ""),
    ],
)  # fmt: skip
def test_seam_allowance(text, status, detail):
    assert x.seam_allowance(x.prepare(text)) == (status, detail)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Vous aurez besoin d'2m10 de tissu d'une laize d'1m40 pour la plus grande taille.", D("2.10")),
        ("Tissu et métrage : il vous faudra 1 m50 de tissu pour la version courte", D("1.50")),
        ("75 cm de tissu du 34 au 40 / 80 cm du 42 au 52 en 145cm de laize", D("0.80")),
        ("Métrage : 1,90 m à 2,70 m en laize 140", D("2.70")),
        ("You will need 2.10m of a 1.40m width fabric", D("2.10")),
        ("Détail consommation tissu en laize d'1m40 / consumption details for a width of 1m40 : 1m40 pour une taille 34/36, 2m pour une taille 38/40, 2m10 pour une taille 42/44", D("2.10")),
        ("Tissu en laize de 140 / 145 cm width fabric of 140 / 145 cm 2m50", D("2.50")),
        ("Consommation tissu en laize d'1m40 : 1m40 pour une taille 34/36, 1m55 pour une taille 46/48. Laize d'1m10 : 2m05 pour une taille 46/48", D("1.55")),
        ("2m90 in 110 cm wide fabric ; 2m50 in 150 cm wide fabric. Métrage indiqué", D("2.50")),
        ("Aucun métrage indiqué", None),
    ],
)  # fmt: skip
def test_yardage_max(text, expected):
    assert x.yardage(x.prepare(text))[1] == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Tissus conseillés : viscose, crêpe, lin", FabricKind.WOVEN),
        ("Suggested fabrics: jersey, interlock", FabricKind.KNIT),
        ("Préconisations : cotonnade, lin, viscose", FabricKind.WOVEN),
        (
            "We advise you to make this skirt in a rather extensible fabric, jersey or crepe",
            FabricKind.BOTH,
        ),
        ("Nous vous conseillons d'utiliser une viscose fluide.", FabricKind.WOVEN),
        ("Couper le tissu en double", FabricKind.UNSPECIFIED),
    ],
)
def test_fabrics(text, expected):
    assert x.fabrics(x.prepare(text))[1] == expected


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        (["patron-de-couture-luisa-pdf.zip"], "Luisa"),
        (["Core Fabrics_Brise Knit Dress_Copyshop-A0.pdf"], "Brise Knit Dress"),
        (["MPF_Garance Notice & Planches.zip"], "Garance"),
        (["blouse-femme-quetzal-patron-de-couture-pdf-32-50.zip"], "Blouse Quetzal"),
    ],
)
def test_name_candidate(names, expected):
    brand_words = {"core", "fabrics", "mpf"}
    assert x.name_candidate(names, brand_words) == expected


@pytest.mark.parametrize(
    ("label", "first_page", "family"),
    [
        ("Luisa", "voici luisa, la petite robe trapèze", Family.DRESSES),
        ("Brise Knit Dress", "", Family.DRESSES),
        ("Blouse Quetzal", "", Family.TOPS),
        ("Ringo", "Le pantalon Ringo a une taille élastique", Family.BOTTOMS),
        ("Kiera jumpsuit", "", Family.JUMPSUITS),
        ("Zorro", "rien", Family.UNCLASSIFIED),
    ],
)
def test_garment_type(label, first_page, family):
    _, fam = x.garment_type(label, x.prepare(first_page))
    assert fam == family


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Robe pour femme", Audience.WOMEN),
        ("Men's cargo shorts", Audience.MEN),
        ("Chemise homme", Audience.MEN),
        ("Patron enfant 2-10 ans", Audience.KIDS),
        ("Sweat unisexe", Audience.UNISEX),
    ],
)
def test_audience(text, expected):
    assert x.audience(x.prepare(text)) == expected


def test_brand_prefers_file_names_and_aliases():
    brands = [(1, "Make My Lemonade", ["MML"]), (2, "Atelier Brunette", []), (3, "Lin", [])]
    assert x.brand(brands, ["mml-luisa.pdf"], x.prepare("rien")) == 1
    assert x.brand(brands, ["luisa.pdf"], x.prepare("un patron Atelier Brunette")) == 2
    assert x.brand(brands, ["luisa.pdf"], x.prepare("tissu en lin, robe")) is None  # too generic
    assert x.brand(brands, ["luisa.pdf"], x.prepare("www.atelierbrunette.com")) == 2  # glued
