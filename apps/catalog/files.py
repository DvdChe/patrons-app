"""Pattern files: classification and content-addressed storage, shared by the MVP import and
the upload of new patterns."""

import hashlib
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import IO

from django.core.files import File
from django.core.files.storage import default_storage

from .models import PatternFile, blob_name
from .text import normalize

JUNK_NAMES = {".ds_store", "thumbs.db", "desktop.ini", "__macosx"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".tif", ".tiff"}
_BOOKLET = (
    r"livret|notice|instruction|explication|guide|tuto|gamme|\bgdm\b|booklet|manual"
    r"|mode d.?emploi|step by step"
)
_PIECES = (
    r"planche|\ba[0-4]\b|letter|copy ?shop|projecteur|projector|pattern ?pieces|pieces"
    r"|calque|layers?|tiled|\bprint"
)
CHUNK = 1024 * 1024


def file_kind(name: str) -> PatternFile.Kind:
    suffix = PurePosixPath(name).suffix.lower()
    if suffix == ".zip":
        return PatternFile.Kind.ARCHIVE
    if suffix in IMAGE_SUFFIXES:
        return PatternFile.Kind.IMAGE
    if suffix == ".pdf":
        # Split CamelCase ("SewingInstructions") and separators before matching words.
        words = normalize(re.sub(r"(?<=[a-z])(?=[A-Z])", " ", PurePosixPath(name).stem))
        words = re.sub(r"[_\-.&]+", " ", words)
        if re.search(_BOOKLET, words):
            return PatternFile.Kind.BOOKLET
        if re.search(_PIECES, words):
            return PatternFile.Kind.PIECES
    return PatternFile.Kind.OTHER


def is_junk(parts: tuple[str, ...]) -> bool:
    """macOS/Windows metadata, hidden and Office lock files."""
    return any(p.startswith((".", "~$")) or p.lower() in JUNK_NAMES for p in parts)


def nfc(name: str) -> str:
    """macOS file names are often NFD: store and compare them in NFC."""
    return unicodedata.normalize("NFC", name)


@dataclass
class Entry:
    """A file to store: a file on disk, a zip member or an upload."""

    name: str  # path inside the pattern folder or zip, NFC, "/" separated
    size: int
    open: Callable[[], IO[bytes]]


def sha256(entry: Entry) -> str:
    digest = hashlib.sha256()
    with entry.open() as fh:
        while chunk := fh.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def store_blob(owner_id: int, entry: Entry, digest: str | None = None) -> tuple[str, str, bool]:
    """Store the entry under its content hash unless already there.

    Returns (storage name, sha256, uploaded?).
    """
    digest = digest or sha256(entry)
    name = blob_name(owner_id, digest, entry.name)
    if default_storage.exists(name):
        return name, digest, False
    with entry.open() as fh:
        content = File(fh, name=PurePosixPath(entry.name).name)
        content.size = entry.size
        saved = default_storage.save(name, content)
    if saved != name:  # another process stored the same blob meanwhile
        default_storage.delete(saved)
    return name, digest, True
