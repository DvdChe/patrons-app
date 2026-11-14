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


def test_merge_policy(fake_ollama):
    p = Proposal(
        family=Family.DRESSES,
        garment_type="Robe",
        size_range="36 – 44",
        seam_allowance="included",
        seam_allowance_detail="1,5 cm",
        fabric_kind=FabricKind.UNSPECIFIED,
        document=DocumentText(text=BOOKLET, opening=BOOKLET[:200]),
    )

    complete_with_llm(p, brands=[(7, "Atelier Rosa", [])])

    # Rules win where they answered
    assert (p.garment_type, p.family) == ("Robe", Family.DRESSES)
    assert p.size_range == "36 – 44"
    # The LLM fills the gaps
    assert p.fabric_kind == FabricKind.WOVEN
    assert (p.difficulty, p.difficulty_raw) == ("beginner", "Facile")
    assert p.brand_id == 7  # brand name read by the LLM, matched to a known brand
    # The LLM wins on yardage and seam width (measured better than the rules)
    assert (p.yardage_raw, p.yardage_max_m) == ("2,10 m", D("2.10"))
    assert p.seam_allowance_detail == "1 cm"
    assert p.llm_fields == {
        "fabric_kind", "fabrics_raw", "difficulty", "difficulty_raw", "brand",
        "yardage_raw", "yardage_max_m", "seam_allowance_detail",
    }  # fmt: skip


def test_brand_examples_use_known_patterns_and_cache_their_text(
    fake_ollama, user, brand, settings, tmp_path
):
    from apps.catalog.files import Entry, store_blob
    from apps.catalog.models import Pattern, PatternFile
    from apps.ingestion.examples import brand_examples
    from apps.ingestion.models import BookletText

    from .pdfs import make_pdf

    settings.MEDIA_ROOT = tmp_path
    settings.STORAGES = {
        **settings.STORAGES,
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    }
    known = Pattern.objects.create(
        owner=user, brand=brand, name="Ancienne", garment_type="Robe", yardage_raw="1,80 m",
        yardage_max_m=D("1.80"),
    )  # fmt: skip
    data = make_pdf([["Métrage : 1,80 m en laize 140 pour la plus grande taille"]])
    entry = Entry("Livret.pdf", len(data), lambda: io.BytesIO(data))
    name, digest, _ = store_blob(user.pk, entry)
    PatternFile.objects.create(
        pattern=known,
        file=name,
        original_name="Livret.pdf",
        kind="booklet",
        sha256=digest,
        size=len(data),
    )

    examples = brand_examples(user, brand.pk)

    assert examples["yardage"][0][1] == {"yardage_m": 1.8}
    assert "1,80 m en laize 140" in examples["yardage"][0][0]
    assert BookletText.objects.count() == 1
    brand_examples(user, brand.pk)  # second time from the cache
    assert BookletText.objects.count() == 1
    assert brand_examples(user, brand.pk, exclude=known.pk) == {}


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
