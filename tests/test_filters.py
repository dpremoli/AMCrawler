from datetime import timedelta

from amcrawler.filters import apply, matches
from amcrawler.models import Filter
from tests.conftest import TODAY, dep


def test_empty_filter_matches_everything():
    assert matches(dep(), Filter(), TODAY)


def test_or_within_and_across():
    f = Filter(formulas=("Discovery", "Adventure"), countries=("JP",))
    assert not matches(dep(), f, TODAY)
    assert matches(dep(countries=("JP", "KR")), f, TODAY)


def test_window_inclusive():
    f = Filter(days_from=30, days_to=30)
    assert matches(dep(), f, TODAY)
    assert not matches(dep(start_date=TODAY + timedelta(days=31)), f, TODAY)
    assert not matches(dep(start_date=TODAY + timedelta(days=29)), f, TODAY)


def test_duration_and_min_participants():
    assert not matches(dep(duration=12), Filter(duration_max=11), TODAY)
    assert matches(dep(duration=12), Filter(duration_max=12, duration_min=12), TODAY)
    assert not matches(dep(duration=12), Filter(duration_min=13), TODAY)
    assert not matches(dep(participants=3), Filter(min_participants=4), TODAY)
    assert matches(dep(participants=4), Filter(min_participants=4), TODAY)


def test_no_catalogue_fails_area_constraint():
    assert not matches(dep(areas=(), countries=()), Filter(areas=("Sud Est Asiatico",)), TODAY)
    assert not matches(dep(areas=(), countries=()), Filter(countries=("TH",)), TODAY)
    assert matches(dep(areas=(), countries=()), Filter(), TODAY)


def test_apply_keeps_input_order():
    a, b, c = dep(id="a", formula="X"), dep(id="b"), dep(id="c")
    assert [d.id for d in apply([c, a, b], Filter(formulas=("Adventure",)), TODAY)] == ["c", "b"]
