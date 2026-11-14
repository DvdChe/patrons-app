import unicodedata

# Ligatures have no ASCII decomposition: NFKD + ASCII would drop them ("cœur" -> "cur").
_LIGATURES = str.maketrans({"œ": "oe", "Œ": "oe", "æ": "ae", "Æ": "ae"})


def normalize(value: str) -> str:
    """Lowercase and strip accents, for accent-insensitive search ("Élise" -> "elise").

    Keep in sync with `norm()` in static/catalog/catalogue.js.
    """
    decomposed = unicodedata.normalize("NFKD", value.translate(_LIGATURES))
    return decomposed.encode("ascii", "ignore").decode().lower()
