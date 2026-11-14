"""Turn an upload into an ingestion job: store the files, extract a proposal, list the
thumbnail candidates. Runs in the request for now (a few seconds); `analyse()` is the unit to
move to a background task if it ever gets too slow."""

import io
import zipfile
from contextlib import closing
from pathlib import PurePosixPath

import pypdfium2 as pdfium
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.catalog.files import IMAGE_SUFFIXES, Entry, file_kind, is_junk, nfc, store_blob
from apps.catalog.models import Brand, PatternFile, SeamAllowance

from .documents import SourceFile
from .models import IngestionFile, IngestionJob
from .pipeline import brand_table, propose

ACCEPTED_SUFFIXES = {".pdf", ".zip"} | IMAGE_SUFFIXES
MAX_UPLOAD_BYTES = 1024 * 1024 * 1024  # per upload, before unpacking
MAX_UNPACKED_BYTES = 3 * 1024 * 1024 * 1024  # zip bomb guard
MAX_ZIP_MEMBERS = 1000
MAX_ANALYSED_PDF_BYTES = 80 * 1024 * 1024
THUMBNAIL_PAGES_PER_PDF = 6
MAX_THUMBNAIL_CANDIDATES = 18


class _Reader:
    """Re-readable view of an uploaded file: `with` rewinds it and never closes it (closing a
    temporary upload deletes it)."""

    def __init__(self, upload):
        self.upload = upload

    def __enter__(self):
        self.upload.seek(0)
        return self.upload

    def __exit__(self, *exc):
        return False


def check_upload(upload) -> None:
    suffix = PurePosixPath(upload.name).suffix.lower()
    if suffix not in ACCEPTED_SUFFIXES:
        raise ValidationError(f"{upload.name} : format non pris en charge (PDF, zip ou image).")
    if upload.size > MAX_UPLOAD_BYTES:
        raise ValidationError(f"{upload.name} : fichier trop lourd (1 Go maximum).")


def _entries(upload, archives: list[zipfile.ZipFile]) -> list[Entry]:
    if not upload.name.lower().endswith(".zip"):
        return [Entry(nfc(PurePosixPath(upload.name).name), upload.size, lambda: _Reader(upload))]
    try:
        zf = zipfile.ZipFile(upload)
    except zipfile.BadZipFile:
        raise ValidationError(f"{upload.name} : archive zip illisible.") from None
    archives.append(zf)
    members = [
        i for i in zf.infolist() if not i.is_dir() and not is_junk(PurePosixPath(i.filename).parts)
    ]
    if len(members) > MAX_ZIP_MEMBERS or sum(i.file_size for i in members) > MAX_UNPACKED_BYTES:
        raise ValidationError(f"{upload.name} : archive trop volumineuse.")
    return [Entry(nfc(i.filename), i.file_size, lambda i=i: zf.open(i)) for i in members]


def create_job(owner, uploads) -> IngestionJob:
    """Store the uploaded files (zip members unpacked) and analyse them."""
    archives: list[zipfile.ZipFile] = []
    try:
        entries = [e for upload in uploads for e in _entries(upload, archives)]
        if not entries:
            raise ValidationError("Aucun fichier exploitable dans l’envoi.")
        with transaction.atomic():
            job = IngestionJob.objects.create(owner=owner)
            names: set[str] = set()
            for entry in entries:
                name = entry.name
                while name in names:  # same name in two uploads
                    stem = PurePosixPath(name)
                    name = str(stem.with_name(f"{stem.stem} (bis){stem.suffix}"))
                names.add(name)
                blob, digest, _ = store_blob(owner.pk, entry)
                IngestionFile.objects.create(
                    job=job,
                    file=blob,
                    original_name=name,
                    kind=file_kind(name),
                    sha256=digest,
                    size=entry.size,
                )
    finally:
        for zf in archives:
            zf.close()
    analyse(job)
    return job


def _brand_rules() -> dict[int, tuple[str, str]]:
    return {
        b.pk: (b.seam_allowance, b.seam_allowance_detail)
        for b in Brand.objects.exclude(seam_allowance=SeamAllowance.UNKNOWN)
    }


def analyse(job: IngestionJob) -> None:
    files = list(job.files.all())
    sources = []
    pdf_data: dict[int, bytes] = {}
    for f in files:
        data = b""
        if f.original_name.lower().endswith(".pdf") and f.size <= MAX_ANALYSED_PDF_BYTES:
            with f.file.open("rb") as fh:
                data = fh.read()
            pdf_data[f.pk] = data
        sources.append(SourceFile(f.original_name, data))
    # The names users give their uploads carry the pattern name: zips first, then booklets.
    sources.sort(key=lambda s: (not s.name.endswith(".zip"), file_kind(s.name) != "booklet"))

    p = propose(sources, brand_table(), _brand_rules())
    job.proposal = {
        "name": p.name,
        "brand": p.brand_id,
        "garment_type": p.garment_type,
        "family": p.family,
        "audience": p.audience,
        "size_range": p.size_range,
        "difficulty": p.difficulty,
        "difficulty_raw": p.difficulty_raw,
        "seam_allowance": p.seam_allowance,
        "seam_allowance_detail": p.seam_allowance_detail,
        "seam_allowance_source": p.seam_allowance_source,
        "fabrics_raw": p.fabrics_raw,
        "fabric_kind": p.fabric_kind,
        # Not reliable enough to pre-fill (see evaluate_extraction): shown as a hint only.
        "yardage_hint": p.yardage_raw,
        "ocr_pages": p.document.ocr_pages,
        "unreadable": list(p.document.unreadable),
    }
    job.thumbnail_candidates = _thumbnail_candidates(files, pdf_data)
    job.save(update_fields=["proposal", "thumbnail_candidates"])


def _thumbnail_candidates(files: list[IngestionFile], pdf_data: dict[int, bytes]) -> list[dict]:
    """Images first (often the technical drawing), then the first pages of the booklets and
    other documents. Pattern pieces are skipped."""
    out = [{"file": f.pk, "page": None} for f in files if f.kind == PatternFile.Kind.IMAGE]
    ordered = sorted(
        (f for f in files if f.pk in pdf_data and f.kind != PatternFile.Kind.PIECES),
        key=lambda f: f.kind != PatternFile.Kind.BOOKLET,
    )
    for f in ordered:
        try:
            pdf = pdfium.PdfDocument(io.BytesIO(pdf_data[f.pk]))
        except pdfium.PdfiumError:
            continue
        with closing(pdf):
            out += [
                {"file": f.pk, "page": i} for i in range(min(THUMBNAIL_PAGES_PER_PDF, len(pdf)))
            ]
    return out[:MAX_THUMBNAIL_CANDIDATES]
