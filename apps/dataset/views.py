from collections import Counter

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.catalog.models import Brand, Season, Tag

from .fields import BY_NAME, CHECK, FIELDS
from .models import AuditRecord, AuditSample
from .sampling import create_sample

DEFAULT_SIZE = 50


@login_required
@require_GET
def index(request):
    samples = AuditSample.objects.filter(owner=request.user).select_related("pattern")
    done = sum(s.done for s in samples)
    following = next((s for s in samples if not s.done), None)
    context = {"samples": samples, "done": done, "next": following, "size": DEFAULT_SIZE}
    return render(request, "dataset/index.html", context)


@login_required
@require_POST
def start(request):
    size = min(max(int(request.POST.get("size") or DEFAULT_SIZE), 1), 200)
    create_sample(request.user, size)
    return redirect("dataset:index")


def _choices(field):
    if field.kind == "choice":
        return [(c.value, c.label) for c in field.choices]
    if field.kind == "seasons":
        return [(c.value, c.label) for c in Season]
    return []


@login_required
@require_http_methods(["GET", "POST"])
def audit(request, pk: int):
    sample = get_object_or_404(
        AuditSample.objects.select_related("pattern__brand"), pk=pk, owner=request.user
    )
    pattern = sample.pattern
    records = {r.field: r for r in sample.records.all()}
    errors = {}
    if request.method == "POST":
        verdicts, corrections = {}, {}
        for field in FIELDS:
            verdict = request.POST.get(f"verdict-{field.name}")
            if verdict not in AuditRecord.Verdict.values:
                errors[field.name] = "Choisissez ✓, ✗ ou ?"
                continue
            verdicts[field.name] = verdict
            if verdict == AuditRecord.Verdict.WRONG and field.kind != CHECK:
                try:
                    corrections[field.name] = field.parse(request.POST)
                except ValueError as e:
                    errors[field.name] = f"Correction invalide : {e}"
        if not errors:
            with transaction.atomic():
                for name, verdict in verdicts.items():
                    field = BY_NAME[name]
                    stored = records[name].stored if name in records else field.stored(pattern)
                    AuditRecord.objects.update_or_create(
                        sample=sample,
                        field=name,
                        defaults={
                            "verdict": verdict,
                            "stored": stored,
                            "corrected": corrections.get(name),
                        },
                    )
                for name, value in corrections.items():
                    BY_NAME[name].apply(pattern, value)
                pattern.save()  # search text, tags included
                sample.done = True
                sample.save(update_fields=["done"])
            following = AuditSample.objects.filter(owner=request.user, done=False).first()
            return (
                redirect("dataset:audit", following.pk) if following else redirect("dataset:report")
            )

    rows = []
    for field in FIELDS:
        record = records.get(field.name)
        stored = record.stored if record else field.stored(pattern)
        current = field.stored(pattern)
        rows.append(
            {
                "field": field,
                "stored": field.display(stored),
                "current": current,
                "verdict": request.POST.get(f"verdict-{field.name}")
                or (record.verdict if record else ""),
                "choices": _choices(field),
                "error": errors.get(field.name, ""),
            }
        )
    context = {
        "sample": sample,
        "pattern": pattern,
        "rows": rows,
        "files": [
            f
            for f in pattern.files.all()
            if f.original_name.lower().endswith((".pdf", ".jpg", ".jpeg", ".png"))
        ],
        "brands": Brand.objects.all(),
        "tag_groups": _tag_groups({(t.group, t.value) for t in pattern.tags.all()}),
        "total": AuditSample.objects.filter(owner=request.user).count(),
    }
    return render(request, "dataset/audit.html", context)


def _tag_groups(current: set[tuple[str, str]]):
    """[(group, [(tag, checked), ...]), ...] in taxonomy order."""
    groups: dict[str, list] = {}
    for tag in Tag.objects.all():
        groups.setdefault(tag.group, []).append((tag, (tag.group, tag.value) in current))
    return list(groups.items())


@login_required
@require_GET
def report(request):
    records = AuditRecord.objects.filter(sample__owner=request.user)
    counts = Counter((r.field, r.verdict) for r in records)
    rows = []
    for field in FIELDS:
        ok = counts[(field.name, "ok")]
        wrong = counts[(field.name, "wrong")]
        unknown = counts[(field.name, "unknown")]
        judged = ok + wrong
        rows.append(
            {
                "label": field.label,
                "ok": ok,
                "wrong": wrong,
                "unknown": unknown,
                "accuracy": f"{ok / judged:.0%}" if judged else "—",
            }
        )
    audited = AuditSample.objects.filter(owner=request.user, done=True).count()
    return render(request, "dataset/report.html", {"rows": rows, "audited": audited})
