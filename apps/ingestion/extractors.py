"""Deterministic field extractors for pattern ingestion (no LLM).

Each extractor takes booklet text prepared with `prepare()` (lowercase, accent-free,
apostrophes as spaces, whitespace collapsed) and/or file names, and returns a proposal that
the user validates. When nothing reliable is found, they return an empty value: never guess.
"""

import itertools
import re
import unicodedata
from decimal import Decimal, InvalidOperation

from apps.catalog import normalize as n
from apps.catalog.models import Audience, Difficulty, FabricKind, Family, SeamAllowance


def prepare_map(text: str) -> tuple[str, list[int]]:
    """Prepared text (see `prepare`) and, for each of its characters, its index in `text`, so
    that a snippet found in the prepared text can be shown as originally written."""
    out: list[str] = []
    index: list[int] = []
    space = True  # drop leading whitespace
    for i, ch in enumerate(text):
        if ch in "'’`":
            ch = " "
        decomposed = unicodedata.normalize("NFKD", ch)
        for c in decomposed.lower():
            if unicodedata.combining(c):
                continue
            if c.isspace():
                if space:
                    continue
                c, space = " ", True
            else:
                space = False
            out.append(c)
            index.append(i)
    if out and out[-1] == " ":
        out.pop()
        index.pop()
    return "".join(out), index


def prepare(text: str) -> str:
    """Lowercase, accent-free, apostrophes as spaces, whitespace collapsed."""
    return prepare_map(text)[0]


def original(raw: str, prepared: str, index: list[int], snippet: str) -> str:
    """The raw text behind a snippet of the prepared text (accents, capitals), one line."""
    pos = prepared.find(snippet) if snippet else -1
    if pos < 0:
        return snippet
    start, end = index[pos], index[pos + len(snippet) - 1] + 1
    return re.sub(r"\s+", " ", raw[start:end]).strip()


def _num(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", "."))
    except InvalidOperation:
        return None


def _fmt(value: Decimal) -> str:
    return format(value.normalize(), "f").replace(".", ",")


# ---- sizes

_RANGE_SEP = r"\s*(?:-|–|au|a|/|to)\s*"
_NUMERIC_SIZE_PATTERNS = [
    rf"\btailles?\b[^0-9]{{0,25}}?(?:du\s+)?t?(\d{{2}}){_RANGE_SEP}t?(\d{{2}})\b(?!\s*ans)",
    rf"\bsizes?\b[^0-9]{{0,25}}?(\d{{2}}){_RANGE_SEP}(\d{{2}})\b",
    r"\bdu\s+t?\s*(\d{2})\s+au\s+t?\s*(\d{2})\b(?!\s*ans)",
]
_LETTER = r"(xxs|xs|s|m|l|xl|xxl|xxxl|[2-6]xl)"
_LETTER_SIZE = rf"\b(?:tailles?|sizes?)\b[^a-z0-9]{{0,20}}{_LETTER}{_RANGE_SEP}{_LETTER}\b"


def size_range(text: str) -> str:
    """Widest size range found: an explicit range near a size keyword ("du 34 au 48"), or a
    run of at least 4 consecutive sizes (size table header). Pairs such as "34/36" from a
    measurement table are ignored: a range must span at least 3 sizes."""
    candidates: list[tuple[int, int]] = []
    for rx in _NUMERIC_SIZE_PATTERNS:
        for a, b in re.findall(rx, text):
            if int(a) >= 30 and int(a) + 4 <= int(b) <= 64:
                candidates.append((int(a), int(b)))
    for m in re.finditer(r"\b(?:tailles?|sizes?)\b\s*:?\s*((?:\d{2}\s+){3,}\d{2})\b", text):
        sizes = [int(v) for v in m.group(1).split()]
        steps = {b - a for a, b in itertools.pairwise(sizes)}
        if steps == {2} and sizes[0] >= 30 and sizes[-1] <= 64:
            candidates.append((sizes[0], sizes[-1]))
    if candidates:
        low, high = max(candidates, key=lambda c: (c[1] - c[0], candidates.count(c)))
        return f"{low} – {high}"
    if m := re.search(_LETTER_SIZE, text):
        return f"{m.group(1).upper()} – {m.group(2).upper()}"
    if re.search(r"\btaille unique\b|\bone size\b", text):
        return "Taille unique"
    return ""


# ---- difficulty

_DIFFICULTY_KEYWORDS = r"\b(?:niveau|difficulte|level|difficulty)\b"


def difficulty(text: str) -> tuple[Difficulty, str]:
    """(level, raw snippet). Looks right after then right before each level keyword."""
    for m in re.finditer(_DIFFICULTY_KEYWORDS, text):
        if text[max(0, m.start() - 3) : m.start()] == "au ":  # "au niveau des épaules"
            continue
        after = re.sub(r"^[\s/:]*(?:lev\s*el|level)?[\s/:]*", "", text[m.end() : m.end() + 50])
        before = text[max(0, m.start() - 35) : m.start()]
        for snippet in (after, before):
            level = n.difficulty(snippet)
            if level != Difficulty.UNSPECIFIED:
                return level, _level_words(snippet, level, from_end=snippet is before)
    return Difficulty.UNSPECIFIED, ""


def _level_words(snippet: str, level: Difficulty, from_end: bool) -> str:
    """The few words that carry the level ("simple comme bonjour"), not the whole snippet.
    A single word ("debutant") is replaced by the level label, which keeps its accents."""
    words = snippet.split()
    for size in range(1, len(words) + 1):
        part = words[-size:] if from_end else words[:size]
        if n.difficulty(" ".join(part)) == level:
            text = " ".join(part).strip(" :/!.«»")
            if re.search(r"\d", text):
                return text  # a score such as "3/5"
            if size == 1:
                return str(level.label)  # keeps the accents of "Débutant"
            return f"« {text[0].upper()}{text[1:]} »"  # a brand phrase
    return snippet.strip()[:60]


# ---- seam allowance

_INCH_FRACTIONS = {"1/4": "0.6", "3/8": "1", "1/2": "1.3", "5/8": "1.5"}
_SEAM_TOPIC = r"marges?|valeurs? de couture|seam allowances?"
_NOT_INCLUDED = (
    r"non (?:comprises?|incluses?)|ne sont pas (?:comprises?|incluses?)|pas incluses?"
    r"|not included|are not included|sans marges?|add (?:a |the )?\d*.{0,10}seam allowance"
    r"|ajouter (?:les |des )?marges"
)
_INCLUDED = r"compris|inclus|included|includes|including"


def seam_allowance(text: str) -> tuple[SeamAllowance, str]:
    """(status, main width such as "1 cm") from the first sentence about seam allowances."""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if not re.search(_SEAM_TOPIC, sentence):
            continue
        if re.search(_NOT_INCLUDED, sentence):
            return SeamAllowance.NOT_INCLUDED, ""
        if re.search(_INCLUDED, sentence):
            return SeamAllowance.INCLUDED, _seam_width(sentence)
    return SeamAllowance.UNKNOWN, ""


def _seam_width(sentence: str) -> str:
    if m := re.search(r"(\d+(?:[.,]\d+)?)\s*-?\s*cm\b", sentence):
        value = _num(m.group(1))
        if value is not None and 0 < value <= 5:
            return f"{_fmt(value)} cm"
    if (m := re.search(r"\b([1-5]/[248])\b", sentence)) and m.group(1) in _INCH_FRACTIONS:
        return f"{_fmt(Decimal(_INCH_FRACTIONS[m.group(1)]))} cm"
    return ""


# ---- yardage

_YARDAGE_TOPIC = (
    r"metrage|vous aurez besoin|il vous faudra|you will need|yardage|fabric requirements?"
    r"|quantite de tissu|\bde tissu\b|consommation|consumption|tissu en laize"
)
_LENGTH = r"\d+(?:[.,]\d+)?\s*(?:m\s*\d{2}|m|cm)?"
TARGET_WIDTH_CM = (135, 160)  # the yardage field is for 140-150 cm wide fabric


def yardage(text: str) -> tuple[str, Decimal | None]:
    """(raw snippet, largest length in metres) near a yardage keyword.

    Lengths are attributed to the fabric width that introduces them ("laize 1m40 : 2m10…") or
    follows them ("2m50 in 150 cm wide fabric"). Only 140-150 cm widths count; lengths with no
    width at all are used when no such width is mentioned.
    """
    target: list[Decimal] = []
    no_width: list[Decimal] = []
    snippet = ""
    for m in re.finditer(_YARDAGE_TOPIC, text):
        window = text[max(0, m.start() - 60) : m.end() + 220]
        for width, segment in _segments_by_width(window):
            found = _lengths(segment)
            if found and not snippet:
                snippet = text[m.start() : m.end() + 220].strip()
            if width is None:
                no_width += found
            elif TARGET_WIDTH_CM[0] <= width <= TARGET_WIDTH_CM[1]:
                target += found
    values = target or no_width
    return (snippet[:200], max(values).quantize(Decimal("0.01"))) if values else ("", None)


def _width_cm(text: str) -> int | None:
    if m := re.search(r"(\d)\s*m\s*(\d{2})", text):  # 1m40
        return int(m.group(1)) * 100 + int(m.group(2))
    if m := re.search(r"(\d+(?:[.,]\d+)?)", text):
        value = Decimal(m.group(1).replace(",", "."))
        return int(value * 100) if value < 3 else int(value)
    return None


def _segments_by_width(window: str) -> list[tuple[int | None, str]]:
    """Split a window into (width in cm or None, text whose lengths belong to that width)."""
    leading = rf"(?:laizes?|width|de large)\s*(?:of\s*|d\s*|de\s*)*(?P<lead>{_LENGTH})"
    trailing = rf"(?P<trail>{_LENGTH})\s*(?:de laize|wide|width)"
    segments: list[tuple[int | None, str]] = []
    current: int | None = None
    pos = 0
    for m in re.finditer(rf"{leading}(?:\s*/\s*{_LENGTH})*|{trailing}", window):
        before = window[pos : m.start()]
        if m.group("trail"):  # the width closes the text before it
            segments.append((_width_cm(m.group("trail")), before))
        else:
            segments.append((current, before))
            current = _width_cm(m.group("lead"))
        pos = m.end()
    segments.append((current, window[pos:]))
    return segments


def _lengths(text: str) -> list[Decimal]:
    out = []
    for a, b in re.findall(r"\b(\d)\s*m\s*(\d{2})\b", text):  # 2m10, 1 m50
        out.append(Decimal(f"{a}.{b}"))
    text = re.sub(r"\b\d\s*m\s*\d{2}\b", " ", text)
    for v in re.findall(r"\b(\d+(?:[.,]\d+)?)\s*m\b", text):  # 2,10 m
        if (value := _num(v)) is not None:
            out.append(value)
    for v in re.findall(r"\b(\d{2,3})\s*cm\b", text):  # 80 cm
        out.append(Decimal(v) / 100)
    return [v for v in out if Decimal("0.3") <= v <= 8]


# ---- fabrics

_FABRIC_TOPIC = (
    r"tissus? conseilles?|tissus? recommandes?|choix du tissu|preconisations|matieres? conseillees?"
    r"|suggested fabrics?|fabric suggestions?|recommended fabrics?|tissus? :|conseils? tissus?"
    r"|nous vous (?:conseillons|recommandons)|we (?:advise|recommend|suggest)|fabrics? such as"
)


def fabrics(text: str) -> tuple[str, FabricKind]:
    if m := re.search(_FABRIC_TOPIC, text):
        window = text[m.end() : m.end() + 200]
        window = re.split(r"(?<=[.!?])\s", window)[0]
        kind = n.fabric_kind(window)
        if kind != FabricKind.UNSPECIFIED:
            return window.strip(" :"), kind
    return "", FabricKind.UNSPECIFIED


# ---- name, type, audience, brand

_NAME_NOISE = {
    "patron", "patrons", "pattern", "patterns", "couture", "sewing", "pdf", "femme", "homme",
    "women", "woman", "womens", "men", "a4", "a0", "a3", "us", "letter", "copyshop", "projector",
    "projecteur", "livret", "notice", "planches", "planche", "instructions", "de", "la", "le",
    "les", "du", "by", "fr", "en", "eng", "the", "size", "sizes", "taille", "tailles", "pack",
    "only", "sa", "et", "and", "x", "digital", "download", "free", "files", "fichiers", "version",
    "t", "ans", "kids", "enfant", "compressed", "optimized", "optimised", "final", "web",
    "printable", "print", "tiled", "layers", "layered", "merged", "file", "v1", "v2", "v3",
}  # fmt: skip


def name_candidate(file_names: list[str], brand_words: set[str]) -> str:
    """Pattern name guessed from the first file name: noise words, sizes and brand removed."""
    for name in file_names:
        stem = re.sub(r"\.[a-z0-9]{2,4}$", "", name.rsplit("/", 1)[-1], flags=re.I)
        words = re.split(r"[^a-z0-9]+", prepare(stem))
        words = [w for w in words if w]
        if words and _is_shop_suffix(words[-1]):
            words = words[:-1]
        kept = [w for w in words if w not in _NAME_NOISE | brand_words and not _is_id(w)]
        if kept:
            return " ".join(_display(w) for w in kept)
    return ""


def _is_id(word: str) -> bool:
    """Numbers and random shop suffixes ("eb80ai", "xrzcej"-like mixes of letters and digits).
    Product codes ("up1056": letters then digits) are not ids: they name the pattern."""
    if word.isdigit():
        return True
    if re.fullmatch(r"[a-z]{1,4}\d{2,6}", word):
        return False
    return len(word) == 6 and any(c.isdigit() for c in word) and any(c.isalpha() for c in word)


def _is_shop_suffix(word: str) -> bool:
    """Random letters appended by shops to download names ("…-barrel-xrzcej")."""
    return len(word) == 6 and word.isalpha() and sum(c in "aeiouy" for c in word) <= 1


def _display(word: str) -> str:
    return word.upper() if re.fullmatch(r"[a-z]{1,4}\d{2,6}", word) else word.capitalize()


_TYPE_WORDS = [
    ("combishort", "Combishort"), ("combinaison", "Combinaison"), ("jumpsuit", "Combinaison"),
    ("salopette", "Salopette"), ("dungarees", "Salopette"), ("robe", "Robe"), ("dress", "Robe"),
    ("jupe", "Jupe"), ("skirt", "Jupe"), ("pantalon", "Pantalon"), ("trousers", "Pantalon"),
    ("pants", "Pantalon"), ("legging", "Pantalon"), ("jogging", "Pantalon"), ("jean", "Jean"),
    ("short", "Short"), ("shorts", "Short"), ("bermuda", "Short"), ("blouse", "Blouse"),
    ("chemise", "Chemise"), ("shirt", "Chemise"), ("tunique", "Tunique"), ("top", "Top"),
    ("t-shirt", "T-shirt"), ("tee", "T-shirt"), ("veste", "Veste"), ("jacket", "Veste"),
    ("blazer", "Veste"), ("manteau", "Manteau"), ("coat", "Manteau"), ("trench", "Trench"),
    ("parka", "Parka"), ("caban", "Manteau"), ("doudoune", "Manteau"), ("blouson", "Blouson"),
    ("bomber", "Bomber"), ("kimono", "Kimono"), ("cape", "Cape"), ("poncho", "Cape"),
    ("gilet", "Gilet"), ("cardigan", "Cardigan"), ("pull", "Pull"), ("sweater", "Pull"),
    ("sweat", "Sweat"), ("sweatshirt", "Sweat"), ("hoodie", "Sweat"), ("sac", "Sac"),
    ("bag", "Sac"), ("tote", "Sac"), ("pochette", "Pochette"), ("trousse", "Pochette"),
    ("tablier", "Tablier"), ("apron", "Tablier"), ("pyjama", "Pyjama"), ("culotte", "Culotte"),
    ("body", "Body"), ("maillot", "Maillot de bain"), ("soutien-gorge", "Soutien-gorge"),
    ("brassiere", "Soutien-gorge"), ("nuisette", "Nuisette (lingerie)"),
]  # fmt: skip


def garment_type(label: str, opening: str) -> tuple[str, Family]:
    """Garment type from the name, else from the opening pages; the earliest word wins."""
    for source in (prepare(label), opening[:3000]):
        hits = [
            (m.start(), value)
            for word, value in _TYPE_WORDS
            if (m := re.search(rf"\b{re.escape(word)}\b", source))
        ]
        if hits:
            value = min(hits)[1]
            return value, n.family(value)
    return "", Family.UNCLASSIFIED


_UPPER = "A-ZÀ-ÖØ-Þ"
_WORD = "A-Za-zÀ-ÖØ-öø-ÿ"
_NAME_IN_TEXT = [
    # "PATRON DE COUTURE HENDRIKS", "Sewing pattern: Juniper"
    rf"(?i:patron de couture|sewing pattern)\s*[:\-–]?\s*([{_UPPER}][{_WORD}'’-]{{2,30}})",
    # "Voici Hendriks", "Voici la robe Cosmos", "Meet Juniper"
    rf"\b(?:Voici|Voilà|Découvrez|Meet|Introducing)\s+(?:(?:la|le|les|l’|l')\s*)?"
    rf"(?:(?:robe|jupe|blouse|chemise|veste|combinaison|pantalon|top|manteau|nouvelle|nouveau)\s+)?"
    rf"([{_UPPER}][{_WORD}'’-]{{2,30}})",
]
_NOT_NAMES = {"le", "la", "les", "notre", "votre", "patron", "pattern", "this", "the", "nouveau"}


def name_from_text(raw_text: str, brand_words: set[str]) -> str:
    """Pattern name as written in the booklet; the most frequent candidate wins."""
    found: list[str] = []
    for rx in _NAME_IN_TEXT:
        for m in re.finditer(rx, raw_text[:20000]):
            word = m.group(1).strip("'’-")
            key = prepare(word)
            if (
                key not in _NOT_NAMES
                and key not in brand_words
                and not any(key == w for w, _ in _TYPE_WORDS)
            ):
                found.append(word[0].upper() + word[1:].lower())
    return max(set(found), key=lambda w: (found.count(w), -found.index(w))) if found else ""


_SEASON_WORDS = [
    ("all", r"toute l annee|toutes saisons|all year|year round"),
    ("winter", r"\bhiver|hivernal|\bwinter\b"),
    ("summer", r"\bete\b|estival|beaux jours|\bsummer\b"),
    ("midseason", r"mi-saison|\bprintemps|\bautomne|\bspring\b|\bautumn\b"),
]


def seasons(text: str) -> list[str]:
    """Seasons named in the booklet (to be marked as inferred). "All year" wins alone."""
    found = [season for season, rx in _SEASON_WORDS if re.search(rx, text)]
    return ["all"] if "all" in found else found


# Measured with evaluate_extraction (150 patterns, seed 3): tags whose wording proved
# misleading were removed (gathered/drawstring, bustier, cargo, invisible zip, midi, maxi, wrap,
# shirt collar, puff sleeves, sleeveless, oversize: below 60 % precision).
TAG_RULES: dict[tuple[str, str], str] = {
    ("Manches", "courtes"): r"manches? courtes?|short sleeves?",
    ("Manches", "longues"): r"manches? longues?|long sleeves?",
    ("Manches", "3/4"): r"manches? 3/4|3/4 sleeves?",
    ("Manches", "raglan"): r"raglan",
    ("Manches", "kimono / chauve-souris"): r"manches? kimono|chauve-souris|batwing",
    ("Manches", "volantées"): r"manches? volantees?|flutter sleeves?",
    ("Manches", "mancherons"): r"mancherons?|cap sleeves?",
    ("Manches", "manches tombantes"): r"epaules? tombantes?|dropped? shoulders?",
    ("Col / encolure", "col Claudine"): r"col claudine|peter pan collar",
    ("Col / encolure", "col mao"): r"col mao|mandarin collar",
    ("Col / encolure", "col roulé"): r"col roule|turtleneck|roll neck",
    (
        "Col / encolure",
        "col cubain / à revers",
    ): r"col cubain|col a revers|revere collar|camp collar",
    ("Col / encolure", "encolure V"): r"encolure (?:en )?v\b|decollete (?:en )?v\b|v-neck",
    ("Col / encolure", "encolure carrée"): r"encolure carree|square neck",
    ("Col / encolure", "encolure bateau"): r"encolure bateau|boat neck",
    ("Col / encolure", "cache-cœur"): r"cache-coeur|cache coeur",
    ("Col / encolure", "dos nu"): r"\bdos nu\b|backless",
    ("Col / encolure", "capuche"): r"capuche|\bhood\b",
    (
        "Fermeture",
        "boutons devant",
    ): r"boutonnage devant|boutons? (?:sur le |au )?devant|button front",
    (
        "Fermeture",
        "zip invisible côté",
    ): r"(?:fermeture|zip) (?:a glissiere )?invisible|invisible zip",
    ("Fermeture", "pressions / boutons pression"): r"boutons? pression|\bpressions\b|\bsnaps\b",
    ("Fermeture", "nouée"): r"\bnouee?s?\b|se noue|self-tie|tie front",
    (
        "Taille",
        "taille élastique",
    ): r"(?:ceinture|taille) (?:est )?elastique|elastic(?:ated)? waist",
    ("Taille", "taille smockée"): r"smock",
    ("Taille", "taille empire"): r"taille empire|empire waist",
    ("Taille", "ceinture paperbag"): r"paper ?bag",
    ("Jambe", "jambe large"): r"jambes? larges?|wide[- ]leg",
    ("Jambe", "fuselée"): r"fusele|tapered",
    ("Jambe", "barrel"): r"\bbarrel\b",
    ("Longueur", "crop"): r"\bcrop",
    ("Coupe", "trapèze"): r"trapeze|a-line",
}


def tags(text: str) -> list[tuple[str, str]]:
    """Detail tags whose wording appears in the booklet (to check: a booklet may mention an
    option that is not part of this pattern)."""
    return [tag for tag, rx in TAG_RULES.items() if re.search(rx, text)]


_COPYRIGHT = re.compile(
    r"(?:©|\(c\)|(?i:copyright))\s*(?:\d{4}\s*)?([A-Z][\w&'’-]*(?:[ ][A-Z&][\w&'’-]*){0,4})",
)


def copyright_holder(raw_text: str) -> str:
    """Brand named in a copyright notice ("© Unfettered Patterns 2025"), from the raw text
    (capitals matter). Used to suggest a new brand when no known brand matched."""
    if m := _COPYRIGHT.search(raw_text):
        return m.group(1).strip(" .,-")
    return ""


def brand_from_name(name: str, brands: list[tuple[int, str, list[str]]]) -> int | None:
    """Known brand whose name or alias matches a brand name read in the booklet."""
    wanted = " ".join(re.split(r"[^a-z0-9]+", prepare(name))).strip()
    if not wanted:
        return None
    for pk, label, aliases in brands:
        for candidate in [label, *aliases]:
            key = " ".join(re.split(r"[^a-z0-9]+", prepare(re.sub(r"\(.*?\)", " ", candidate))))
            key = key.strip()
            # "Make My Lemonade Paris" contains "make my lemonade"; a lone word ("Fibre") is
            # too vague to stand for "Fibre Mood".
            contained = (" " in key and key in wanted) or (" " in wanted and wanted in key)
            if key and (key == wanted or contained):
                return pk
    return None


def audience(text: str) -> Audience:
    if re.search(r"\b(?:unisexe?|mixte|unisex)\b", text):
        return Audience.UNISEX
    if re.search(r"\b(?:enfants?|kids?|child(?:ren)?|bebe|baby|fillette|garcon)\b", text):
        return Audience.KIDS
    if re.search(r"\b(?:homme|hommes|men|mens|man|masculin)\b", text):
        return Audience.MEN
    return Audience.WOMEN


def brand(brands: list[tuple[int, str, list[str]]], file_names: list[str], text: str) -> int | None:
    """Brand id whose name or alias appears in the file names (strong signal) or the text.
    One-word names shorter than 6 letters only count in file names (too generic in text)."""
    files = " " + " ".join(re.split(r"[^a-z0-9]+", prepare(" ".join(file_names)))) + " "
    words = " " + " ".join(re.split(r"[^a-z0-9]+", text)) + " "
    glued = re.sub(r"[^a-z0-9]", "", text + " " + files)  # URLs, e-mails: "atelierbrunette"
    scores: dict[int, int] = {}
    for pk, name, aliases in brands:
        for label in [name, *aliases]:
            key = " ".join(
                w for w in re.split(r"[^a-z0-9]+", prepare(re.sub(r"\(.*?\)", " ", label))) if w
            )
            if not key:
                continue
            score = files.count(f" {key} ") * 10
            if " " in key or len(key) >= 6:
                score += words.count(f" {key} ")
            if not score and len(compact := key.replace(" ", "")) >= 8 and compact in glued:
                score = 1
            if score:
                scores[pk] = scores.get(pk, 0) + score
    return max(scores, key=scores.get) if scores else None
