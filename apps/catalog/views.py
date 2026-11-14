"""Catalogue pages and the JSON API used by the catalogue page (static/catalog/catalogue.js).

Every query is scoped to request.user: other users' data answers 404.
"""

import json
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_http_methods

from . import presenters
from .models import (
    MEASURES,
    Audience,
    Difficulty,
    FabricKind,
    Family,
    MeasurementProfile,
    Pattern,
    SeamAllowance,
    Tag,
)
from .sizing import recommend

DEFAULT_PROFILE_NAME = "Moi"
MAX_MEASURE_CM = Decimal(300)


def _patterns(user):
    return Pattern.objects.filter(owner=user).select_related("brand")


def _with_size_charts(qs):
    return qs.prefetch_related("tags", "size_charts__rows", "brand_size_charts__rows")


def _profile(request: HttpRequest) -> MeasurementProfile | None:
    """Profile from the `profile` query parameter, or None (no measurements)."""
    pk = request.GET.get("profile")
    if not pk or not pk.isdigit():
        return None
    return MeasurementProfile.objects.filter(owner=request.user, pk=pk).first()


def _measures(profile: MeasurementProfile | None) -> dict[str, Decimal]:
    if profile is None:
        return {}
    return {k: getattr(profile, k) for k in MEASURES if getattr(profile, k) is not None}


@login_required
def catalogue(request):
    return render(request, "catalog/catalogue.html")


@login_required
@require_GET
def api_catalogue(request):
    """Every pattern of the user with the values the catalogue filters on."""
    patterns = _patterns(request.user).prefetch_related("tags", "size_charts", "brand_size_charts")
    rows = []
    for p in patterns:
        charts = list(p.size_charts.all()) + list(p.brand_size_charts.all())
        rows.append(
            {
                "id": p.pk,
                "name": p.name,
                "brand": p.brand.name,
                "type": p.garment_type,
                "family": p.family,
                "audience": p.audience,
                "seasons": p.seasons,
                "seasons_text": presenters.seasons(p),
                "yardage_text": p.yardage_raw,
                "yardage_max": float(p.yardage_max_m) if p.yardage_max_m is not None else None,
                "fabric": p.fabric_kind,
                "difficulty": p.difficulty,
                "seam": p.seam_allowance,
                "thumb": p.thumbnail.url if p.thumbnail else "",
                "tags": [f"{t.group}|{t.value}" for t in p.tags.all()],
                "search": p.search_text,
                "has_body_chart": any(c.measure_type == "body" for c in charts),
            }
        )
    taxonomy: dict[str, list[str]] = {}
    for tag in Tag.objects.all():
        taxonomy.setdefault(tag.group, []).append(tag.value)

    def choices(enum, exclude=()):
        return [[c.value, str(c.label)] for c in enum if c.value not in exclude]

    return JsonResponse(
        {
            "patterns": rows,
            "tags": taxonomy,
            "choices": {
                "family": choices(Family),
                "audience": choices(Audience),
                "fabric": choices(FabricKind, exclude={FabricKind.UNSPECIFIED}),
                "difficulty": choices(Difficulty),
                "seam": choices(SeamAllowance),
            },
        }
    )


@login_required
@require_GET
def api_recommendations(request):
    """Size recommendation of every pattern for one profile: {pattern id: {...}}."""
    measures = _measures(_profile(request))
    out = {}
    for p in _with_size_charts(_patterns(request.user)):
        rec = recommend(p, measures)
        text, muted = presenters.card_recommendation(rec)
        out[p.pk] = {"text": text, "muted": muted, "fits": rec.fits}
    return JsonResponse(out)


@login_required
@require_GET
def pattern_sheet(request, pk: int):
    """HTML fragment of a pattern sheet, shown in the catalogue modal."""
    pattern = get_object_or_404(_with_size_charts(_patterns(request.user)), pk=pk)
    measures = _measures(_profile(request))
    rec = recommend(pattern, measures)
    context = {
        "pattern": pattern,
        "files": pattern.files.all(),
        "tags": pattern.tags.all(),
        "seasons": presenters.seasons(pattern),
        "body_charts": [presenters.body_chart_block(r, rec, measures) for r in rec.body_charts],
        "finished_charts": [
            {"note": m.note, "table": presenters.chart_table(c)} for m, c in rec.finished_charts
        ],
    }
    return render(request, "catalog/_sheet.html", context)


# ---- measurement profiles


def _profile_json(p: MeasurementProfile) -> dict:
    return {
        "id": p.pk,
        "name": p.name,
        "measures": {
            k: (float(getattr(p, k)) if getattr(p, k) is not None else None) for k in MEASURES
        },
    }


def _error(message: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"error": message}, status=status)


def _parse_measures(data) -> dict[str, Decimal | None]:
    if not isinstance(data, dict) or set(data) - set(MEASURES):
        raise ValueError("Mesures inconnues.")
    out = {}
    for k, v in data.items():
        if v is None or v == "":
            out[k] = None
            continue
        try:
            value = Decimal(str(v)).quantize(Decimal("0.1"))
        except InvalidOperation:
            raise ValueError(f"Valeur invalide pour {k}.") from None
        if not 0 < value < MAX_MEASURE_CM:
            raise ValueError(f"Valeur hors limites pour {k}.")
        out[k] = value
    return out


def _body(request) -> dict:
    try:
        data = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        raise ValueError("JSON invalide.") from None
    if not isinstance(data, dict):
        raise ValueError("JSON invalide.")
    return data


@login_required
@require_http_methods(["GET", "POST"])
def api_profiles(request):
    if request.method == "GET":
        profiles = MeasurementProfile.objects.filter(owner=request.user)
        if not profiles.exists():
            MeasurementProfile.objects.create(owner=request.user, name=DEFAULT_PROFILE_NAME)
        return JsonResponse({"profiles": [_profile_json(p) for p in profiles]})
    try:
        data = _body(request)
        name = str(data.get("name", "")).strip()
        if not name:
            return _error("Nom manquant.")
        with transaction.atomic():
            profile = MeasurementProfile.objects.create(owner=request.user, name=name)
    except ValueError as e:
        return _error(str(e))
    except IntegrityError:
        return _error("Un profil porte déjà ce nom.")
    return JsonResponse(_profile_json(profile), status=201)


@login_required
@require_http_methods(["PATCH", "DELETE"])
def api_profile(request, pk: int):
    profile = get_object_or_404(MeasurementProfile, owner=request.user, pk=pk)
    if request.method == "DELETE":
        if MeasurementProfile.objects.filter(owner=request.user).count() < 2:
            return _error("Il faut garder au moins un profil.")
        profile.delete()
        return HttpResponse(status=204)
    try:
        data = _body(request)
        if "name" in data:
            name = str(data["name"]).strip()
            if not name:
                return _error("Nom manquant.")
            profile.name = name
        for k, v in _parse_measures(data.get("measures", {})).items():
            setattr(profile, k, v)
        with transaction.atomic():
            profile.save()
    except ValueError as e:
        return _error(str(e))
    except IntegrityError:
        return _error("Un profil porte déjà ce nom.")
    return JsonResponse(_profile_json(profile))
