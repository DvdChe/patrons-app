"""Size recommendation. Behaviour ported from the MVP (`template.html`: locate, mainZones,
evalTab, fits and the ease computation of the pattern sheet)."""

from decimal import Decimal as D

import pytest
from django.db.backends.postgresql.psycopg_any import NumericRange

from apps.catalog import sizing as s
from apps.catalog.models import Family, MeasureType, Pattern, SizeChart, SizeChartRow, Tag


def chart(labels, **measures):
    """chart(["36", "38"], chest=[84, (86, 88)]) — int = exact value, tuple = range."""
    return s.Chart(
        labels=labels,
        measures={
            k: [None if v is None else (D(v), D(v)) if isinstance(v, int) else (D(v[0]), D(v[1]))
                for v in values]
            for k, values in measures.items()
        },
    )  # fmt: skip


LABELS = ["36", "38", "40"]


class TestLocate:
    def test_exact(self):
        loc = s.locate([(D(80), D(80)), (D(84), D(84)), (D(88), D(88))], LABELS, D(84))
        assert loc == s.Location(s.Fit.EXACT, index=1, labels=("38",))

    def test_inside_a_range(self):
        loc = s.locate([(D(80), D(83)), (D(84), D(87))], LABELS, D("85.5"))
        assert loc == s.Location(s.Fit.EXACT, index=1, labels=("38",))

    def test_between_two_sizes_points_to_the_upper_one(self):
        loc = s.locate([(D(80), D(80)), (D(84), D(84)), (D(88), D(88))], LABELS, D(86))
        assert loc == s.Location(s.Fit.BETWEEN, index=2, labels=("38", "40"))

    @pytest.mark.parametrize("value", [D(79), D(89)])
    def test_outside_the_chart(self, value):
        loc = s.locate([(D(80), D(80)), (D(84), D(84)), (D(88), D(88))], LABELS, value)
        assert loc == s.Location(s.Fit.OUTSIDE)

    def test_missing_values_are_skipped(self):
        values = [None, (D(84), D(84)), (D(88), D(88))]
        assert s.locate(values, LABELS, D(84)).index == 1
        assert s.locate(values, LABELS, D(80)).fit == s.Fit.OUTSIDE

    def test_no_value_at_all(self):
        assert s.locate([None, None], ["36", "38"], D(84)) is None


class TestMainMeasures:
    def test_bottoms_use_hip_and_waist(self):
        assert s.main_measures(Family.BOTTOMS, set(), chart(LABELS)) == ["hip", "waist"]

    @pytest.mark.parametrize("waist", ["taille élastique", "taille coulissée"])
    def test_bottoms_with_elastic_waist_use_hip_only(self, waist):
        tags = {("Taille", waist)}
        assert s.main_measures(Family.BOTTOMS, tags, chart(LABELS)) == ["hip"]

    @pytest.mark.parametrize(
        "family",
        [Family.DRESSES, Family.TOPS, Family.JUMPSUITS, Family.OUTERWEAR, Family.KNITWEAR,
         Family.SETS],
    )  # fmt: skip
    def test_upper_body_garments_use_chest(self, family):
        assert s.main_measures(family, set(), chart(LABELS)) == ["chest"]

    def test_other_families_use_chest_when_the_chart_has_it(self):
        with_chest = chart(LABELS, chest=[80, 84, 88])
        assert s.main_measures(Family.LINGERIE, set(), with_chest) == ["chest"]
        assert s.main_measures(Family.LINGERIE, set(), chart(LABELS, hip=[90, 94, 98])) == ["hip"]


class TestEvaluate:
    BODY = chart(LABELS, chest=[80, 84, 88], waist=[62, 66, 70], hip=[88, 92, 96])

    def test_recommends_the_main_measure_size_and_flags_grading(self):
        profile = {"chest": D(88), "waist": D(66), "hip": D(92)}
        ev = s.evaluate(self.BODY, profile, ["chest"])
        assert ev.recommended_index == 2
        assert ev.main == {"chest": s.Location(s.Fit.EXACT, 2, ("40",))}
        assert set(ev.results) == {"chest", "waist", "hip"}
        assert ev.needs_grading is True

    def test_no_grading_when_all_measures_agree(self):
        profile = {"chest": D(84), "waist": D(66), "hip": D(92)}
        assert s.evaluate(self.BODY, profile, ["chest"]).needs_grading is False

    def test_main_measures_missing_from_the_chart_are_ignored(self):
        ev = s.evaluate(chart(LABELS, hip=[88, 92, 96]), {"hip": D(92)}, ["hip", "waist"])
        assert list(ev.main) == ["hip"]
        assert ev.recommended_index == 1

    def test_nothing_to_recommend_without_the_main_measure(self):
        ev = s.evaluate(self.BODY, {"waist": D(66)}, ["chest"])
        assert ev.main == {}
        assert ev.recommended_index is None

    def test_no_recommendation_outside_the_chart(self):
        ev = s.evaluate(self.BODY, {"chest": D(120)}, ["chest"])
        assert ev.main["chest"].fit == s.Fit.OUTSIDE
        assert ev.recommended_index is None


class TestFits:
    BODY = chart(LABELS, chest=[80, 84, 88])

    def test_unknown_without_body_chart(self):
        assert s.fits([], {"chest": D(84)}, ["chest"]) is None

    def test_unknown_without_the_main_measure(self):
        assert s.fits([self.BODY], {"hip": D(90)}, ["chest"]) is None

    def test_inside(self):
        assert s.fits([self.BODY], {"chest": D(86)}, ["chest"]) is True

    def test_outside(self):
        assert s.fits([self.BODY], {"chest": D(100)}, ["chest"]) is False

    def test_any_chart_is_enough(self):
        big = chart(["44", "46"], chest=[96, 100])
        assert s.fits([self.BODY, big], {"chest": D(100)}, ["chest"]) is True


class TestEase:
    def test_finished_minus_body_for_the_recommended_size(self):
        body = chart(["36 (S)", "38 (M)"], chest=[84, 88])
        finished = chart(["36", "38"], chest=[92, (95, 97)], hip=[None, 100])
        profile = {"chest": D(88), "hip": D("92.5")}

        ease = s.ease(body, 1, [finished], profile)

        # Ranges use their upper bound, like the MVP.
        assert ease == [("38 (M)", "chest", D("9.0")), ("38 (M)", "hip", D("7.5"))]

    def test_sizes_missing_from_the_finished_chart_are_skipped(self):
        body = chart(["36", "38"], chest=[84, 88])
        finished = chart(["40"], chest=[100])
        assert s.ease(body, 1, [finished], {"chest": D(88)}) == []


@pytest.mark.django_db
def test_recommend_from_models(user, brand):
    pattern = Pattern.objects.create(
        owner=user, brand=brand, name="Rosa", garment_type="Pantalon", family=Family.BOTTOMS
    )
    pattern.tags.add(Tag.objects.create(group="Taille", value="taille élastique"))
    brand_chart = SizeChart.objects.create(brand=brand, measure_type=MeasureType.BODY)
    for i, (label, hip) in enumerate([("36", 90), ("38", 94)]):
        SizeChartRow.objects.create(
            chart=brand_chart, position=i, label=label, hip=NumericRange(D(hip), D(hip), "[]")
        )
    pattern.brand_size_charts.add(brand_chart)

    rec = s.recommend(pattern, {"hip": D(94), "waist": D(80)})

    assert rec.main_measures == ["hip"]
    assert rec.recommended_label == "38"
    assert rec.fits is True
