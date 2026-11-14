"""LLM extraction: passages, per-field answers, few-shot messages, fill-only-empty merge.
No network: the Ollama call is replaced by a fake answering from the requested schema."""

import io
import json
from decimal import Decimal as D

import pytest

from apps.catalog.models import FabricKind, Family, Tag
from apps.ingestion import llm
from apps.ingestion.documents import DocumentText
from apps.ingestion.pipeline import Proposal, complete_with_llm

BOOKLET = (
    "Voici Rosa, une robe fluide. Niveau : facile. Tailles : du 34 au 46.\n"
    + "Montage. " * 300
    + "Tissus conseillés : crêpe, viscose. Les marges de couture de 1 cm sont incluses. "
    + "Métrage : 2,10 m en laize 140 pour la plus grande taille."
)

# Answer per task, recognised by the first property of the requested schema.
ANSWERS = {
    "brand": {"brand": "Atelier Rosa"},
    "garment_type": {"garment_type": "Robe"},
    "smallest": {"smallest": "34", "largest": "46"},
    "as_written": {"as_written": "Facile", "level": "beginner"},
    "included": {"included": "included", "width_cm": 1},
    "yardage_m": {"yardage_m": 2.1},
    "fabrics": {"fabrics": "crêpe, viscose", "kind": "woven"},
}


@pytest.fixture
def fake_ollama(monkeypatch, settings):
    settings.LLM_URL = "http://ollama:11434"
    settings.LLM_MODEL = "test-model"
    calls = []

    def urlopen(request, timeout):
        body = json.loads(request.data)
        calls.append(body)
        first_property = next(iter(body["format"]["properties"]))
        answer = ANSWERS.get(first_property, {})
        reply = {"message": {"content": json.dumps(answer)}, "total_duration": 1_000_000_000}
        return io.BytesIO(json.dumps(reply).encode())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    return calls


def test_passages_keep_only_the_text_around_the_keywords():
    out = llm.passages(BOOKLET, r"tissu|metrage", 20, 60, 5000)
    assert "crêpe, viscose" in out
    assert "Montage. Montage. Montage. Montage." not in out
    assert len(out) < len(BOOKLET) / 4


def test_ask_runs_one_call_per_field(fake_ollama):
    answer = llm.ask(BOOKLET[:200], BOOKLET)

    assert len(fake_ollama) == len(llm.TASKS)
    assert answer.brand == "Atelier Rosa"
    assert answer.size_range == "34 – 46"
    assert (answer.difficulty, answer.difficulty_raw) == ("beginner", "Facile")
    assert (answer.seam_allowance, answer.seam_width) == ("included", "1 cm")
    assert answer.yardage_m == D("2.10")
    assert answer.fabric_kind == "woven"
    assert all(call["think"] is False and call["stream"] is False for call in fake_ollama)


def test_few_shot_examples_are_sent_as_previous_turns(fake_ollama):
    examples = {"yardage": [("Métrage : 1,50 m", {"yardage_m": 1.5})]}
    llm.ask("", BOOKLET, examples=examples, tasks=["yardage"])
    roles = [m["role"] for m in fake_ollama[0]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert fake_ollama[0]["messages"][2]["content"] == '{"yardage_m": 1.5}'


def test_invalid_values_are_dropped(fake_ollama, monkeypatch):
    monkeypatch.setitem(ANSWERS, "yardage_m", {"yardage_m": 250})
    monkeypatch.setitem(ANSWERS, "as_written", {"as_written": "A2", "level": "expert"})
    answer = llm.ask("", BOOKLET, tasks=["yardage", "difficulty"])
    assert answer.yardage_m is None
    assert answer.difficulty == "unspecified"


def test_ask_returns_none_when_ollama_is_down(settings):
    settings.LLM_URL = "http://127.0.0.1:9"  # nothing listens there
    settings.LLM_MODEL = "test-model"
    assert llm.ask("", BOOKLET) is None


def test_llm_only_fills_what_the_rules_left_empty(fake_ollama):
    p = Proposal(
        family=Family.DRESSES,
        garment_type="Robe",
        fabric_kind=FabricKind.UNSPECIFIED,
        document=DocumentText(text=BOOKLET, opening=BOOKLET[:200]),
    )

    complete_with_llm(p)

    assert (p.garment_type, p.family) == ("Robe", Family.DRESSES)  # never from the LLM
    assert p.fabric_kind == FabricKind.WOVEN  # filled
    assert p.fabrics_raw == "crêpe, viscose"
    assert p.yardage_max_m is None  # not trusted yet, kept on p.llm
    assert p.llm.yardage_m == D("2.10")


def test_drawing_tags_keeps_taxonomy_values_only(db, monkeypatch, settings):
    from PIL import Image

    from apps.ingestion import vision

    settings.LLM_URL, settings.LLM_MODEL = "http://ollama:11434", "test-model"
    Tag.objects.create(group="Manches", value="courtes")
    Tag.objects.create(group="Coupe", value="droite")
    sent = []

    def urlopen(request, timeout):
        sent.append(json.loads(request.data))
        answer = {"Manches": ["courtes", "courtes", "inventée"], "Coupe": "droite"}
        return io.BytesIO(json.dumps({"message": {"content": json.dumps(answer)}}).encode())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    answer = vision.drawing_tags(Image.new("RGB", (2000, 1000), "white"))
    assert answer.tags == [("Manches", "courtes")]
    assert sent[0]["messages"][0]["images"]
