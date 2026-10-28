from datetime import date

import httpx
import pytest
import respx

from tests.conftest import load_json, read_text
from amcrawler.source import (
    BASE_URL,
    SourceError,
    fetch_uncovered,
    itinerary_url,
    make_client,
    parse_catalogue,
    parse_tours_page,
)

def test_parse_tours_page_fields():
    deps, size = parse_tours_page(load_json("tours_page1.json"))
    assert size == 30
    d = deps[0]
    assert d.code.startswith("2026-") and isinstance(d.start_date, date)
    assert d.participants >= 1 and d.max_participants >= d.participants
    assert d.itinerary_code and d.name and d.formula


def test_parse_tours_page_drops_assigned():
    payload = load_json("tours_page1.json")
    assigned = [r["code"] for r in payload["data"]["results"] if r["assignment"]]
    deps, _ = parse_tours_page(payload)
    assert assigned and not set(assigned) & {d.code for d in deps}


@pytest.mark.parametrize(
    "payload",
    [
        {"success": False},
        {"success": True, "data": {}},
        {"success": True, "data": {"results": [{"code": 1}], "maxResultsPerPage": 30}},
    ],
)
def test_parse_tours_page_rejects_bad_shape(payload):
    with pytest.raises(SourceError):
        parse_tours_page(payload)


@respx.mock
def test_fetch_uncovered_paginates_and_stops():
    route = respx.get(f"{BASE_URL}/api/operative/tours").mock(
        side_effect=[
            httpx.Response(200, json=load_json("tours_page1.json")),
            httpx.Response(200, json=load_json("tours_page2.json")),
        ]
    )
    deps = fetch_uncovered(make_client(), pause=0)
    assert route.call_count == 2
    assert route.calls[0].request.url.params["hasAssignment"] == "0"
    assert route.calls[1].request.url.params["spg"] == "2"
    assert len(deps) == len({d.id for d in deps})


@respx.mock
def test_fetch_uncovered_http_error():
    respx.get(f"{BASE_URL}/api/operative/tours").mock(return_value=httpx.Response(503))
    with pytest.raises(SourceError):
        fetch_uncovered(make_client(), pause=0)


def test_parse_catalogue():
    its = {i.code: i for i in parse_catalogue(read_text("bases_sample.js"))}
    assert any(i.coordinator_free for i in its.values())
    pat = next(i for i in its.values() if "AR" in i.countries)
    assert "CL" in pat.countries and "PX" not in pat.countries
    assert all(i.areas for i in its.values())


def test_parse_catalogue_garbage():
    with pytest.raises(SourceError):
        parse_catalogue("var X = 1;")


def test_itinerary_url():
    assert itinerary_url("1410") == "https://www.viaggiavventurenelmondo.it/viaggi/1410"
