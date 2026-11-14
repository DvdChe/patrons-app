import json
from decimal import Decimal as D

import pytest
from django.db.backends.postgresql.psycopg_any import NumericRange

from apps.catalog.models import (
    Family,
    MeasurementProfile,
    MeasureType,
    Pattern,
    SizeChart,
    SizeChartRow,
    Tag,
)


def exact(v):
    return NumericRange(D(v), D(v), "[]")


@pytest.fixture
def other(django_user_model):
    return django_user_model.objects.create_user(username="bob", password="x")


@pytest.fixture
def dress(user, brand):
    p = Pattern.objects.create(
        owner=user,
        brand=brand,
        name="Rosa",
        garment_type="Robe",
        family=Family.DRESSES,
        seasons=["summer"],
        seasons_inferred=True,
        yardage_raw="1,90 – 2,70 m",
        yardage_max_m=D("2.70"),
    )
    p.tags.add(Tag.objects.create(group="Manches", value="courtes"))
    p.save()  # index tags
    body = SizeChart.objects.create(pattern=p, measure_type=MeasureType.BODY, note="Livret")
    finished = SizeChart.objects.create(pattern=p, measure_type=MeasureType.FINISHED)
    for i, (label, chest, fin) in enumerate([("36", 84, 92), ("38", 88, 96), ("40", 92, 100)]):
        SizeChartRow.objects.create(chart=body, position=i, label=label, chest=exact(chest))
        SizeChartRow.objects.create(chart=finished, position=i, label=label, chest=exact(fin))
    return p


@pytest.fixture
def profile(user):
    return MeasurementProfile.objects.create(owner=user, name="Moi", chest=D(88))


@pytest.fixture
def logged(client, user):
    client.force_login(user)
    return client


@pytest.mark.parametrize(
    "url", ["/", "/api/catalogue", "/api/recommendations", "/api/profiles", "/patterns/1/sheet"]
)
def test_login_required(client, db, url):
    response = client.get(url)
    assert response.status_code == 302
    assert response.url.startswith("/login/")


def test_catalogue_page(logged):
    response = logged.get("/")
    assert response.status_code == 200
    assert b'x-data="catalogue"' in response.content


def test_api_catalogue(logged, dress, other, brand):
    Pattern.objects.create(owner=other, brand=brand, name="Secret", garment_type="Robe")

    data = logged.get("/api/catalogue").json()

    assert [p["name"] for p in data["patterns"]] == ["Rosa"]
    row = data["patterns"][0]
    assert row["family"] == "dresses"
    assert row["seasons_text"] == "Été (déduite)"
    assert row["yardage_max"] == 2.7
    assert row["tags"] == ["Manches|courtes"]
    assert "courtes" in row["search"]
    assert row["has_body_chart"] is True
    assert data["tags"] == {"Manches": ["courtes"]}
    assert ["included", "Comprises"] in data["choices"]["seam"]
    assert "unspecified" not in [c[0] for c in data["choices"]["fabric"]]


def test_api_recommendations(logged, dress, profile):
    data = logged.get(f"/api/recommendations?profile={profile.pk}").json()
    assert data[str(dress.pk)] == {"text": "Taille conseillée : 38", "muted": False, "fits": True}


def test_api_recommendations_without_profile(logged, dress):
    data = logged.get("/api/recommendations").json()
    assert data[str(dress.pk)] == {"text": "", "muted": True, "fits": None}


def test_api_recommendations_ignore_other_users_profile(logged, dress, other):
    foreign = MeasurementProfile.objects.create(owner=other, name="Bob", chest=D(88))
    data = logged.get(f"/api/recommendations?profile={foreign.pk}").json()
    assert data[str(dress.pk)]["text"] == ""


def test_sheet(logged, dress, profile):
    html = logged.get(f"/patterns/{dress.pk}/sheet?profile={profile.pk}").content.decode()
    assert "Rosa" in html
    assert "Poitrine (zone principale) 38" in html
    assert "Aisance en taille 38 (mesure finie − votre mesure) : Poitrine 8 cm" in html
    assert '<td class="hl">38</td>' in html
    assert "Mesures du vêtement fini" in html


def test_sheet_of_another_user_is_404(client, dress, other):
    client.force_login(other)
    assert client.get(f"/patterns/{dress.pk}/sheet").status_code == 404


class TestProfilesApi:
    def test_default_profile_is_created(self, logged, user):
        data = logged.get("/api/profiles").json()
        assert [p["name"] for p in data["profiles"]] == ["Moi"]
        assert MeasurementProfile.objects.filter(owner=user).count() == 1

    def test_create(self, logged, profile):
        response = logged.post(
            "/api/profiles", json.dumps({"name": "Léa"}), content_type="application/json"
        )
        assert response.status_code == 201
        assert response.json()["name"] == "Léa"

    def test_create_duplicate_name(self, logged, profile):
        response = logged.post(
            "/api/profiles", json.dumps({"name": "Moi"}), content_type="application/json"
        )
        assert response.status_code == 400

    def test_update_measures(self, logged, profile):
        response = logged.patch(
            f"/api/profiles/{profile.pk}",
            json.dumps({"measures": {"chest": 90.5, "hip": None, "waist": ""}}),
            content_type="application/json",
        )
        assert response.status_code == 200
        profile.refresh_from_db()
        assert profile.chest == D("90.5")
        assert profile.hip is None

    @pytest.mark.parametrize(
        "measures", [{"chest": -1}, {"chest": 1000}, {"chest": "abc"}, {"shoe": 38}]
    )
    def test_update_rejects_invalid_measures(self, logged, profile, measures):
        response = logged.patch(
            f"/api/profiles/{profile.pk}",
            json.dumps({"measures": measures}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_cannot_delete_last_profile(self, logged, profile):
        assert logged.delete(f"/api/profiles/{profile.pk}").status_code == 400

    def test_delete(self, logged, profile, user):
        second = MeasurementProfile.objects.create(owner=user, name="Léa")
        assert logged.delete(f"/api/profiles/{second.pk}").status_code == 204
        assert not MeasurementProfile.objects.filter(pk=second.pk).exists()

    def test_cannot_touch_other_users_profile(self, client, profile, other):
        client.force_login(other)
        response = client.patch(
            f"/api/profiles/{profile.pk}",
            json.dumps({"name": "x"}),
            content_type="application/json",
        )
        assert response.status_code == 404


def test_login_page(client, db):
    response = client.get("/login/")
    assert response.status_code == 200
    assert b"Se connecter" in response.content
