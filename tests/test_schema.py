from decimal import Decimal

import pytest
from django.db import IntegrityError
from django.db.backends.postgresql.psycopg_any import NumericRange

from apps.catalog.models import Brand, MeasureType, Pattern, SizeChart, SizeChartRow


def test_healthz(client):
    assert client.get("/healthz").content == b"ok"


def test_search_text_is_accent_insensitive(user, brand):
    pattern = Pattern.objects.create(owner=user, brand=brand, name="Élise", garment_type="Robe")
    assert "elise" in pattern.search_text
    assert "make my lemonade" in pattern.search_text


def test_brand_name_is_unique_case_insensitive(brand):
    with pytest.raises(IntegrityError):
        Brand.objects.create(name="make my lemonade")


def test_size_chart_belongs_to_brand_xor_pattern(user, brand):
    pattern = Pattern.objects.create(owner=user, brand=brand, name="Rosa", garment_type="Robe")
    with pytest.raises(IntegrityError):
        SizeChart.objects.create(brand=brand, pattern=pattern, measure_type=MeasureType.BODY)


def test_size_chart_row_stores_ranges(brand):
    chart = SizeChart.objects.create(brand=brand, measure_type=MeasureType.BODY)
    SizeChartRow.objects.create(
        chart=chart, position=0, label="38", chest=NumericRange(Decimal(80), Decimal(84), "[]")
    )
    assert SizeChartRow.objects.filter(chest__contains=Decimal(84)).count() == 1


def test_brand_names_sort_in_french_order(db):
    for name in ["Zoé", "Élise", "Anna"]:
        Brand.objects.create(name=name)
    assert list(Brand.objects.values_list("name", flat=True)) == [
        "Anna",
        "Élise",
        "Zoé",
    ]
