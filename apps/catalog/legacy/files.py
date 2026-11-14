"""Import the pattern files (PDF, images, archives) of the MVP `Patrons` folder into storage.

- Each pattern's `legacy_location` (folder, zip or single file) is read; zip members are read
  in place, nothing is extracted to disk.
- Blobs are content-addressed (`blob_name`): identical files are stored once per owner and a
  re-run uploads nothing new.
- Several patterns may share one location (magazine issues, books, bundles): a file is then
  only linked to the pattern whose name appears in the file name.
"""

import hashlib
import re
import unicodedata
import zipfile
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import IO

from django.core.files import File
from django.core.files.storage import default_storage

from apps.catalog.models import Pattern, PatternFile, blob_name
from apps.catalog.text import normalize

PATRONS_PREFIX = "Documents/Patrons/"
SKIPPED_NAMES = {".ds_store", "thumbs.db", "desktop.ini", "__macosx"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".tif", ".tiff"}
_BOOKLET = (
    r"livret|notice|instruction|explication|guide|tuto|gamme|\bgdm\b|booklet|manual"
    r"|mode d.?emploi|step by step"
)
_PIECES = (
    r"planche|\ba[0-4]\b|letter|copy ?shop|projecteur|projector|pattern ?pieces|pieces"
    r"|calque|layers?|tiled|\bprint"
)
_STOPWORDS = {"and", "the", "et", "les", "des", "pour", "with"}
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


def _words(text: str) -> list[str]:
    """Lowercase ASCII words; CamelCase is split ("CowlNeckTop" -> cowl, neck, top)."""
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
    text = re.sub(r"['’](?=s\b)", "", text)  # English possessive: "Men’s" -> mens
    text = re.sub(r"['’]", " ", text)  # French elision: "L’Harmonieuse" -> l, harmonieuse
    return [w for w in re.split(r"[^a-z0-9]+", normalize(text)) if w]


def matches_pattern(pattern_name: str, filename: str) -> bool:
    """True if every significant word of the pattern name is a word of the file name."""
    base = re.sub(r"\(.*?\)", " ", pattern_name)
    wanted = [w for w in _words(base) if len(w) >= 3 and w not in _STOPWORDS] or _words(base)
    present = set(_words(PurePosixPath(filename).as_posix()))
    return bool(wanted) and all(w in present for w in wanted)


def _nz(name: str) -> str:
    return unicodedata.normalize("NFC", name).replace("\xa0", " ").lower()


def resolve(source: Path, location: str) -> Path | None:
    """Find a legacy location on disk; tolerant to a missing ".zip" and to Unicode/case
    differences in the last path component (same rules as the MVP)."""
    if not location.startswith(PATRONS_PREFIX):
        return None
    rel = location[len(PATRONS_PREFIX) :].strip("/")
    path = source / rel
    if path.exists():
        return path
    if (zipped := source / f"{rel}.zip").exists():
        return zipped
    if not path.parent.is_dir():
        return None
    base = _nz(path.name)
    entries = list(path.parent.iterdir())
    found = [e for e in entries if _nz(e.name) in (base, f"{base}.zip")] or [
        e for e in entries if base in _nz(e.name)
    ]
    return found[0] if len(found) == 1 else None


@dataclass
class Entry:
    name: str  # path inside the location, NFC, "/" separated
    size: int
    open: Callable[[], IO[bytes]]


def _skipped(parts: tuple[str, ...]) -> bool:
    return any(p.startswith((".", "~$")) or p.lower() in SKIPPED_NAMES for p in parts)


@contextmanager
def entries(path: Path) -> Iterator[list[Entry]]:
    if path.is_dir():
        found = []
        for f in sorted(path.rglob("*")):
            rel = f.relative_to(path)
            # "X.zip" next to its extracted "X/" folder: the folder already has the files.
            unpacked = f.suffix.lower() == ".zip" and f.with_suffix("").is_dir()
            if f.is_file() and not _skipped(rel.parts) and not unpacked:
                found.append(
                    Entry(_nz_keep_case(rel.as_posix()), f.stat().st_size, lambda f=f: f.open("rb"))
                )
        yield found
    elif path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            yield [
                Entry(_nz_keep_case(i.filename), i.file_size, lambda i=i: zf.open(i))
                for i in zf.infolist()
                if not i.is_dir() and not _skipped(PurePosixPath(i.filename).parts)
            ]
    else:
        yield [Entry(_nz_keep_case(path.name), path.stat().st_size, lambda: path.open("rb"))]


def _nz_keep_case(name: str) -> str:
    return unicodedata.normalize("NFC", name)


def _sha256(entry: Entry) -> str:
    digest = hashlib.sha256()
    with entry.open() as fh:
        while chunk := fh.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class FilesReport:
    patterns: int = 0
    files_linked: int = 0  # created or replaced links (would be, in dry-run)
    files_unchanged: int = 0
    blobs_uploaded: int = 0
    bytes_uploaded: int = 0
    bytes_total: int = 0
    warnings: list[str] = field(default_factory=list)


class _FilesImporter:
    def __init__(self, source: Path, owner, refs: set[int] | None, dry_run: bool):
        self.source = source
        self.owner = owner
        self.dry_run = dry_run
        self.report = FilesReport()
        # shared location -> (every file name, names linked to a pattern)
        self.shared: dict[str, tuple[set[str], set[str]]] = {}
        legacy = Pattern.objects.filter(owner=owner, legacy_ref__isnull=False)
        self.location_count = Counter(legacy.values_list("legacy_location", flat=True))
        self.patterns = legacy.order_by("legacy_ref")
        if refs:
            self.patterns = self.patterns.filter(legacy_ref__in=refs)

    def run(self) -> FilesReport:
        for pattern in self.patterns:
            self.report.patterns += 1
            self._import_pattern(pattern)
        processed = Counter(p.legacy_location for p in self.patterns)
        for location, (every, linked) in self.shared.items():
            if processed[location] < self.location_count[location]:
                continue  # some patterns of this location were not part of this run
            if unlinked := sorted(every - linked):
                self.report.warnings.append(
                    f"{location}: {len(unlinked)} file(s) linked to no pattern: "
                    + ", ".join(unlinked)
                )
        return self.report

    def _warn(self, pattern: Pattern, message: str) -> None:
        self.report.warnings.append(f"line {pattern.legacy_ref + 1} ({pattern.name}): {message}")

    def _import_pattern(self, pattern: Pattern) -> None:
        location = pattern.legacy_location
        if not location.startswith(PATRONS_PREFIX):
            self._warn(pattern, f"location outside the Patrons folder, skipped: {location}")
            return
        path = resolve(self.source, location)
        if path is None:
            self._warn(pattern, f"location not found: {location}")
            return
        shared = self.location_count[location] > 1
        with entries(path) as found:
            if shared:
                _, linked = self.shared.setdefault(location, ({e.name for e in found}, set()))
                found = [e for e in found if matches_pattern(pattern.name, e.name)]
                linked.update(e.name for e in found)
                if not found:
                    self._warn(pattern, f"no file named after the pattern in shared {location}")
            for entry in found:
                self._import_entry(pattern, entry)

    def _import_entry(self, pattern: Pattern, entry: Entry) -> None:
        self.report.bytes_total += entry.size
        existing = PatternFile.objects.filter(pattern=pattern, original_name=entry.name).first()
        if self.dry_run:
            self.report.files_linked += existing is None
            self.report.files_unchanged += existing is not None
            return
        sha = _sha256(entry)
        if existing and existing.sha256 == sha:
            if existing.kind != (kind := file_kind(entry.name)):  # classification rules changed
                existing.kind = kind
                existing.save(update_fields=["kind"])
            self.report.files_unchanged += 1
            return
        name = blob_name(self.owner.pk, sha, entry.name)
        if not default_storage.exists(name):
            with entry.open() as fh:
                content = File(fh, name=PurePosixPath(entry.name).name)
                content.size = entry.size
                saved = default_storage.save(name, content)
            if saved != name:  # another process stored the same blob meanwhile
                default_storage.delete(saved)
            self.report.blobs_uploaded += 1
            self.report.bytes_uploaded += entry.size
        PatternFile.objects.update_or_create(
            pattern=pattern,
            original_name=entry.name,
            defaults={
                "file": name,
                "sha256": sha,
                "size": entry.size,
                "kind": file_kind(entry.name),
            },
        )
        self.report.files_linked += 1


def import_files(
    source: Path, owner, *, refs: set[int] | None = None, dry_run: bool = False
) -> FilesReport:
    return _FilesImporter(Path(source), owner, refs, dry_run).run()
