import io
import json
from decimal import Decimal as D

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.catalog.forms import PatternForm
from apps.catalog.models import Brand, Difficulty, FabricKind, Family, Pattern, Tag


@pytest.fixture
def pattern(user, brand):
    p = Pattern.objects.create(
        owner=user,
        brand=brand,
        name="Rosa",
        garment_type="Robe",
        family=Family.DRESSES,
        difficulty_raw="Débutant",
        difficulty=Difficulty.BEGINNER,
    )
    return p


@pytest.fixture
def logged(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def other_client(client, django_user_model):
    client.force_login(django_user_model.objects.create_user(username="bob", password="x"))
    return client


def form_data(pattern, **changes):
    """POST data equivalent to the unchanged form, plus changes."""
    form = PatternForm(instance=pattern)
    data = {}
    for name, field in form.fields.items():
        value = form.initial.get(name, field.initial)
        if name == "tags":
            value = [t.pk for t in value] if value else []
        if name in ("brand",) and hasattr(value, "pk"):
            value = value.pk
        if value is None or name == "thumbnail":
            continue
        if isinstance(value, bool) and not value:
            continue
        data[name] = value
    data.update(changes)
    return data


class TestPatternForm:
    def test_changing_a_raw_value_updates_its_normalized_value(self, pattern):
        form = PatternForm(
            form_data(
                pattern,
                garment_type="Pantalon",
                difficulty_raw="3/5",
                fabrics_raw="Jersey (maille)",
                yardage_raw="1,20 – 1,60 m",
            ),
            instance=pattern,
        )
        assert form.is_valid(), form.errors
        p = form.save()
        assert p.family == Family.BOTTOMS
        assert p.difficulty == Difficulty.INTERMEDIATE
        assert p.fabric_kind == FabricKind.KNIT
        assert p.yardage_max_m == D("1.60")

    def test_an_explicit_normalized_value_wins(self, pattern):
        form = PatternForm(
            form_data(pattern, garment_type="Objet", family=Family.ACCESSORIES), instance=pattern
        )
        assert form.is_valid(), form.errors
        assert form.save().family == Family.ACCESSORIES

    def test_unchanged_raw_value_keeps_a_manual_override(self, pattern):
        pattern.difficulty = Difficulty.ADVANCED  # set by hand earlier
        pattern.save()
        form = PatternForm(form_data(pattern, name="Rosa 2"), instance=pattern)
        assert form.is_valid(), form.errors
        assert form.save().difficulty == Difficulty.ADVANCED

    def test_brand_choices_are_shared_brands(self, pattern):
        Brand.objects.create(name="Autre")
        assert {b.name for b in PatternForm().fields["brand"].queryset} == {
            "Make My Lemonade",
            "Autre",
        }


def jpeg(size=(1600, 1200)):
    buf = io.BytesIO()
    Image.new("RGB", size, "red").save(buf, "JPEG")
    return SimpleUploadedFile("photo.jpg", buf.getvalue(), content_type="image/jpeg")


@pytest.fixture
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.STORAGES = {
        **settings.STORAGES,
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    }


class TestEditView:
    def test_get(self, logged, pattern):
        response = logged.get(f"/patterns/{pattern.pk}/edit")
        assert response.status_code == 200
        assert b"Rosa" in response.content

    def test_post_saves_and_indexes_tags(self, logged, pattern):
        tag = Tag.objects.create(group="Manches", value="ballon")
        response = logged.post(
            f"/patterns/{pattern.pk}/edit", form_data(pattern, name="Rosa bis", tags=[tag.pk])
        )
        assert response.status_code == 302
        assert response.url == f"/#pattern-{pattern.pk}"
        pattern.refresh_from_db()
        assert pattern.name == "Rosa bis"
        assert "ballon" in pattern.search_text

    def test_thumbnail_is_resized_to_jpeg(self, logged, pattern, media):
        response = logged.post(
            f"/patterns/{pattern.pk}/edit", {**form_data(pattern), "thumbnail": jpeg()}
        )
        assert response.status_code == 302
        pattern.refresh_from_db()
        with Image.open(pattern.thumbnail) as img:
            assert img.format == "JPEG"
            assert max(img.size) == 800

    def test_rejects_non_image_thumbnail(self, logged, pattern, media):
        bogus = SimpleUploadedFile("x.jpg", b"not an image", content_type="image/jpeg")
        response = logged.post(
            f"/patterns/{pattern.pk}/edit", {**form_data(pattern), "thumbnail": bogus}
        )
        assert response.status_code == 200
        assert "thumbnail" in response.context["form"].errors

    def test_other_user_gets_404(self, other_client, pattern):
        assert other_client.get(f"/patterns/{pattern.pk}/edit").status_code == 404
        assert other_client.post(f"/patterns/{pattern.pk}/edit", {}).status_code == 404


class TestDelete:
    def test_confirm_then_delete(self, logged, pattern):
        assert logged.get(f"/patterns/{pattern.pk}/delete").status_code == 200
        response = logged.post(f"/patterns/{pattern.pk}/delete")
        assert response.status_code == 302
        assert not Pattern.objects.filter(pk=pattern.pk).exists()

    def test_other_user_gets_404(self, other_client, pattern):
        assert other_client.post(f"/patterns/{pattern.pk}/delete").status_code == 404
        assert Pattern.objects.filter(pk=pattern.pk).exists()


class TestQuickEditApi:
    def patch(self, client, pattern, data):
        return client.patch(
            f"/api/patterns/{pattern.pk}", json.dumps(data), content_type="application/json"
        )

    def test_sewn_and_notes(self, logged, pattern):
        response = self.patch(logged, pattern, {"sewn": True, "notes": "En lin, raccourcir 3 cm"})
        assert response.json() == {"sewn": True, "notes": "En lin, raccourcir 3 cm"}
        pattern.refresh_from_db()
        assert pattern.sewn is True
        assert pattern.notes == "En lin, raccourcir 3 cm"

    def test_only_sewn_and_notes_are_editable(self, logged, pattern):
        assert self.patch(logged, pattern, {"name": "Hack"}).status_code == 400

    def test_types_are_checked(self, logged, pattern):
        assert self.patch(logged, pattern, {"sewn": "yes"}).status_code == 400

    def test_other_user_gets_404(self, other_client, pattern):
        assert self.patch(other_client, pattern, {"sewn": True}).status_code == 404


def test_catalogue_exposes_sewn(logged, pattern):
    pattern.sewn = True
    pattern.save()
    row = logged.get("/api/catalogue").json()["patterns"][0]
    assert row["sewn"] is True
