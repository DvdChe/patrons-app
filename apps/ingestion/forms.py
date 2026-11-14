import json
import re

from django import forms
from django.db import transaction
from django.utils.text import slugify

from apps.catalog import normalize as n
from apps.catalog.forms import PatternForm
from apps.catalog.models import (
    MEASURES,
    Brand,
    MeasureType,
    Pattern,
    PatternFile,
    SizeChart,
    SizeChartRow,
    Tag,
)

from . import previews
from .models import IngestionJob


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """A FileField cleaning a list of files (pattern from the Django documentation)."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        if isinstance(data, list | tuple):
            return [super(MultipleFileField, self).clean(d, initial) for d in data]
        return [super().clean(data, initial)]


class UploadForm(forms.Form):
    files = MultipleFileField(
        widget=MultipleFileInput(attrs={"accept": ".pdf,.zip,image/*"}),
        label="Fichiers du patron",
        help_text="Livret, planches, zip téléchargé tel quel, images (dessin technique…).",
    )


class ReviewForm(PatternForm):
    """Pattern form pre-filled from the proposal, plus what only exists when adding: a new
    brand, the thumbnail picked from a page, the size charts."""

    new_brand = forms.CharField(
        label="ou nouvelle marque", required=False, max_length=200, help_text=""
    )
    thumbnail_ref = forms.CharField(required=False, widget=forms.HiddenInput)  # "file:page"
    crop = forms.CharField(required=False, widget=forms.HiddenInput)  # "x0,y0,x1,y1"
    brand_charts = forms.CharField(required=False, widget=forms.HiddenInput)  # JSON ids
    size_chart = forms.CharField(required=False, widget=forms.HiddenInput)  # JSON rows

    class Meta(PatternForm.Meta):
        fields = [f for f in PatternForm.Meta.fields if f != "thumbnail"]

    def __init__(self, *args, job: IngestionJob, **kwargs):
        self.job = job
        super().__init__(*args, **kwargs)
        self.fields["brand"].required = False

    def clean(self):
        cleaned = super().clean()
        new_brand = cleaned.get("new_brand", "").strip()
        if not cleaned.get("brand") and not new_brand:
            self.add_error("brand", "Choisissez une marque ou saisissez-en une nouvelle.")
        cleaned["thumbnail_source"] = self._thumbnail_source(cleaned)
        cleaned["brand_chart_objects"] = self._brand_charts(cleaned)
        cleaned["chart_rows"] = self._chart_rows(cleaned.get("size_chart", ""))
        return cleaned

    def _thumbnail_source(self, cleaned):
        ref = cleaned.get("thumbnail_ref", "")
        if not ref:
            return None
        try:
            file_id, page = ref.split(":")
            source = self.job.files.get(pk=int(file_id))
            page = None if page in ("", "null") else int(page)
            crop = previews.parse_crop(cleaned.get("crop") or "0,0,1,1")
        except (ValueError, self.job.files.model.DoesNotExist):
            self.add_error(None, "Vignette invalide : choisissez-la à nouveau.")
            return None
        return source, page, crop

    def _brand_charts(self, cleaned):
        brand = cleaned.get("brand")
        try:
            ids = json.loads(cleaned.get("brand_charts") or "[]")
        except json.JSONDecodeError:
            ids = None
        if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
            self.add_error(None, "Sélection de tableaux de marque invalide.")
            return []
        if not ids:
            return []
        charts = list(SizeChart.objects.filter(pk__in=ids, brand=brand))
        if brand is None or len(charts) != len(set(ids)):
            self.add_error(None, "Ces tableaux de tailles n’appartiennent pas à la marque choisie.")
            return []
        return charts

    def _chart_rows(self, value):
        """Rows of the pattern's own body chart, as SizeChartRow field values."""
        try:
            rows = json.loads(value or "[]")
        except json.JSONDecodeError:
            rows = None
        if not isinstance(rows, list):
            self.add_error(None, "Tableau de tailles invalide.")
            return []
        out = []
        for i, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                self.add_error(None, "Tableau de tailles invalide.")
                return []
            label = str(row.get("label", "")).strip()
            values = {k: str(row.get(k) or "").strip() for k in MEASURES}
            if not any(values.values()):
                continue  # empty line, or a size label pre-filled but left without measures
            if not label:
                self.add_error(None, f"Tableau de tailles, ligne {i} : taille manquante.")
                continue
            measures = {}
            for k, raw in values.items():
                try:
                    measures[k] = n.measure_range(raw.replace(" ", ""))
                except ValueError:
                    self.add_error(
                        None, f"Tableau de tailles, taille {label} : « {raw} » invalide."
                    )
            if any(measures.values()):
                out.append({"label": label[:50], **measures})
        return out

    @transaction.atomic
    def save_pattern(self) -> Pattern:
        data = self.cleaned_data
        pattern = self.save(commit=False)
        pattern.owner = self.job.owner
        if not data.get("brand"):
            name = data["new_brand"].strip()
            pattern.brand = Brand.objects.filter(name__iexact=name).first() or Brand.objects.create(
                name=name
            )
        pattern.save()
        self.save_m2m()
        pattern.brand_size_charts.set(data["brand_chart_objects"])
        if data["chart_rows"]:
            chart = SizeChart.objects.create(
                pattern=pattern, measure_type=MeasureType.BODY, note="Saisi à l’ajout du patron"
            )
            SizeChartRow.objects.bulk_create(
                SizeChartRow(chart=chart, position=i, **row)
                for i, row in enumerate(data["chart_rows"])
            )
        PatternFile.objects.bulk_create(
            PatternFile(
                pattern=pattern,
                file=f.file.name,
                original_name=f.original_name,
                kind=f.kind,
                sha256=f.sha256,
                size=f.size,
            )
            for f in self.job.files.all()
        )
        if source := data["thumbnail_source"]:
            f, page, crop = source
            name = f"{slugify(pattern.name)[:40] or 'vignette'}.jpg"
            pattern.thumbnail.save(name, previews.thumbnail(f, page, crop, name), save=False)
        pattern.save()  # thumbnail and search text (tags are saved now)
        self.job.status = IngestionJob.Status.DONE
        self.job.pattern = pattern
        self.job.save(update_fields=["status", "pattern"])
        return pattern


def initial_from_proposal(proposal: dict) -> dict:
    keys = [
        "name", "garment_type", "family", "audience", "size_range", "difficulty",
        "difficulty_raw", "seam_allowance", "seam_allowance_detail", "seam_allowance_source",
        "fabrics_raw", "fabric_kind",
    ]  # fmt: skip
    initial = {k: proposal[k] for k in keys if proposal.get(k)}
    if proposal.get("brand"):
        initial["brand"] = proposal["brand"]
    elif proposal.get("new_brand"):
        initial["new_brand"] = proposal["new_brand"]
    initial["brand_confidence"] = "certain" if proposal.get("brand") else "unknown"

    return initial


def suggested_tag_ids(proposal: dict) -> set[int]:
    """Tags found in the booklet: highlighted on the review form, not checked (about 2 in 3
    are right, see evaluate_extraction)."""
    wanted = {tuple(t.split("|", 1)) for t in proposal.get("tags", [])}
    return {t.pk for t in Tag.objects.all() if (t.group, t.value) in wanted}


def size_labels(size_range: str) -> list[str]:
    """Rows to pre-fill the size grid with: "34 – 48" -> 34, 36, ..., 48."""
    bounds = [int(v) for v in re.findall(r"\d{2}", size_range)[:2]]
    if len(bounds) == 2 and 30 <= bounds[0] < bounds[1] <= 64:
        return [str(v) for v in range(bounds[0], bounds[1] + 1, 2)]
    return []


def covers(chart: SizeChart, size_range: str) -> bool:
    """True if the brand chart has every size of the pattern's numeric size range."""
    wanted = set(size_labels(size_range))
    labels = {str(r.label).split()[0] for r in chart.rows.all()}
    return bool(wanted) and wanted <= labels
