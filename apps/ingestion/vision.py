"""Detail tags read on the technical drawing by a vision model (Ollama chat API with images).

EXPERIMENTAL, not used by the application. Measured with evaluate_vision on 40 drawings
(precision on the tag groups the inventory filled): qwen3.5 9B 38 %, gemma4 12B 34 %. Both
models answer by default ("straight fit", "front buttons", "midi") instead of reading the
drawing. Kept to measure larger models later.
"""

import base64
import io
import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from django.conf import settings
from PIL import Image

from apps.catalog.models import Tag

log = logging.getLogger(__name__)

MAX_SIDE = 1024  # px sent to the model
MAX_PER_GROUP = 2  # a pattern may have two versions (short and long sleeves...)
TIMEOUT = 300

PROMPT = """This is the technical drawing (flat sketch) of a sewing pattern, possibly with
several views or versions. For each group, list the values that are clearly visible on the
drawing, at most two per group (only when versions differ). Use an empty list when the group
does not apply (no sleeves group for a skirt, no leg group for a top) or when you cannot see
it. Do not guess: an empty list is better than a wrong value."""


@dataclass
class VisionAnswer:
    tags: list[tuple[str, str]] = field(default_factory=list)
    seconds: float = 0.0


def _taxonomy() -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for tag in Tag.objects.all():
        groups.setdefault(tag.group, []).append(tag.value)
    return groups


def _png(image: Image.Image) -> str:
    image = image.convert("RGB")
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def drawing_tags(image: Image.Image, model: str | None = None) -> VisionAnswer | None:
    taxonomy = _taxonomy()
    schema = {
        "type": "object",
        "properties": {
            group: {
                "type": "array",
                "items": {"type": "string", "enum": values},
                "maxItems": MAX_PER_GROUP,
            }
            for group, values in taxonomy.items()
        },
        "required": list(taxonomy),
    }
    body = {
        "model": model or settings.LLM_MODEL,
        "messages": [{"role": "user", "content": PROMPT, "images": [_png(image)]}],
        "format": schema,
        "stream": False,
        "think": False,
        "options": {"temperature": 0},
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
        log.warning("Vision extraction failed: %s", e)
        return None
    tags = []
    for group, values in taxonomy.items():
        chosen = data.get(group) or []
        if isinstance(chosen, list):
            tags += [(group, v) for v in dict.fromkeys(chosen) if v in values][:MAX_PER_GROUP]
    return VisionAnswer(tags=tags, seconds=reply.get("total_duration", 0) / 1e9)
