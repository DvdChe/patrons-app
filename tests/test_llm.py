"""LLM extraction: excerpts, answer validation, fill-only-empty merge. No network: the Ollama
call is replaced by a fake."""

import io
import json
from decimal import Decimal as D

import pytest

from apps.catalog.models import FabricKind, Family, Tag
from apps.ingestion import llm
from apps.ingestion.documents import DocumentText
from apps.ingestion.pipeline import Proposal, complete_with_llm


def test_excerpts_keep_only_passages_about_fabrics_and_yardage():
    raw = (
        "Bonjour ! " + "Blabla. " * 200
        + "Tissus conseillés : crêpe, viscose. " + "Montage. " * 200
        + "Métrage : 2,10 m en laize 140."
    )  # fmt: skip
    out = llm.excerpts(raw)
    assert "crêpe, viscose" in out
    assert "2,10 m en laize 140" in out
    assert "[…]" in out  # two separate passages
    assert len(out) < len(raw) / 2


@pytest.fixture
def fake_ollama(monkeypatch, settings):
    settings.LLM_URL = "http://ollama:11434"
    settings.LLM_MODEL = "test-model"
    calls = []

    def respond(answer):
        def urlopen(request, timeout):
            calls.append(json.loads(request.data))
            body = {"message": {"content": json.dumps(answer)}, "total_duration": 2_000_000_000}
            return io.BytesIO(json.dumps(body).encode())

        monkeypatch.setattr(llm.urllib.request, "urlopen", urlopen)

    return respond, calls


ANSWER = {
    "garment_type": "Trench",
    "fabrics": "drap de laine, tweed",
    "fabric_kind": "woven",
    "yardage_m": 4.55,
    "seasons": ["winter", "winter", "spring"],  # duplicate and invalid value
    "tags": ["Manches : raglan", "Manches : inventé"],  # value outside the taxonomy
}


def test_ask_sends_a_schema_and_validates_the_answer(db, fake_ollama):
    respond, calls = fake_ollama
    Tag.objects.create(group="Manches", value="raglan")
    respond(ANSWER)

    answer = llm.ask("Hendriks", "Voici Hendriks", "Tissus conseillés : drap de laine")

    assert answer.garment_type == "Trench"
    assert answer.yardage_m == D("4.55")
    assert answer.seasons == ["winter"]
    assert answer.tags == [("Manches", "raglan")]
    assert answer.seconds == 2
    sent = calls[0]
    assert sent["model"] == "test-model"
    assert sent["think"] is False
    assert sent["format"]["properties"]["tags"]["items"]["enum"] == ["Manches : raglan"]


def test_ask_returns_none_when_ollama_is_down(db, settings):
    settings.LLM_URL = "http://127.0.0.1:9"  # nothing listens there
    settings.LLM_MODEL = "test-model"
    assert llm.ask("x", "", "") is None


def test_llm_only_fills_what_the_rules_left_empty(db, fake_ollama):
    respond, _ = fake_ollama
    respond(ANSWER)
    p = Proposal(
        family=Family.DRESSES,
        garment_type="Robe",
        fabric_kind=FabricKind.UNSPECIFIED,
        document=DocumentText(text="", opening=""),
    )

    complete_with_llm(p)

    assert (p.garment_type, p.family) == ("Robe", Family.DRESSES)  # never from the LLM
    assert p.fabric_kind == FabricKind.WOVEN  # filled
    assert p.fabrics_raw == "drap de laine, tweed"
    assert p.yardage_max_m is None  # never pre-filled, kept on p.llm for hints
    assert p.llm.yardage_m == D("4.55")
