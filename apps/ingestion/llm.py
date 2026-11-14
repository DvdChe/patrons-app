"""Optional LLM extraction (Ollama chat API) for the fields the rules cannot read reliably:
fabrics, yardage, seasons, detail tags, garment type.

The model never sees a whole booklet: only its opening pages and the passages around fabric
and yardage keywords (located by the rules), which keeps prompts short enough for small
models and CPU inference. Answers are constrained by a JSON schema and validated.
"""

import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.conf import settings

from apps.catalog.models import FabricKind, Season, Tag

from . import extractors as x

log = logging.getLogger(__name__)

TOPICS = (
    r"tissu|fabric|matiere|preconisation|metrage|laize|consommation|yardage|you will need"
    r"|fabric requirements?|quantite"
)
OPENING_CHARS = 2500
WINDOW_BEFORE, WINDOW_AFTER = 150, 650
MAX_EXCERPT_CHARS = 6000
TIMEOUT = 300

SYSTEM_PROMPT = """You read sewing pattern booklets (French or English) and extract facts.
Use only what the text says; when something is not stated, use null or an empty list.
- garment_type: the kind of garment in French, one or two words (Robe, Trench, Pantalon...).
- fabrics: the recommended fabrics, copied as written in the booklet (French if available).
- fabric_kind: "woven", "knit", "both" or "unspecified", from the recommended fabrics.
- yardage_m: metres of MAIN fabric needed for the LARGEST size with a 140-150 cm wide fabric.
  Not the lining, not other widths. null if not given for that width.
- seasons: seasons this garment is designed for, judged from the garment and its recommended
  fabrics, not from passing remarks. Use "all" for a garment worn all year. Empty if unclear.
- tags: details that describe THIS pattern (sleeves, neckline, closure, waist, leg, length,
  fit), only from the allowed values, only if the text says so."""


@dataclass
class LlmAnswer:
    garment_type: str = ""
    fabrics: str = ""
    fabric_kind: str = FabricKind.UNSPECIFIED
    yardage_m: Decimal | None = None
    seasons: list[str] = field(default_factory=list)
    tags: list[tuple[str, str]] = field(default_factory=list)
    seconds: float = 0.0


def enabled() -> bool:
    return bool(settings.LLM_URL and settings.LLM_MODEL)


def excerpts(raw_text: str) -> str:
    """Passages around fabric and yardage keywords, as written, merged and capped."""
    prepared, index = x.prepare_map(raw_text)
    spans: list[list[int]] = []
    for m in re.finditer(TOPICS, prepared):
        start = index[max(0, m.start() - WINDOW_BEFORE)]
        end = index[min(len(index) - 1, m.end() + WINDOW_AFTER)]
        if spans and start <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], end)
        else:
            spans.append([start, end])
    out, total = [], 0
    for start, end in spans:
        part = re.sub(r"[ \t]+", " ", raw_text[start:end]).strip()
        if total + len(part) > MAX_EXCERPT_CHARS:
            break
        out.append(part)
        total += len(part)
    return "\n[…]\n".join(out)


def _schema(tag_values: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "garment_type": {"type": "string"},
            "fabrics": {"type": "string"},
            "fabric_kind": {"type": "string", "enum": [c.value for c in FabricKind]},
            "yardage_m": {"type": ["number", "null"]},
            "seasons": {
                "type": "array",
                "items": {"type": "string", "enum": [s.value for s in Season]},
            },
            "tags": {"type": "array", "items": {"type": "string", "enum": tag_values}},
        },
        "required": ["garment_type", "fabrics", "fabric_kind", "yardage_m", "seasons", "tags"],
    }


def ask(name: str, opening: str, raw_text: str, model: str | None = None) -> LlmAnswer | None:
    """One chat call; None when the model is unreachable or answers nonsense."""
    tag_values = [f"{t.group} : {t.value}" for t in Tag.objects.all()]
    user = (
        f"Pattern name: {name or 'unknown'}\n\n"
        f"Opening pages:\n{opening[:OPENING_CHARS]}\n\n"
        f"Passages about fabrics and yardage:\n{excerpts(raw_text) or '(none)'}"
    )
    body = {
        "model": model or settings.LLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        "format": _schema(tag_values),
        "stream": False,
        "think": False,
        "options": {"temperature": 0, "num_ctx": 8192},
    }
    request = urllib.request.Request(
        f"{settings.LLM_URL.rstrip('/')}/api/chat",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            reply = json.load(response)
        data = json.loads(reply["message"]["content"])
    except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError) as e:
        log.warning("LLM extraction failed: %s", e)
        return None
    return _parse(data, set(tag_values), reply.get("total_duration", 0) / 1e9)


def _parse(data: dict, allowed_tags: set[str], seconds: float) -> LlmAnswer:
    answer = LlmAnswer(seconds=seconds)
    answer.garment_type = str(data.get("garment_type") or "").strip()[:100]
    answer.fabrics = str(data.get("fabrics") or "").strip()[:500]
    if data.get("fabric_kind") in FabricKind.values:
        answer.fabric_kind = data["fabric_kind"]
    try:
        value = Decimal(str(data.get("yardage_m"))).quantize(Decimal("0.01"))
        if Decimal("0.2") <= value <= 10:
            answer.yardage_m = value
    except (InvalidOperation, ValueError):
        pass
    answer.seasons = [s for s in dict.fromkeys(data.get("seasons") or []) if s in Season.values]
    answer.tags = [
        tuple(t.split(" : ", 1)) for t in dict.fromkeys(data.get("tags") or []) if t in allowed_tags
    ]
    return answer
