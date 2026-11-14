"""Turn the free-text values of the legacy CSV inventory into normalized model values.

Ported from the MVP generator (`_moteur/regenerer_moteur.py`), where the same rules ran at
every page generation. Each function is pure and takes the raw CSV cell.
"""

import re
from decimal import Decimal

from django.db.backends.postgresql.psycopg_any import NumericRange

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
from apps.catalog.text import normalize as _norm

# First matching rule wins. Matched against the lowercased, accent-free garment type.
_FAMILY_RULES: list[tuple[Family, str]] = [
    (Family.LINGERIE, r"pyjama|robe de chambre|deshabille"),
    (Family.DRESSES, r"^robe|^top / robe"),
    (Family.SETS, r"^ensemble|\btop \+ jupe|haut / jupe|top \+ blouse"),
    (Family.JUMPSUITS, r"^combi|salopette|barboteuse"),
    (Family.LINGERIE, r"lingerie|maillot|soutien|calecon|body \(lingerie\)|^culotte"),
    (Family.RESOURCES, r"recueil|ressource|gabarit|motifs|tutoriel"),
    (
        Family.ACCESSORIES,
        r"^accessoire|^sac|pochette|^bob|chapeau|chausson|espadrille|coussin|pouf|deco|peluche"
        r"|etole|etui|tablier|tapis|^col\b|^element|capuche|deguisement",
    ),
    (
        Family.OUTERWEAR,
        r"^veste|manteau|blouson|bomber|parka|trench|blazer|^cape|capelet|^kimono|^gilet"
        r"|cardigan|bolero",
    ),
    (Family.KNITWEAR, r"^pull|^sweat|^tricot"),
    (Family.BOTTOMS, r"^pantalon|^jean|^short|^jupe"),
    (Family.TOPS, r"^blouse|^chemis|^top|^t-shirt|^body|^haut|^tunique|^crop|^polo"),
]


def family(garment_type: str) -> Family:
    x = _norm(garment_type)
    for fam, rx in _FAMILY_RULES:
        if re.search(rx, x):
            return fam
    return Family.UNCLASSIFIED


_ADVANCED_START = r"\W*(avance|difficile|confirme|pas froid aux yeux)"
_INTERMEDIATE_WORDS = (
    r"intermediaire|concentr|vaut le coup|patience|moyen|average|long|doucement mais surement"
)
_BEGINNER_WORDS = (
    r"debutant|facile|gateau|tarte|rien de tres|va bien se passer|pas de souci|easy|beginner"
    r"|simple comme bonjour|tranquille|ideal pour une 1ere fois|legeres notions"
)


def difficulty(raw: str) -> Difficulty:
    x = _norm(raw)
    if not x.strip():
        return Difficulty.UNSPECIFIED
    # A score such as "2/5" or "3,5/5" wins over words.
    m = re.search(r"(\d+(?:,\d)?)\s*/\s*(\d)", x)
    if m:
        ratio = float(m.group(1).replace(",", ".")) / int(m.group(2))
        if ratio <= 0.5:
            return Difficulty.BEGINNER
        return Difficulty.INTERMEDIATE if ratio < 0.8 else Difficulty.ADVANCED
    # Anchored: "Débutant confirmé" must stay beginner.
    if re.match(_ADVANCED_START, x):
        return Difficulty.ADVANCED
    # "Débutant / intermédiaire" stays beginner: the first word decides.
    if re.search(_INTERMEDIATE_WORDS, x) and not re.match(r"\W*debutant", x):
        return Difficulty.INTERMEDIATE
    if re.search(_BEGINNER_WORDS, x):
        return Difficulty.BEGINNER
    return Difficulty.UNSPECIFIED


_KNIT = (
    r"maille|jersey|knit|sweat|molleton|interlock|cotele|lycra|stretch|extensible|polaire"
    r"|bord-cote|french terry|ponte"
)
_NOT_KNIT = r"non extensible|pas extensible|sans elasticite|no stretch|non-stretch"
_WOVEN = (
    r"chaine et trame|woven|tisse|popeline|lin\b|viscose|crepe|denim|gabardine|satin|velours"
    r"|toile|batiste|voile|seersucker|double gaze|twill|serge|lainage|tencel|non extensible"
    r"|cupro|chambray|jean|broderie"
)


def fabric_kind(raw: str) -> FabricKind:
    x = _norm(raw)
    knit = bool(re.search(_KNIT, x)) and not re.search(_NOT_KNIT, x)
    woven = bool(re.search(_WOVEN, x))
    if knit and woven:
        return FabricKind.BOTH
    if knit:
        return FabricKind.KNIT
    if woven:
        return FabricKind.WOVEN
    return FabricKind.UNSPECIFIED


_SEASON_WORDS: list[tuple[str, Season]] = [
    (r"\btoutes\b", Season.ALL),
    (r"\bete\b", Season.SUMMER),
    (r"\bmi-saison\b", Season.MIDSEASON),
    (r"\bautomne\b", Season.MIDSEASON),  # the MVP counted autumn as mid-season
    (r"\bhiver\b", Season.WINTER),
]


def seasons(raw: str) -> tuple[list[Season], bool]:
    """Return (seasons, inferred). "(déduite)" marks a season inferred from fabrics/type."""
    x = _norm(raw)
    found: list[Season] = []
    for rx, season in _SEASON_WORDS:
        if re.search(rx, x) and season not in found:
            found.append(season)
    return found, "deduite" in x


def yardage_max(raw: str) -> Decimal | None:
    """Largest length in metres, read before any parenthesis or ';' (the details after it
    mention other fabric widths or versions)."""
    head = raw.split("(")[0].split(";")[0]
    values = []
    for number, unit in re.findall(r"(\d+(?:[.,]\d+)?)\s*(cm|m)?", head):
        v = Decimal(number.replace(",", "."))
        if unit == "cm" or (not unit and v > 20):
            v /= 100
        if 0 < v < 15:
            values.append(v)
    return max(values).quantize(Decimal("0.01")) if values else None


def seam_allowance(raw: str) -> tuple[SeamAllowance, str]:
    """Return (status, detail), e.g. "Comprises (1 cm)" -> (INCLUDED, "1 cm")."""
    raw = raw.strip()
    m = re.fullmatch(r"(Comprises|Non comprises)\s*(?:\((.*)\))?", raw)
    if not m:
        return SeamAllowance.UNKNOWN, ""
    status = SeamAllowance.INCLUDED if m.group(1) == "Comprises" else SeamAllowance.NOT_INCLUDED
    return status, (m.group(2) or "").strip()


def info_source(raw: str) -> InfoSource:
    x = _norm(raw)
    if x.startswith("regle"):
        return InfoSource.BRAND_RULE
    if x.startswith("livret"):
        return InfoSource.BOOKLET
    if x.startswith("planche"):
        return InfoSource.PIECES
    return InfoSource.UNKNOWN


_AUDIENCES = {
    "femme": Audience.WOMEN,
    "homme": Audience.MEN,
    "mixte": Audience.UNISEX,
    "enfant": Audience.KIDS,
}


def audience(raw: str) -> Audience:
    return _AUDIENCES.get(_norm(raw).strip(), Audience.UNSPECIFIED)


_CONFIDENCES = {"certaine": BrandConfidence.CERTAIN, "probable": BrandConfidence.PROBABLE}


def brand_confidence(raw: str) -> BrandConfidence:
    return _CONFIDENCES.get(_norm(raw).strip(), BrandConfidence.UNKNOWN)


_MEASURE_TYPES = {"corps": MeasureType.BODY, "vetement fini": MeasureType.FINISHED}


def measure_type(raw: str) -> MeasureType:
    try:
        return _MEASURE_TYPES[_norm(raw).strip()]
    except KeyError:
        raise ValueError(f"Unknown measure type: {raw!r}") from None


_NUMBER = r"\d+(?:,\d+)?"


def measure_range(raw: str) -> NumericRange | None:
    """ "88" -> [88, 88], "80–84" -> [80, 84] (inclusive bounds), "" -> None."""
    raw = raw.strip()
    if not raw:
        return None
    m = re.fullmatch(rf"({_NUMBER})\s*(?:[–-]\s*({_NUMBER}))?", raw)
    if not m:
        raise ValueError(f"Unparsable measure: {raw!r}")
    lower = Decimal(m.group(1).replace(",", "."))
    upper = Decimal(m.group(2).replace(",", ".")) if m.group(2) else lower
    return NumericRange(lower, upper, "[]")


def tags(raw: str) -> list[tuple[str, str]]:
    """ "Manches : courtes ; Fermeture : nouée" -> [("Manches", "courtes"), ...]."""
    out = []
    for part in raw.split(" ; "):
        if " : " in part:
            group, value = part.split(" : ", 1)
            out.append((group.strip(), value.strip()))
    return out


def sewn(raw: str) -> bool:
    return _norm(raw).strip() in {"oui", "yes", "x", "1", "true"}
