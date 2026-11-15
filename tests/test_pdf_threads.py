"""PDFium is not thread-safe: the dev server and Granian serve page previews from several
threads at once, which used to crash the process (heap corruption). Without the lock this test
crashes the test run or raises; with it, every render succeeds."""

import io
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from apps.ingestion import previews
from apps.ingestion.documents import SourceFile, read_text

from .pdfs import make_pdf

PDF = make_pdf([[f"Page {i} " + "texte " * 200] for i in range(6)])


class _File:
    def open(self, mode):
        return io.BytesIO(PDF)


def test_concurrent_renders_and_reads_do_not_crash():
    f = SimpleNamespace(file=_File())

    def render(i):
        return previews.render(f, i % 6, 600).size

    def read(_):
        return read_text([SourceFile("Livret.pdf", PDF)]).pages

    with ThreadPoolExecutor(max_workers=8) as pool:
        sizes = list(pool.map(render, range(200)))
        pages = list(pool.map(read, range(50)))

    assert len(set(sizes)) == 1
    assert len(set(pages)) == 1 and pages[0] > 0
