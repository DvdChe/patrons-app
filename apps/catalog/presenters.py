"""Turn models and size evaluations into display values (French wording of the MVP)."""

from decimal import Decimal

from django.utils.translation import gettext as _

from .models import MEASURES, Pattern, Season
from .sizing import BodyChartResult, Fit, Location, Range, Recommendation

MEASURE_LABELS = {
    "chest": "Poitrine",
    "underbust": "Dessous de poitrine",
    "waist": "Taille",
    "hip": "Hanches",
    "back_width": "Carrure dos",
    "back_waist_length": "Longueur taille-dos",
    "inseam": "Entrejambe",
    "height": "Stature",
}
# Order of the per-measure results on the pattern sheet (main body measures first).
SHEET_RESULT_ORDER = (
    "chest",
    "waist",
    "hip",
    "underbust",
    "back_width",
    "back_waist_length",
    "inseam",
    "height",
)


def number(value: Decimal | float | None) -> str:
    """French decimal format without trailing zeros: 84 -> "84", 2.50 -> "2,5"."""
    if value is None:
        return ""
    text = format(Decimal(value).normalize(), "f")
    return text.replace(".", ",")


def measure_range(value: Range | None) -> str:
    if value is None:
        return ""
    low, high = value
    return number(low) if low == high else f"{number(low)}–{number(high)}"


def location(loc: Location) -> str:
    if loc.fit == Fit.EXACT:
        return loc.labels[0]
    if loc.fit == Fit.BETWEEN:
        return _("entre %(low)s et %(high)s") % {"low": loc.labels[0], "high": loc.labels[1]}
    return _("hors tableau")


def card_recommendation(rec: Recommendation) -> tuple[str, bool]:
    """(text, muted) shown at the bottom of a catalogue card."""
    if rec.primary is None:
        return _("Tableau de tailles non disponible"), True
    main = rec.primary.main_measures_in_chart
    results = rec.primary.evaluation.main
    parts = [
        (f"{MEASURE_LABELS[k]} " if len(main) > 1 else "") + location(results[k])
        for k in main
        if k in results
    ]
    if not parts:
        return "", True
    return _("Taille conseillée : ") + " · ".join(parts), False


def seasons(pattern: Pattern) -> str:
    labels = [Season(s).label for s in pattern.seasons]
    if not labels:
        return ""
    text = " / ".join(str(label) for label in labels)
    return f"{text} {_('(déduite)')}" if pattern.seasons_inferred else text


def body_chart_block(result: BodyChartResult, rec: Recommendation, profile) -> dict:
    ev = result.evaluation
    main = set(result.evaluation.main_order)
    results = [
        {
            "label": MEASURE_LABELS[k],
            "main": k in main,
            "text": location(ev.results[k]),
        }
        for k in SHEET_RESULT_ORDER
        if k in ev.results
    ]
    ease = [
        f"{MEASURE_LABELS[name]} {number(diff)} cm"
        for _label, name, diff in rec.ease(result, profile)
    ]
    return {
        "is_brand": result.model.brand_id is not None,
        "note": result.model.note,
        "results": results,
        "grading": ev.needs_grading,
        "ease_label": result.chart.labels[ev.recommended_index] if ease else "",
        "ease": ease,
        "table": chart_table(result.chart, ev.recommended_index),
    }


def chart_table(chart, highlight: int | None = None) -> dict:
    columns = [k for k in MEASURES if k in chart.measures]
    return {
        "headers": [MEASURE_LABELS[k] for k in columns],
        "rows": [
            {
                "label": label,
                "highlight": i == highlight,
                "cells": [measure_range(chart.measures[k][i]) for k in columns],
            }
            for i, label in enumerate(chart.labels)
        ],
    }
