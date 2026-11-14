"""Catalogue schema.

Design rules:
- Patterns, their files and size charts, and profiles are private to their `owner`.
- Reference data is shared by all users and curated by staff: brands (with their rules and
  brand size charts) and the detail tag taxonomy.
- Values used for filtering are stored normalized (enums, numbers, ranges) at write time.
  The free text found in the pattern booklet is kept next to them (`*_raw` / `*_detail`).
- Primary keys are stable IDs, never the position of a row.
"""

from pathlib import PurePosixPath

from django.conf import settings
from django.contrib.postgres.fields import ArrayField, DecimalRangeField
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _

from .text import normalize

FR_COLLATION = "fr-x-icu"  # ICU collation shipped with PostgreSQL: French alphabetical order


class OwnedModel(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")

    class Meta:
        abstract = True


class Family(models.TextChoices):
    DRESSES = "dresses", _("Robes")
    TOPS = "tops", _("Hauts")
    BOTTOMS = "bottoms", _("Bas")
    JUMPSUITS = "jumpsuits", _("Combinaisons et salopettes")
    OUTERWEAR = "outerwear", _("Vestes et manteaux")
    KNITWEAR = "knitwear", _("Pulls et sweats")
    LINGERIE = "lingerie", _("Lingerie, maillots et pyjamas")
    SETS = "sets", _("Ensembles")
    ACCESSORIES = "accessories", _("Accessoires et sacs")
    RESOURCES = "resources", _("Ressources")
    UNCLASSIFIED = "unclassified", _("Non classé")


class Audience(models.TextChoices):
    WOMEN = "women", _("Femme")
    MEN = "men", _("Homme")
    UNISEX = "unisex", _("Mixte")
    KIDS = "kids", _("Enfant")
    UNSPECIFIED = "unspecified", _("À préciser")


class Season(models.TextChoices):
    SUMMER = "summer", _("Été")
    MIDSEASON = "midseason", _("Mi-saison")
    WINTER = "winter", _("Hiver")
    ALL = "all", _("Toutes saisons")


class FabricKind(models.TextChoices):
    WOVEN = "woven", _("Chaîne et trame")
    KNIT = "knit", _("Maille")
    BOTH = "both", _("Les deux")
    UNSPECIFIED = "unspecified", _("Non précisé")


class Difficulty(models.TextChoices):
    BEGINNER = "beginner", _("Débutant")
    INTERMEDIATE = "intermediate", _("Intermédiaire")
    ADVANCED = "advanced", _("Avancé")
    UNSPECIFIED = "unspecified", _("Non précisé")


class SeamAllowance(models.TextChoices):
    INCLUDED = "included", _("Comprises")
    NOT_INCLUDED = "not_included", _("Non comprises")
    UNKNOWN = "unknown", _("Non renseigné")


class InfoSource(models.TextChoices):
    BOOKLET = "booklet", _("Livret")
    PIECES = "pieces", _("Planches")
    BRAND_RULE = "brand_rule", _("Règle de la marque")
    UNKNOWN = "unknown", _("Inconnue")


class BrandConfidence(models.TextChoices):
    CERTAIN = "certain", _("Certaine")
    PROBABLE = "probable", _("Probable")
    UNKNOWN = "unknown", _("À identifier")


class Brand(models.Model):
    """Shared reference data (not owned): one brand list, curated by staff, used by all users
    for brand recognition and brand rules (seam allowance, size charts)."""

    name = models.CharField(_("nom"), max_length=200, db_collation=FR_COLLATION)
    # Other spellings found in file names or booklets ("MPF", "MML"...), used for recognition.
    aliases = ArrayField(models.CharField(max_length=200), blank=True, default=list)
    seam_allowance = models.CharField(
        _("marges (règle de la marque)"),
        max_length=20,
        choices=SeamAllowance,
        default=SeamAllowance.UNKNOWN,
    )
    seam_allowance_detail = models.CharField(_("marges : détail"), max_length=200, blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(Lower("name"), name="brand_unique_name_ci"),
        ]

    def __str__(self):
        return self.name


class Tag(models.Model):
    """Shared detail taxonomy (sleeves, neckline, closure...). Not user data."""

    group = models.CharField(_("groupe"), max_length=50)
    value = models.CharField(_("valeur"), max_length=100)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["pk"]  # creation order = order of the taxonomy file (groups stay together)
        constraints = [
            models.UniqueConstraint(fields=["group", "value"], name="tag_unique_group_value"),
        ]

    def __str__(self):
        return f"{self.group} : {self.value}"


class Pattern(OwnedModel):
    name = models.CharField(_("modèle"), max_length=200, db_collation=FR_COLLATION)
    brand = models.ForeignKey(
        Brand, on_delete=models.PROTECT, related_name="patterns", verbose_name=_("marque")
    )
    brand_confidence = models.CharField(
        _("marque : fiabilité"),
        max_length=20,
        choices=BrandConfidence,
        default=BrandConfidence.CERTAIN,
    )

    garment_type = models.CharField(_("type"), max_length=100)  # free text, e.g. "Robe / blouse"
    family = models.CharField(
        _("famille"), max_length=20, choices=Family, default=Family.UNCLASSIFIED
    )
    audience = models.CharField(
        _("public"), max_length=20, choices=Audience, default=Audience.UNSPECIFIED
    )

    seasons = ArrayField(models.CharField(max_length=20, choices=Season), blank=True, default=list)
    seasons_inferred = models.BooleanField(_("saison déduite"), default=False)

    yardage_raw = models.CharField(_("métrage (laize 140-150)"), max_length=500, blank=True)
    yardage_max_m = models.DecimalField(
        _("métrage maximum (m)"), max_digits=4, decimal_places=2, null=True, blank=True
    )

    fabrics_raw = models.TextField(_("tissus conseillés"), blank=True)
    fabric_kind = models.CharField(
        _("tissu"), max_length=20, choices=FabricKind, default=FabricKind.UNSPECIFIED
    )

    size_range = models.CharField(_("tailles"), max_length=100, blank=True)

    difficulty_raw = models.CharField(_("niveau (livret)"), max_length=200, blank=True)
    difficulty = models.CharField(
        _("niveau"), max_length=20, choices=Difficulty, default=Difficulty.UNSPECIFIED
    )

    seam_allowance = models.CharField(
        _("marges de couture"),
        max_length=20,
        choices=SeamAllowance,
        default=SeamAllowance.UNKNOWN,
    )
    seam_allowance_detail = models.CharField(_("marges : détail"), max_length=200, blank=True)
    seam_allowance_source = models.CharField(
        _("marges : source"), max_length=20, choices=InfoSource, default=InfoSource.UNKNOWN
    )

    tags = models.ManyToManyField(
        Tag, blank=True, related_name="patterns", verbose_name=_("détails")
    )
    tags_source = models.CharField(_("détails : source"), max_length=100, blank=True)

    # Brand size charts that apply to this pattern (a brand may have several, e.g. 30-48 and
    # 34-56). Explicit link instead of the old "same brand name" string matching.
    brand_size_charts = models.ManyToManyField(
        "SizeChart", blank=True, related_name="applied_to", limit_choices_to={"pattern": None}
    )

    thumbnail = models.ImageField(_("vignette"), upload_to="thumbnails/", blank=True)

    # Personal tracking (never usable in the static MVP)
    sewn = models.BooleanField(_("cousu"), default=False)
    notes = models.TextField(_("mes notes"), blank=True)

    remarks = models.TextField(_("remarques d'inventaire"), blank=True)

    # Traceability to the CSV inventory (row number = old ID, original path on disk)
    legacy_ref = models.PositiveIntegerField(null=True, blank=True, editable=False)
    legacy_location = models.CharField(max_length=500, blank=True, editable=False)

    search_text = models.TextField(editable=False, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "legacy_ref"],
                condition=Q(legacy_ref__isnull=False),
                name="pattern_unique_legacy_ref_per_owner",
            ),
            models.CheckConstraint(
                condition=Q(yardage_max_m__isnull=True) | Q(yardage_max_m__gt=0),
                name="pattern_yardage_positive",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.brand})"

    def save(self, *args, **kwargs):
        self.search_text = self.build_search_text()
        super().save(*args, **kwargs)

    def build_search_text(self) -> str:
        parts = [self.name, self.brand.name, self.garment_type, self.fabrics_raw, self.remarks]
        # Tags are M2M: they only exist once saved. Save again after changing tags to index them.
        if self.pk:
            parts += [f"{t.group} {t.value}" for t in self.tags.all()]
        return normalize(" ".join(parts))


def blob_name(owner_id: int, sha256: str, filename: str) -> str:
    """Content-addressed storage key: identical files are stored once per owner."""
    suffix = PurePosixPath(filename).suffix.lower()
    return f"files/{owner_id}/{sha256[:2]}/{sha256}{suffix}"


def pattern_file_path(instance, filename):
    return blob_name(instance.pattern.owner_id, instance.sha256, filename)


class PatternFile(models.Model):
    """A file of a pattern (booklet, pieces...). Its blob may be shared with other patterns
    of the same owner when the content is identical."""

    class Kind(models.TextChoices):
        BOOKLET = "booklet", _("Livret")
        PIECES = "pieces", _("Planches")
        IMAGE = "image", _("Image")
        ARCHIVE = "archive", _("Archive")
        OTHER = "other", _("Autre")

    pattern = models.ForeignKey(Pattern, on_delete=models.CASCADE, related_name="files")
    file = models.FileField(upload_to=pattern_file_path, max_length=500)
    kind = models.CharField(max_length=20, choices=Kind, default=Kind.OTHER)
    original_name = models.CharField(max_length=500)  # path inside the pattern folder or zip
    sha256 = models.CharField(max_length=64, db_index=True)
    size = models.PositiveBigIntegerField()

    class Meta:
        ordering = ["kind", "original_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["pattern", "original_name"], name="patternfile_unique_name"
            ),
        ]

    def __str__(self):
        return self.original_name

    @property
    def basename(self) -> str:
        return PurePosixPath(self.original_name).name


class MeasureType(models.TextChoices):
    BODY = "body", _("Corps")
    FINISHED = "finished", _("Vêtement fini")


class SizeChart(models.Model):
    """A size chart belongs either to a brand (shared reference data) or to one pattern
    (private: ownership follows the pattern)."""

    brand = models.ForeignKey(
        Brand, on_delete=models.CASCADE, null=True, blank=True, related_name="size_charts"
    )
    pattern = models.ForeignKey(
        Pattern, on_delete=models.CASCADE, null=True, blank=True, related_name="size_charts"
    )
    measure_type = models.CharField(_("type de mesure"), max_length=20, choices=MeasureType)
    note = models.TextField(_("précision"), blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(Q(brand__isnull=False) & Q(pattern__isnull=True))
                | (Q(brand__isnull=True) & Q(pattern__isnull=False)),
                name="sizechart_brand_xor_pattern",
            ),
        ]

    def __str__(self):
        target = self.pattern or self.brand
        return f"{target} — {self.get_measure_type_display()}"


class SizeChartRow(models.Model):
    """One size of a chart. Each measure is a range in cm (a single value is [v, v])."""

    chart = models.ForeignKey(SizeChart, on_delete=models.CASCADE, related_name="rows")
    position = models.PositiveSmallIntegerField()
    label = models.CharField(_("taille"), max_length=50)  # as printed: "38", "M", "38 (M)"

    chest = DecimalRangeField(_("poitrine"), null=True, blank=True, default_bounds="[]")
    underbust = DecimalRangeField(
        _("dessous de poitrine"), null=True, blank=True, default_bounds="[]"
    )
    waist = DecimalRangeField(_("taille"), null=True, blank=True, default_bounds="[]")
    hip = DecimalRangeField(_("hanches"), null=True, blank=True, default_bounds="[]")
    back_width = DecimalRangeField(_("carrure dos"), null=True, blank=True, default_bounds="[]")
    back_waist_length = DecimalRangeField(
        _("longueur taille-dos"), null=True, blank=True, default_bounds="[]"
    )
    inseam = DecimalRangeField(_("entrejambe"), null=True, blank=True, default_bounds="[]")
    height = DecimalRangeField(_("stature"), null=True, blank=True, default_bounds="[]")

    class Meta:
        ordering = ["chart", "position"]
        constraints = [
            models.UniqueConstraint(fields=["chart", "position"], name="sizechartrow_unique_pos"),
        ]

    def __str__(self):
        return self.label


MEASURES = (
    "chest",
    "underbust",
    "waist",
    "hip",
    "back_width",
    "back_waist_length",
    "inseam",
    "height",
)


class MeasurementProfile(OwnedModel):
    """Body measurements of a person the user sews for (themselves, partner, child...)."""

    name = models.CharField(_("nom"), max_length=100)

    chest = models.DecimalField(
        _("poitrine"), max_digits=5, decimal_places=1, null=True, blank=True
    )
    underbust = models.DecimalField(
        _("dessous de poitrine"), max_digits=5, decimal_places=1, null=True, blank=True
    )
    waist = models.DecimalField(_("taille"), max_digits=5, decimal_places=1, null=True, blank=True)
    hip = models.DecimalField(_("hanches"), max_digits=5, decimal_places=1, null=True, blank=True)
    back_width = models.DecimalField(
        _("carrure dos"), max_digits=5, decimal_places=1, null=True, blank=True
    )
    back_waist_length = models.DecimalField(
        _("longueur taille-dos"), max_digits=5, decimal_places=1, null=True, blank=True
    )
    inseam = models.DecimalField(
        _("entrejambe"), max_digits=5, decimal_places=1, null=True, blank=True
    )
    height = models.DecimalField(
        _("stature"), max_digits=5, decimal_places=1, null=True, blank=True
    )

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["owner", "name"], name="profile_unique_name_per_owner"),
        ]

    def __str__(self):
        return self.name
