"""Text of the PDFs of a pattern: embedded text first, OCR (tesseract) for scanned pages."""

import io
import logging
import subprocess
from dataclasses import dataclass

import pypdfium2 as pdfium

from apps.catalog.files import file_kind
from apps.catalog.models import PatternFile

log = logging.getLogger(__name__)

# Pages read per PDF kind: booklets hold the useful information, pieces sometimes a title
# block with the seam allowance. Pieces are never OCR'd (huge A0 pages).
PAGES = {PatternFile.Kind.BOOKLET: 10, PatternFile.Kind.OTHER: 4, PatternFile.Kind.PIECES: 2}
MAX_OCR_PAGES = 6
MIN_PAGE_CHARS = 40  # below this, a page is considered scanned
MAX_OCR_AREA = 842 * 1191  # A3 in PDF points: larger pages are pattern pieces
OCR_DPI = 200
OCR_TIMEOUT = 60


@dataclass
class SourceFile:
    name: str
    data: bytes


@dataclass
class DocumentText:
    text: str = ""
    first_page: str = ""
    pages: int = 0
    ocr_pages: int = 0
    unreadable: tuple[str, ...] = ()


def ocr_image(image) -> str:
    buf = io.BytesIO()
    image.save(buf, "PNG")
    result = subprocess.run(
        ["tesseract", "stdin", "stdout", "-l", "fra+eng"],
        input=buf.getvalue(),
        capture_output=True,
        timeout=OCR_TIMEOUT,
        check=False,
    )
    return result.stdout.decode("utf-8", "replace")


def _ordered_pdfs(files: list[SourceFile]) -> list[tuple[SourceFile, PatternFile.Kind]]:
    order = [PatternFile.Kind.BOOKLET, PatternFile.Kind.OTHER, PatternFile.Kind.PIECES]
    pdfs = [(f, file_kind(f.name)) for f in files if f.name.lower().endswith(".pdf")]
    return sorted(pdfs, key=lambda p: order.index(p[1]))


def read_text(files: list[SourceFile]) -> DocumentText:
    out = DocumentText()
    chunks: list[str] = []
    unreadable = []
    for source, kind in _ordered_pdfs(files):
        try:
            pdf = pdfium.PdfDocument(source.data)
        except pdfium.PdfiumError:
            unreadable.append(source.name)
            continue
        try:
            for i in range(min(PAGES[kind], len(pdf))):
                page = pdf[i]
                text = page.get_textpage().get_text_range()
                width, height = page.get_size()
                if (
                    len(text.strip()) < MIN_PAGE_CHARS
                    and kind != PatternFile.Kind.PIECES
                    and width * height <= MAX_OCR_AREA
                    and out.ocr_pages < MAX_OCR_PAGES
                ):
                    image = page.render(scale=OCR_DPI / 72).to_pil()
                    text = ocr_image(image)
                    out.ocr_pages += 1
                out.pages += 1
                if not out.first_page and text.strip():
                    out.first_page = text
                chunks.append(text)
        finally:
            pdf.close()
    out.text = "\n".join(chunks)
    out.unreadable = tuple(unreadable)
    return out
