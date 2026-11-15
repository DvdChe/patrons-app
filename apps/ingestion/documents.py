"""Text of the PDFs of a pattern: embedded text first, OCR (tesseract) for scanned pages."""

import io
import logging
import re
import subprocess
from dataclasses import dataclass

import pypdfium2 as pdfium

from apps.catalog.files import file_kind
from apps.catalog.models import PatternFile

from .pdf import LOCK

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


def clean(text: str) -> str:
    """PDFium text: CRLF line ends, U+FFFE (or soft hyphen) where a word was hyphenated at the
    end of a line ("mou\ufffe\r\nvement"). Rejoin those words."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"[\ufffe\u00ad]\n?", "", text)


@dataclass
class DocumentText:
    text: str = ""
    opening: str = ""  # first two pages with text: title, presentation
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
    opening_pages: list[str] = []
    unreadable = []
    for source, kind in _ordered_pdfs(files):
        with LOCK:
            try:
                pdf = pdfium.PdfDocument(source.data)
            except pdfium.PdfiumError:
                unreadable.append(source.name)
                continue
        try:
            for i in range(min(PAGES[kind], len(pdf))):
                image = None
                with LOCK:
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
                if image is not None:  # OCR is slow and does not touch PDFium: unlocked
                    text = ocr_image(image)
                    out.ocr_pages += 1
                out.pages += 1
                text = clean(text)
                if text.strip() and len(opening_pages) < 2:
                    opening_pages.append(text)
                chunks.append(text)
        finally:
            with LOCK:
                pdf.close()
    out.text = "\n".join(chunks)
    out.opening = "\n".join(opening_pages)
    out.unreadable = tuple(unreadable)
    return out
