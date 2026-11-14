import io

from django import forms
from django.core.files.base import ContentFile
from django.utils.text import slugify
from PIL import Image

from . import normalize as n
from .models import Pattern, Season

THUMBNAIL_MAX_SIDE = 800  # px, as in the MVP (portrait 3:4 cards)
THUMBNAIL_MAX_UPLOAD = 15 * 1024 * 1024

# raw text field -> (normalized field, function deriving it from the raw text)
DERIVED_FIELDS = {
    "garment_type": ("family", n.family),
    "difficulty_raw": ("difficulty", n.difficulty),
    "fabrics_raw": ("fabric_kind", n.fabric_kind),
    "yardage_raw": ("yardage_max_m", n.yardage_max),
}


class PatternForm(forms.ModelForm):
    seasons = forms.MultipleChoiceField(
        choices=Season.choices, required=False, widget=forms.CheckboxSelectMultiple
    )

    class Meta:
        model = Pattern
        fields = [
            "name",
            "brand",
            "brand_confidence",
            "garment_type",
            "family",
            "audience",
            "seasons",
            "seasons_inferred",
            "yardage_raw",
            "yardage_max_m",
            "fabrics_raw",
            "fabric_kind",
            "size_range",
            "difficulty_raw",
            "difficulty",
            "seam_allowance",
            "seam_allowance_detail",
            "seam_allowance_source",
            "tags",
            "tags_source",
            "thumbnail",
            "sewn",
            "notes",
            "remarks",
        ]
        widgets = {
            "tags": forms.CheckboxSelectMultiple,
            "fabrics_raw": forms.Textarea(attrs={"rows": 2}),
            "notes": forms.Textarea(attrs={"rows": 4}),
            "remarks": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Checkboxes are shown under their group title: label them with the value only.
        self.fields["tags"].label_from_instance = lambda tag: tag.value

    def clean_thumbnail(self):
        """Re-encode uploads as an 800 px JPEG: consistent cards, no metadata kept."""
        upload = self.cleaned_data.get("thumbnail")
        if not upload or upload == self.instance.thumbnail or not hasattr(upload, "content_type"):
            return upload  # unchanged, or cleared (False)
        if upload.size > THUMBNAIL_MAX_UPLOAD:
            raise forms.ValidationError("Image trop lourde (15 Mo maximum).")
        with Image.open(upload) as img:
            img = img.convert("RGB")
            img.thumbnail((THUMBNAIL_MAX_SIDE, THUMBNAIL_MAX_SIDE))
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=88)
        name = slugify(self.cleaned_data.get("name") or "vignette")[:40] or "vignette"
        return ContentFile(buf.getvalue(), name=f"{name}.jpg")

    def clean(self):
        cleaned = super().clean()
        # A raw value edited without touching its normalized value re-derives it, so the
        # filters follow the text. An explicit normalized value always wins.
        for raw, (target, derive) in DERIVED_FIELDS.items():
            if raw in self.changed_data and target not in self.changed_data and raw in cleaned:
                cleaned[target] = derive(cleaned[raw])
        return cleaned

    def tag_groups(self):
        """Tag checkboxes grouped by tag group, in taxonomy order, for the template."""
        groups: dict[str, list] = {}
        for widget in self["tags"]:
            groups.setdefault(widget.data["value"].instance.group, []).append(widget)
        return list(groups.items())
