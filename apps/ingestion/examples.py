"""Worked examples for the LLM: known patterns of the same brand, with their booklet passages
and stored answers (see llm.TASKS)."""

import hashlib

from apps.catalog.models import Pattern, PatternFile

from .documents import DocumentText, SourceFile, read_text
from .llm import TASKS, Examples
from .models import BookletText

EXAMPLES_PER_TASK = 2
MAX_PATTERNS = 6  # patterns read per brand, to bound the work for a large library
MAX_PDF_BYTES = 80 * 1024 * 1024
READ_KINDS = {PatternFile.Kind.BOOKLET, PatternFile.Kind.OTHER}  # never the big pieces


def pattern_text(pattern: Pattern) -> DocumentText | None:
    """Booklet text of a pattern, from the cache or read from its files (then cached)."""
    pdfs = [
        f
        for f in pattern.files.all()
        if f.original_name.lower().endswith(".pdf")
        and f.kind in READ_KINDS
        and f.size <= MAX_PDF_BYTES
    ]
    if not pdfs:
        return None
    key = hashlib.sha256("".join(sorted(f.sha256 for f in pdfs)).encode()).hexdigest()
    if cached := BookletText.objects.filter(key=key).first():
        return DocumentText(text=cached.text, opening=cached.opening)
    sources = []
    for f in pdfs:
        with f.file.open("rb") as fh:
            sources.append(SourceFile(f.original_name, fh.read()))
    doc = read_text(sources)
    BookletText.objects.get_or_create(key=key, defaults={"text": doc.text, "opening": doc.opening})
    return doc


def brand_examples(owner, brand_id: int, exclude: int | None = None) -> Examples:
    """Up to two examples per field task from the owner's patterns of this brand, recently
    edited first (more likely checked by a person)."""
    patterns = (
        Pattern.objects.filter(owner=owner, brand_id=brand_id, files__isnull=False)
        .exclude(pk=exclude)
        .distinct()
        .order_by("-updated_at")
        .prefetch_related("files")[:MAX_PATTERNS]
    )
    texts: dict[int, DocumentText | None] = {}
    examples: Examples = {}
    for task in TASKS:
        for pattern in patterns:
            if len(examples.get(task.name, [])) >= EXAMPLES_PER_TASK:
                break
            answer = task.example(pattern)
            if answer is None:
                continue
            if pattern.pk not in texts:
                texts[pattern.pk] = pattern_text(pattern)
            doc = texts[pattern.pk]
            passage = task.source(doc.text, doc.opening).strip() if doc else ""
            if passage:
                examples.setdefault(task.name, []).append((passage, answer))
    return examples
