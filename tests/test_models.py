from amcrawler.models import Filter


def test_filter_query_roundtrip():
    f = Filter(areas=("Sud Est Asiatico",), formulas=("Discovery", "Adventure"), days_from=14, days_to=120, min_participants=4)
    assert Filter.from_query(f.to_query()) == f


def test_filter_from_query_drops_junk():
    f = Filter.from_query([("dfrom", "abc"), ("pmin", "-3"), ("formula", ""), ("dmax", "20")])
    assert f == Filter(duration_max=20)


def test_filter_dict_roundtrip_and_empty():
    f = Filter(countries=("JP",), duration_min=8)
    assert Filter.from_dict(f.to_dict()) == f
    assert Filter().is_empty() and not f.is_empty()
