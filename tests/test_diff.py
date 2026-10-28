from datetime import timedelta

from amcrawler.diff import new_entries
from amcrawler.models import Filter
from tests.conftest import TODAY, dep


def test_widened_filter_is_not_new():
    africa = dep(id="a", areas=("Africa Centrale",))
    f = Filter(areas=("Sud Est Asiatico", "Africa Centrale"))
    assert new_entries([africa], [africa], f, set(), TODAY) == []


def test_crossing_min_participants_is_new():
    f = Filter(min_participants=4)
    result = new_entries([dep(id="x", participants=4)], [dep(id="x", participants=3)], f, set(), TODAY)
    assert [d.id for d in result] == ["x"]


def test_reentry_after_coordinator_dropout_is_new():
    assert [d.id for d in new_entries([dep(id="x")], [], Filter(), set(), TODAY)] == ["x"]


def test_present_at_both_digests_is_not_new():
    assert new_entries([dep(id="x", participants=1)], [dep(id="x", participants=1)], Filter(), set(), TODAY) == []


def test_hidden_never_new():
    assert new_entries([dep(id="x")], [], Filter(), {"x"}, TODAY) == []


def test_sorted_by_start_then_name():
    a = dep(id="a", name="B", start_date=TODAY + timedelta(days=5))
    b = dep(id="b", name="A", start_date=TODAY + timedelta(days=5))
    c = dep(id="c", start_date=TODAY + timedelta(days=1))
    assert [d.id for d in new_entries([a, b, c], [], Filter(), set(), TODAY)] == ["c", "b", "a"]
