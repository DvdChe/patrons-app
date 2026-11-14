"""Page previews and thumbnail cropping for the review screen."""

import io

import pypdfium2 as pdfium
from django.core.files.base import ContentFile
from PIL import Image

from .models import IngestionFile

MAX_RENDER_PX = 2000  # longest side; bounds memory whatever the page size
THUMBNAIL_PX = 800
MIN_CROP = 0.05  # a crop keeps at least 5 % of the width and height


def render(f: IngestionFile, page: int | None, longest_side: int) -> Image.Image:
    """The image file itself, or one PDF page, scaled so its longest side fits."""
    longest_side = min(longest_side, MAX_RENDER_PX)
    with f.file.open("rb") as fh:
        data = fh.read()
    if page is None:
        img = Image.open(io.BytesIO(data))
        img.load()
        img = img.convert("RGB")
        img.thumbnail((longest_side, longest_side))
        return img
    pdf = pdfium.PdfDocument(io.BytesIO(data))
    try:
        if not 0 <= page < len(pdf):
            raise ValueError(f"page {page} out of range")
        p = pdf[page]
        width, height = p.get_size()
        return p.render(scale=longest_side / max(width, height)).to_pil().convert("RGB")
    finally:
        pdf.close()


def jpeg(img: Image.Image, quality: int = 85) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def parse_crop(value: str) -> tuple[float, float, float, float]:
    """ "x0,y0,x1,y1" as fractions of the image (0..1)."""
    try:
        x0, y0, x1, y1 = (float(v) for v in value.split(","))
    except ValueError:
        raise ValueError("invalid crop") from None
    x0, y0 = max(0.0, x0), max(0.0, y0)
    x1, y1 = min(1.0, x1), min(1.0, y1)
    if x1 - x0 < MIN_CROP or y1 - y0 < MIN_CROP:
        raise ValueError("crop too small")
    return x0, y0, x1, y1


def thumbnail(
    f: IngestionFile, page: int | None, crop: tuple[float, float, float, float], name: str
) -> ContentFile:
    img = render(f, page, MAX_RENDER_PX)
    w, h = img.size
    x0, y0, x1, y1 = crop
    img = img.crop((round(x0 * w), round(y0 * h), round(x1 * w), round(y1 * h)))
    img.thumbnail((THUMBNAIL_PX, THUMBNAIL_PX))
    return ContentFile(jpeg(img, 88), name=name)
