import unicodedata


def normalize(value: str) -> str:
    """Lowercase and strip accents, for accent-insensitive search ("Élise" -> "elise")."""
    decomposed = unicodedata.normalize("NFKD", value)
    return decomposed.encode("ascii", "ignore").decode().lower()
