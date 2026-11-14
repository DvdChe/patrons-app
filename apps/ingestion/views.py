import json

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.catalog.models import MEASURES, Brand, MeasureType, Season
from apps.catalog.presenters import MEASURE_LABELS, measure_range
from apps.catalog.sizing import Chart

from . import previews
from .analysis import check_upload, create_job
from .forms import (
    ReviewForm,
    UploadForm,
    covers,
    initial_from_proposal,
    size_labels,
    suggested_tag_ids,
)
from .models import IngestionJob

PREVIEW_PX = 360


def _job(request, pk: int) -> IngestionJob:
    return get_object_or_404(IngestionJob, pk=pk, owner=request.user)


@login_required
@require_http_methods(["GET", "POST"])
def upload(request):
    form = UploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            for f in form.cleaned_data["files"]:
                check_upload(f)
            job = create_job(request.user, form.cleaned_data["files"])
        except ValidationError as e:
            form.add_error("files", e)
        else:
            return redirect("ingestion:review", job.pk)
    pending = IngestionJob.objects.filter(
        owner=request.user, status=IngestionJob.Status.ANALYSED
    ).prefetch_related("files")
    return render(request, "ingestion/upload.html", {"form": form, "pending": pending})


# Where each pre-filled value comes from, shown next to the field.
SOURCES = {
    "name": "d’après le nom des fichiers",
    "brand": "détectée",
    "new_brand": "lue dans le PDF (marque inconnue)",
    "garment_type": "à vérifier",
    "family": "à vérifier",
    "audience": "à vérifier",
    "size_range": "lu dans le livret",
    "difficulty_raw": "lu dans le livret",
    "difficulty": "lu dans le livret",
    "fabrics_raw": "lu dans le livret",
    "fabric_kind": "lu dans le livret",
}


@login_required
@require_http_methods(["GET", "POST"])
def review(request, pk: int):
    job = _job(request, pk)
    if job.status == IngestionJob.Status.DONE and job.pattern_id:
        return redirect(f"/#pattern-{job.pattern_id}")
    proposal = job.proposal
    form = ReviewForm(request.POST or None, job=job, initial=initial_from_proposal(proposal))
    if request.method == "POST" and form.is_valid():
        pattern = form.save_pattern()
        return redirect(f"/#pattern-{pattern.pk}")
    empty = (None, "", "unspecified", "unknown", "unclassified")
    sources = {k: v for k, v in SOURCES.items() if proposal.get(k) not in empty}
    if proposal.get("seam_allowance") not in (None, "", "unknown"):
        seam = {"brand_rule": "règle de la marque", "pieces": "lu sur les planches"}.get(
            proposal["seam_allowance_source"], "lu dans le livret"
        )
        sources |= {"seam_allowance": seam, "seam_allowance_detail": seam}
    if proposal.get("tags"):
        sources["tags"] = "suggestions du livret en couleur : cliquez pour les retenir"
    yardage = proposal.get("yardage_hint")
    season_labels = [str(Season(v).label) for v in proposal.get("seasons_hint", [])]
    context = {
        "job": job,
        "form": form,
        "sources": sources,
        "files": job.files.all(),
        "yardage_hint": f"Trouvé dans le livret (à vérifier) : « {yardage} »" if yardage else "",
        "seasons_hint": "Le livret mentionne : " + ", ".join(season_labels)
        if season_labels
        else "",
        "config": _review_config(request, job),
        "suggested_tags": suggested_tag_ids(proposal),
    }
    return render(request, "ingestion/review.html", context)


def _review_config(request, job: IngestionJob) -> dict:
    """State of the review page widgets; after a failed POST, the submitted state."""
    posted = request.POST if request.method == "POST" else None
    candidates = []
    for c in job.thumbnail_candidates:
        page = c["page"]
        args = (job.pk, c["file"]) if page is None else (job.pk, c["file"], page)
        url = reverse("ingestion:preview_image" if page is None else "ingestion:preview", args=args)
        candidates.append(
            {
                "ref": f"{c['file']}:{'' if page is None else page}",
                "small": f"{url}?px=240",
                "large": f"{url}?px=900",
            }
        )

    def posted_json(name, default):
        try:
            return json.loads(posted.get(name) or "null") if posted else default
        except json.JSONDecodeError:
            return default

    labels = size_labels(job.proposal.get("size_range", ""))
    return {
        "candidates": candidates,
        "thumbRef": posted.get("thumbnail_ref", "") if posted else "",
        "crop": posted.get("crop", "") if posted else "",
        "measures": [{"key": k, "label": MEASURE_LABELS[k]} for k in MEASURES],
        "rows": posted_json("size_chart", None)
        or [{"label": label} for label in labels]
        or [{"label": ""}],
        "chosenCharts": posted_json("brand_charts", None),  # None: pre-check covering charts
    }


@login_required
@require_POST
def discard(request, pk: int):
    _job(request, pk).delete()
    return redirect("ingestion:upload")


@login_required
@require_GET
def preview(request, pk: int, file_id: int, page: int | None = None):
    """JPEG of an uploaded image (no page) or of one PDF page, for the thumbnail picker."""
    f = get_object_or_404(_job(request, pk).files, pk=file_id)
    size = min(int(request.GET.get("px", PREVIEW_PX)), previews.MAX_RENDER_PX)
    try:
        img = previews.render(f, page, size)
    except (ValueError, OSError):
        return HttpResponse(status=404)
    response = HttpResponse(previews.jpeg(img), content_type="image/jpeg")
    response["Cache-Control"] = "private, max-age=3600"
    return response


@login_required
@require_GET
def brand_charts(request, pk: int):
    """Size charts of a brand, for the review screen; `covers` tells whether a chart has every
    size of the `sizes` range given."""
    brand = get_object_or_404(Brand, pk=pk)
    size_range = request.GET.get("sizes", "")
    out = []
    for chart in brand.size_charts.prefetch_related("rows"):
        converted = Chart.from_model(chart)
        out.append(
            {
                "id": chart.pk,
                "body": chart.measure_type == MeasureType.BODY,
                "type": chart.get_measure_type_display(),
                "note": chart.note,
                "labels": converted.labels,
                "measures": [MEASURE_LABELS[k] for k in converted.measures],
                "rows": [
                    [label] + [measure_range(converted.measures[k][i]) for k in converted.measures]
                    for i, label in enumerate(converted.labels)
                ],
                "covers": covers(chart, size_range),
            }
        )
    return JsonResponse({"charts": out})
