import pytest

from apps.catalog.models import Brand, Difficulty, Family, Pattern, Tag
from apps.dataset.models import AuditRecord, AuditSample
from apps.dataset.sampling import create_sample


@pytest.fixture
def patterns(user, brand):
    out = []
    for i in range(1, 6):
        out.append(
            Pattern.objects.create(
                owner=user,
                brand=brand,
                name=f"Modèle {i}",
                garment_type="Robe",
                family=Family.DRESSES,
                legacy_ref=i,
                difficulty=Difficulty.BEGINNER,
                yardage_raw="2 m",
            )
        )
    Pattern.objects.create(
        owner=user, brand=brand, name="Manuel", garment_type="Recueil",
        family=Family.RESOURCES, legacy_ref=99,
    )  # fmt: skip
    return out


@pytest.fixture
def logged(client, user):
    client.force_login(user)
    return client


def all_ok(**overrides):
    from apps.dataset.fields import FIELDS

    data = {f"verdict-{f.name}": "ok" for f in FIELDS}
    data.update(overrides)
    return data


def test_sample_excludes_resources_and_already_sampled(user, patterns):
    first = create_sample(user, 3, seed=1)
    second = create_sample(user, 10, seed=1)
    assert len(first) == 3
    assert len(second) == 2  # 5 garments, 3 already drawn, the manual excluded
    assert [s.rank for s in AuditSample.objects.all()] == [1, 2, 3, 4, 5]


def test_start_and_index(logged, patterns):
    logged.post("/audit/start", {"size": 2})
    html = logged.get("/audit/").content.decode()
    assert "0 / 2" in html


def test_audit_records_verdicts_and_applies_corrections(logged, user, patterns):
    other_brand = Brand.objects.create(name="Atelier Juste")
    tag = Tag.objects.create(group="Manches", value="courtes")
    sample = create_sample(user, 2, seed=1)[0]
    pattern = sample.pattern

    response = logged.post(
        f"/audit/{sample.pk}/",
        all_ok(
            **{
                "verdict-brand": "wrong",
                "value-brand": str(other_brand.pk),
                "verdict-difficulty": "wrong",
                "value-difficulty": "advanced",
                "verdict-yardage_raw": "wrong",
                "value-yardage_raw": "1,90 – 2,70 m",
                "verdict-tags": "wrong",
                "value-tags": [str(tag.pk)],
                "verdict-seasons": "unknown",
            }
        ),
    )

    nxt = AuditSample.objects.get(rank=2)
    assert response.url == f"/audit/{nxt.pk}/"
    pattern.refresh_from_db()
    assert pattern.brand == other_brand
    assert pattern.difficulty == "advanced"
    assert str(pattern.yardage_max_m) == "2.70"  # re-derived from the corrected text
    assert list(pattern.tags.all()) == [tag]
    brand_record = AuditRecord.objects.get(sample=sample, field="brand")
    assert (brand_record.verdict, brand_record.corrected) == ("wrong", other_brand.pk)
    assert brand_record.stored != other_brand.pk  # value before the audit is kept
    assert AuditRecord.objects.get(sample=sample, field="seasons").verdict == "unknown"
    sample.refresh_from_db()
    assert sample.done


def test_every_field_needs_a_verdict(logged, user, patterns):
    sample = create_sample(user, 1, seed=1)[0]
    data = all_ok()
    del data["verdict-name"]
    response = logged.post(f"/audit/{sample.pk}/", data)
    assert response.status_code == 200
    assert "Choisissez" in response.content.decode()
    assert not AuditRecord.objects.exists()


def test_invalid_correction_is_rejected(logged, user, patterns):
    sample = create_sample(user, 1, seed=1)[0]
    response = logged.post(
        f"/audit/{sample.pk}/", all_ok(**{"verdict-family": "wrong", "value-family": "nope"})
    )
    assert response.status_code == 200
    assert "Correction invalide" in response.content.decode()


def test_report(logged, user, patterns):
    samples = create_sample(user, 2, seed=1)
    logged.post(f"/audit/{samples[0].pk}/", all_ok(**{"verdict-name": "wrong", "value-name": "X"}))
    logged.post(f"/audit/{samples[1].pk}/", all_ok())
    html = logged.get("/audit/report").content.decode()
    assert "50%" in html  # name: 1 ok, 1 wrong
    assert "100%" in html


def test_other_user_cannot_audit(client, user, patterns, django_user_model):
    sample = create_sample(user, 1, seed=1)[0]
    client.force_login(django_user_model.objects.create_user(username="bob", password="x"))
    assert client.get(f"/audit/{sample.pk}/").status_code == 404
