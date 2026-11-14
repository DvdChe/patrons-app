"""Optional LLM extraction (Ollama chat API), one short call per field.

Each field task sends only the booklet passages about that field (located with keywords),
asks one question with a strict JSON schema, and can be shown worked examples: other patterns
of the same brand with their known answer (booklets of a brand share their layout).
Measured with `evaluate_extraction --llm [--few-shot]`; the pipeline only uses the fields that
beat the rules.
"""

import json
import logging
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.conf import settings

from apps.catalog.models import Difficulty, FabricKind, Pattern, SeamAllowance

from . import extractors as x

log = logging.getLogger(__name__)
TIMEOUT = 300


def enabled() -> bool:
    return bool(settings.LLM_URL and settings.LLM_MODEL)


def passages(raw_text: str, topics: str, before: int, after: int, max_chars: int) -> str:
    """Passages around the topic keywords, as written, merged and capped."""
    prepared, index = x.prepare_map(raw_text)
    spans: list[list[int]] = []
    for m in re.finditer(topics, prepared):
        start = index[max(0, m.start() - before)]
        end = index[min(len(index) - 1, m.end() + after)]
        if spans and start <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], end)
        else:
            spans.append([start, end])
    out, total = [], 0
    for start, end in spans:
        part = re.sub(r"[ \t]+", " ", raw_text[start:end]).strip()
        if total + len(part) > max_chars:
            part = part[: max_chars - total]
        out.append(part)
        total += len(part)
        if total >= max_chars:
            break
    return "\n[…]\n".join(p for p in out if p)


def _number(value) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _bounds(size_range: str) -> tuple[str, str]:
    tokens = re.findall(r"\d{2}|[2-6]?x{0,3}[sml]\b|unique", size_range.lower())
    return (tokens[0], tokens[1]) if len(tokens) >= 2 else ("", "")


@dataclass(frozen=True)
class FieldTask:
    name: str
    question: str
    schema: dict
    source: Callable[[str, str], str]  # (raw booklet text, opening pages) -> passage
    example: Callable[[Pattern], dict | None]  # known answer of an inventory pattern


def _schema(properties: dict) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties)}


TASKS = [
    FieldTask(
        "brand",
        "Which brand or designer published this sewing pattern? Give its name as written "
        "(not the pattern name). Empty string if not stated.",
        _schema({"brand": {"type": "string"}}),
        lambda raw, opening: (
            opening[:1500]
            + "\n[…]\n"
            + passages(raw, r"©|copyright|www|\.com|\.fr|instagram|@", 80, 80, 800)
        ),
        lambda p: {"brand": p.brand.name},
    ),
    FieldTask(
        "garment",
        "What kind of garment is this pattern? Answer in French with one or two words "
        "(Robe, Jupe, Pantalon, Trench, Combinaison...).",
        _schema({"garment_type": {"type": "string"}}),
        lambda raw, opening: opening[:1500],
        lambda p: {"garment_type": p.garment_type} if p.garment_type else None,
    ),
    FieldTask(
        "sizes",
        "Which is the smallest and the largest size of the pattern, as printed (34, XS...)? "
        "Empty strings if not stated.",
        _schema({"smallest": {"type": "string"}, "largest": {"type": "string"}}),
        lambda raw, opening: passages(raw, r"\btailles?\b|\bsizes?\b", 60, 300, 2500),
        lambda p: (
            dict(zip(("smallest", "largest"), _bounds(p.size_range), strict=True))
            if _bounds(p.size_range)[0]
            else None
        ),
    ),
    FieldTask(
        "difficulty",
        "What sewing level does the booklet state? Copy the wording as written, and classify "
        "it. Use unspecified if no level is stated.",
        _schema(
            {
                "as_written": {"type": "string"},
                "level": {"type": "string", "enum": [d.value for d in Difficulty]},
            }
        ),
        lambda raw, opening: (
            opening[:800] + "\n[…]\n" + passages(raw, r"niveau|level|difficult", 80, 200, 1500)
        ),
        lambda p: (
            {"as_written": p.difficulty_raw, "level": p.difficulty}
            if p.difficulty != Difficulty.UNSPECIFIED
            else None
        ),
    ),
    FieldTask(
        "seam",
        "Are seam allowances included in the pattern pieces? Give the main seam allowance width "
        "in cm (not the hem), null if not stated.",
        _schema(
            {
                "included": {"type": "string", "enum": [s.value for s in SeamAllowance]},
                "width_cm": {"type": ["number", "null"]},
            }
        ),
        lambda raw, opening: passages(
            raw, r"marges?|seam allowances?|valeurs? de couture|coutures? comprises", 150, 300, 2000
        ),
        lambda p: (
            {
                "included": p.seam_allowance,
                "width_cm": float(w)
                if (w := _number(_first_number(p.seam_allowance_detail)))
                else None,
            }
            if p.seam_allowance != SeamAllowance.UNKNOWN
            else None
        ),
    ),
    FieldTask(
        "yardage",
        "How many metres of main fabric does the LARGEST size need, for a 140-150 cm wide "
        "fabric? Not the lining, not other widths. null if not stated for that width.",
        _schema({"yardage_m": {"type": ["number", "null"]}}),
        lambda raw, opening: passages(
            raw,
            r"metrage|consommation|laize|yardage|fabric requirements?|you will need"
            r"|quantite de tissu|tissu principal",
            100,
            900,
            3500,
        ),
        lambda p: {"yardage_m": float(p.yardage_max_m)} if p.yardage_max_m else None,
    ),
    FieldTask(
        "fabrics",
        "Which fabrics does the booklet recommend? Copy them as written (French if available) "
        "and say whether they are woven, knit or both.",
        _schema(
            {
                "fabrics": {"type": "string"},
                "kind": {"type": "string", "enum": [k.value for k in FabricKind]},
            }
        ),
        lambda raw, opening: passages(raw, r"tissu|fabric|matiere|preconisation", 150, 600, 3000),
        lambda p: (
            {"fabrics": p.fabrics_raw, "kind": p.fabric_kind}
            if p.fabric_kind != FabricKind.UNSPECIFIED
            else None
        ),
    ),
]
TASKS_BY_NAME = {t.name: t for t in TASKS}

SYSTEM_PROMPT = (
    "You read passages of a sewing pattern booklet (French or English) and answer one "
    "question in JSON. Use only what the passages say; never guess."
)


def _first_number(text: str) -> str:
    m = re.search(r"\d+(?:[.,]\d+)?", text or "")
    return m.group(0).replace(",", ".") if m else ""


@dataclass
class LlmAnswer:
    brand: str = ""
    garment_type: str = ""
    size_range: str = ""
    difficulty: str = Difficulty.UNSPECIFIED
    difficulty_raw: str = ""
    seam_allowance: str = SeamAllowance.UNKNOWN
    seam_width: str = ""  # "1 cm"
    yardage_m: Decimal | None = None
    fabrics: str = ""
    fabric_kind: str = FabricKind.UNSPECIFIED
    seconds: float = 0.0
    failed: list[str] = field(default_factory=list)


# Examples: task name -> [(passage, answer), ...], for few-shot prompting.
Examples = dict[str, list[tuple[str, dict]]]


def _chat(task: FieldTask, passage: str, examples: list, model: str | None) -> tuple[dict, float]:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for example_passage, answer in examples:
        messages.append({"role": "user", "content": f"{task.question}\n\n{example_passage}"})
        messages.append({"role": "assistant", "content": json.dumps(answer, ensure_ascii=False)})
    messages.append({"role": "user", "content": f"{task.question}\n\n{passage}"})
    body = {
        "model": model or settings.LLM_MODEL,
        "messages": messages,
        "format": task.schema,
        "stream": False,
        "think": False,
        "options": {"temperature": 0, "num_ctx": 8192},
    }
    request = urllib.request.Request(
        f"{settings.LLM_URL.rstrip('/')}/api/chat",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        reply = json.load(response)
    return json.loads(reply["message"]["content"]), reply.get("total_duration", 0) / 1e9


def ask(
    opening: str,
    raw_text: str,
    model: str | None = None,
    examples: Examples | None = None,
    tasks: list[str] | None = None,
) -> LlmAnswer | None:
    """Run the field tasks; None when the server is unreachable."""
    answer = LlmAnswer()
    started = time.monotonic()
    for task in TASKS:
        if tasks is not None and task.name not in tasks:
            continue
        passage = task.source(raw_text, opening).strip()
        if not passage:
            continue
        try:
            data, _ = _chat(task, passage, (examples or {}).get(task.name, []), model)
        except urllib.error.URLError as e:
            log.warning("LLM unreachable: %s", e)
            return None
        except (TimeoutError, KeyError, json.JSONDecodeError) as e:
            log.warning("LLM task %s failed: %s", task.name, e)
            answer.failed.append(task.name)
            continue
        _apply(answer, task.name, data)
    answer.seconds = time.monotonic() - started
    return answer


def _apply(answer: LlmAnswer, task: str, data: dict) -> None:
    text = lambda key: str(data.get(key) or "").strip()[:300]  # noqa: E731
    if task == "brand":
        answer.brand = text("brand")
    elif task == "garment":
        answer.garment_type = text("garment_type")[:100]
    elif task == "sizes":
        smallest, largest = text("smallest").upper(), text("largest").upper()
        if smallest and largest and smallest != largest:
            answer.size_range = f"{smallest} – {largest}"
    elif task == "difficulty":
        if data.get("level") in Difficulty.values:
            answer.difficulty = data["level"]
            answer.difficulty_raw = text("as_written") if answer.difficulty != "unspecified" else ""
    elif task == "seam":
        if data.get("included") in SeamAllowance.values:
            answer.seam_allowance = data["included"]
        width = _number(data.get("width_cm"))
        if answer.seam_allowance == SeamAllowance.INCLUDED and width and 0 < width <= 5:
            answer.seam_width = f"{format(width.normalize(), 'f').replace('.', ',')} cm"
    elif task == "yardage":
        value = _number(data.get("yardage_m"))
        if value is not None and Decimal("0.2") <= value <= 10:
            answer.yardage_m = value.quantize(Decimal("0.01"))
    elif task == "fabrics":
        answer.fabrics = text("fabrics")
        if data.get("kind") in FabricKind.values:
            answer.fabric_kind = data["kind"]
