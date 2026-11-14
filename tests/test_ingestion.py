"""Adding a pattern: upload, proposal, review, creation. Synthetic files only."""

import io
import json
import zipfile
from decimal import Decimal as D

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.backends.postgresql.psycopg_any import NumericRange
from PIL import Image

from apps.catalog.models import (
    Brand,
    Difficulty,
    MeasureType,
    Pattern,
    PatternFile,
    SeamAllowance,
    SizeChart,
    SizeChartRow,
)
from apps.ingestion.models import IngestionJob

from .pdfs import make_pdf

BOOKLET = make_pdf(
    [
        [
            "Make My Lemonade",
            "Robe Rosa",
            "Taille / Size : 34-46",
            "Niveau couture : intermédiaire",
            "Les marges de couture de 1 cm sont incluses.",
            "Tissus conseillés : viscose, crêpe, lin.",
            "Métrage : 2,10 m en laize 140.",
        ],
        ["Page 2 : montage"],
    ]
)


def png(color="red"):
    buf = io.BytesIO()
    Image.new("RGB", (400, 600), color).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.STORAGES = {
        **settings.STORAGES,
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    }


@pytest.fixture
def logged(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def other(client, django_user_model):
    client.force_login(django_user_model.objects.create_user(username="bob", password="x"))
    return client


def upload(client, *files):
    return client.post("/imports/", {"files": [SimpleUploadedFile(n, d) for n, d in files]})


@pytest.fixture
def job(logged, brand):
    response = upload(logged, ("Livret Rosa.pdf", BOOKLET), ("dessin.png", png()))
    assert response.status_code == 302, response.content
    return IngestionJob.objects.get()


def review_data(job, **changes):
    """Minimal valid review POST."""
    data = {
        "name": "Rosa",
        "brand": job.proposal["brand"] or "",
        "brand_confidence": "certain",
        "garment_type": "Robe",
        "family": "dresses",
        "audience": "women",
        "fabric_kind": "woven",
        "difficulty": "intermediate",
        "seam_allowance": "included",
        "seam_allowance_source": "booklet",
        "thumbnail_ref": "",
        "crop": "",
        "brand_charts": "[]",
        "size_chart": "[]",
    }
    data.update(changes)
    return data


def test_login_required(client, db):
    assert client.get("/imports/").status_code == 302


class TestUpload:
    def test_pdf_is_stored_and_analysed(self, logged, job, brand):
        assert job.files.count() == 2
        booklet = job.files.get(original_name="Livret Rosa.pdf")
        assert booklet.kind == PatternFile.Kind.BOOKLET
        assert booklet.file.read() == BOOKLET
        p = job.proposal
        assert p["brand"] == brand.pk
        assert p["size_range"] == "34 – 46"
        assert p["difficulty"] == Difficulty.INTERMEDIATE
        assert p["seam_allowance"] == SeamAllowance.INCLUDED
        assert p["seam_allowance_detail"] == "1 cm"
        assert p["fabric_kind"] == "woven"
        assert "2,10 m" in p["yardage_hint"]
        # Image first (often the technical drawing), then the booklet pages.
        image = job.files.get(original_name="dessin.png")
        assert job.thumbnail_candidates == [
            {"file": image.pk, "page": None},
            {"file": booklet.pk, "page": 0},
            {"file": booklet.pk, "page": 1},
        ]

    def test_zip_is_unpacked_without_junk(self, logged, brand):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("rosa/Livret Rosa.pdf", BOOKLET)
            z.writestr("rosa/A0.pdf", make_pdf([["A0"]]))
            z.writestr("__MACOSX/rosa/._Livret Rosa.pdf", b"junk")
            z.writestr("rosa/.DS_Store", b"junk")
        assert upload(logged, ("patron-de-couture-rosa-pdf.zip", buf.getvalue())).status_code == 302
        job = IngestionJob.objects.get()
        assert sorted(job.files.values_list("original_name", flat=True)) == [
            "rosa/A0.pdf",
            "rosa/Livret Rosa.pdf",
        ]
        assert job.proposal["name"] == "Rosa"

    @pytest.mark.parametrize("name", ["virus.exe", "notes.docx"])
    def test_rejects_other_formats(self, logged, name):
        response = upload(logged, (name, b"x"))
        assert response.status_code == 200
        assert "format non pris en charge" in response.content.decode()
        assert not IngestionJob.objects.exists()

    def test_rejects_broken_zip(self, logged):
        response = upload(logged, ("patron.zip", b"not a zip"))
        assert "illisible" in response.content.decode()
        assert not IngestionJob.objects.exists()

    def test_pending_jobs_are_listed(self, logged, job):
        assert "Rosa" in logged.get("/imports/").content.decode()


class TestReview:
    def test_form_is_prefilled_with_sources(self, logged, job):
        html = logged.get(f"/imports/{job.pk}/").content.decode()
        assert 'value="34 – 46"' in html
        assert "lu dans le livret" in html
        assert "Trouvé dans le livret (à vérifier)" in html  # yardage hint, not pre-filled
        assert 'name="yardage_raw" value=' not in html

    def test_creates_the_pattern(self, logged, job, user, brand):
        chart = SizeChart.objects.create(brand=brand, measure_type=MeasureType.BODY)
        SizeChartRow.objects.create(
            chart=chart, position=0, label="34", chest=NumericRange(D(80), D(80), "[]")
        )
        booklet = job.files.get(original_name="Livret Rosa.pdf")
        rows = [
            {"label": "34", "chest": "80", "waist": "62-64"},
            {"label": "36", "chest": "84"},
            {"label": "", "chest": ""},  # empty line, ignored
        ]
        response = logged.post(
            f"/imports/{job.pk}/",
            review_data(
                job,
                thumbnail_ref=f"{booklet.pk}:0",
                crop="0.1,0.1,0.6,0.5",
                brand_charts=json.dumps([chart.pk]),
                size_chart=json.dumps(rows),
                yardage_raw="2,10 m",
            ),
        )

        assert response.status_code == 302, response.context["form"].errors
        pattern = Pattern.objects.get()
        assert response.url == f"/#pattern-{pattern.pk}"
        assert pattern.owner == user
        assert pattern.brand == brand
        assert pattern.yardage_max_m == D("2.10")  # derived from the text typed in
        assert list(pattern.brand_size_charts.all()) == [chart]
        own = pattern.size_charts.get()
        assert [r.label for r in own.rows.all()] == ["34", "36"]
        assert (own.rows.first().waist.lower, own.rows.first().waist.upper) == (62, 64)
        files = {f.original_name: f for f in pattern.files.all()}
        assert set(files) == {"Livret Rosa.pdf", "dessin.png"}
        assert files["Livret Rosa.pdf"].file.name == booklet.file.name  # same blob
        with Image.open(pattern.thumbnail) as img:
            assert img.format == "JPEG"
            assert max(img.size) <= 800
        job.refresh_from_db()
        assert job.status == IngestionJob.Status.DONE
        assert job.pattern == pattern
        # Reopening the job goes to the pattern
        assert logged.get(f"/imports/{job.pk}/").url == f"/#pattern-{pattern.pk}"

    def test_new_brand(self, logged, job):
        response = logged.post(
            f"/imports/{job.pk}/", review_data(job, brand="", new_brand="Atelier Neuf")
        )
        assert response.status_code == 302
        assert Pattern.objects.get().brand.name == "Atelier Neuf"

    def test_new_brand_reuses_existing_name(self, logged, job, brand):
        logged.post(f"/imports/{job.pk}/", review_data(job, brand="", new_brand="make my LEMONADE"))
        assert Brand.objects.count() == 1

    def test_brand_is_required(self, logged, job):
        response = logged.post(f"/imports/{job.pk}/", review_data(job, brand=""))
        assert response.status_code == 200
        assert not Pattern.objects.exists()

    def test_invalid_size_value(self, logged, job):
        rows = [{"label": "34", "chest": "environ 80"}]
        response = logged.post(f"/imports/{job.pk}/", review_data(job, size_chart=json.dumps(rows)))
        assert response.status_code == 200
        assert "environ 80" in response.content.decode()
        assert not Pattern.objects.exists()

    def test_size_labels_without_measures_are_ignored(self, logged, job):
        rows = [{"label": "36"}, {"label": "38", "chest": ""}]
        response = logged.post(f"/imports/{job.pk}/", review_data(job, size_chart=json.dumps(rows)))
        assert response.status_code == 302
        assert not Pattern.objects.get().size_charts.exists()

    def test_rejects_chart_of_another_brand(self, logged, job):
        other_brand = Brand.objects.create(name="Autre")
        chart = SizeChart.objects.create(brand=other_brand, measure_type=MeasureType.BODY)
        response = logged.post(
            f"/imports/{job.pk}/", review_data(job, brand_charts=json.dumps([chart.pk]))
        )
        assert response.status_code == 200
        assert not Pattern.objects.exists()

    def test_rejects_thumbnail_of_another_job(self, logged, job):
        response = logged.post(f"/imports/{job.pk}/", review_data(job, thumbnail_ref="999999:0"))
        assert response.status_code == 200
        assert not Pattern.objects.exists()


def test_preview_is_a_jpeg(logged, job):
    booklet = job.files.get(original_name="Livret Rosa.pdf")
    response = logged.get(f"/imports/{job.pk}/files/{booklet.pk}/pages/0.jpg?px=200")
    assert response["Content-Type"] == "image/jpeg"
    with Image.open(io.BytesIO(response.content)) as img:
        assert max(img.size) == 200
    image = job.files.get(original_name="dessin.png")
    assert logged.get(f"/imports/{job.pk}/files/{image.pk}/image.jpg").status_code == 200
    assert logged.get(f"/imports/{job.pk}/files/{booklet.pk}/pages/99.jpg").status_code == 404


def test_discard(logged, job):
    assert logged.post(f"/imports/{job.pk}/discard").status_code == 302
    assert not IngestionJob.objects.exists()


def test_other_user_cannot_see_or_touch_a_job(job, other):
    booklet = job.files.first()
    assert other.get(f"/imports/{job.pk}/").status_code == 404
    assert other.get(f"/imports/{job.pk}/files/{booklet.pk}/pages/0.jpg").status_code == 404
    assert other.post(f"/imports/{job.pk}/discard").status_code == 404
    assert IngestionJob.objects.filter(pk=job.pk).exists()


def test_brand_charts_api_tells_which_charts_cover_the_sizes(logged, brand):
    chart = SizeChart.objects.create(brand=brand, measure_type=MeasureType.BODY)
    for i, label in enumerate(["34", "36", "38", "40", "42", "44", "46"]):
        SizeChartRow.objects.create(
            chart=chart,
            position=i,
            label=label,
            chest=NumericRange(D(80 + 4 * i), D(80 + 4 * i), "[]"),
        )
    data = logged.get(f"/api/brands/{brand.pk}/charts?sizes=34 – 46").json()["charts"]
    assert data[0]["covers"] is True
    assert data[0]["rows"][0] == ["34", "80"]
    assert (
        logged.get(f"/api/brands/{brand.pk}/charts?sizes=34 – 52").json()["charts"][0]["covers"]
        is False
    )
