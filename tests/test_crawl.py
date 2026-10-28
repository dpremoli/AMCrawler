from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from amcrawler.crawl import run_crawl
from amcrawler.source import BASE_URL, make_client, parse_tours_page
from tests.conftest import dep, itin, load_json, read_text

NOW = datetime(2026, 10, 28, 6, 0, tzinfo=timezone.utc)
TOURS = f"{BASE_URL}/api/operative/tours"
BASES = f"{BASE_URL}/data/bases.js"


def mock_ok():
    tours = respx.get(TOURS).mock(
        side_effect=[
            httpx.Response(200, json=load_json("tours_page1.json")),
            httpx.Response(200, json=load_json("tours_page2.json")),
        ]
    )
    bases = respx.get(BASES).mock(
        return_value=httpx.Response(200, text=read_text("bases_sample.js"))
    )
    return tours, bases


@respx.mock
def test_crawl_records_ok_snapshot_and_catalogue(store):
    mock_ok()
    info = run_crawl(store, make_client(), NOW, pause=0)
    assert info.ok and info.error is None and info.departure_count > 0
    assert info == store.latest_snapshot()
    assert store.itineraries()
    assert store.catalogue_fetched_at() == NOW


@respx.mock
def test_crawl_skips_catalogue_when_fresh(store):
    _, bases = mock_ok()
    store.save_catalogue([itin()], NOW - timedelta(hours=23))
    info = run_crawl(store, make_client(), NOW, pause=0)
    assert info.ok
    assert bases.call_count == 0


@respx.mock
def test_crawl_refreshes_stale_catalogue(store):
    _, bases = mock_ok()
    store.save_catalogue([itin()], NOW - timedelta(hours=25))
    run_crawl(store, make_client(), NOW, pause=0)
    assert bases.call_count == 1


@respx.mock
def test_crawl_catalogue_failure_is_non_fatal(store):
    mock_ok()
    respx.get(BASES).mock(return_value=httpx.Response(500))
    info = run_crawl(store, make_client(), NOW, pause=0)
    assert info.ok
    assert store.catalogue_fetched_at() is None


@respx.mock
def test_crawl_api_failure_records_error(store):
    respx.get(BASES).mock(return_value=httpx.Response(200, text=read_text("bases_sample.js")))
    respx.get(TOURS).mock(return_value=httpx.Response(503))
    info = run_crawl(store, make_client(), NOW, pause=0)
    assert info.ok is False
    assert info.error


@respx.mock
@pytest.mark.parametrize("previous, expected_ok", [(12, False), (3, True)])
def test_crawl_sanity_guard(store, previous, expected_ok):
    store.record_snapshot(
        NOW - timedelta(days=1), [dep(id=f"p{n}", code=f"c{n}") for n in range(previous)]
    )
    respx.get(BASES).mock(return_value=httpx.Response(200, text=read_text("bases_sample.js")))
    empty = {"success": True, "data": {"results": [], "maxResultsPerPage": 30}}
    respx.get(TOURS).mock(return_value=httpx.Response(200, json=empty))
    info = run_crawl(store, make_client(), NOW, pause=0)
    assert info.ok is expected_ok
    if not expected_ok:
        assert info.error == "suspicious empty result"


@respx.mock
def test_crawl_dedupes_departures(store):
    page1 = load_json("tours_page1.json")
    page2 = load_json("tours_page2.json")
    unique = {d.id for p in (page1, page2) for d in parse_tours_page(p)[0]}
    # pagination over live data can repeat a Departure: replay page-1 results on page 2
    page2["data"]["results"] = page2["data"]["results"] + page1["data"]["results"][:3]
    respx.get(BASES).mock(return_value=httpx.Response(200, text=read_text("bases_sample.js")))
    respx.get(TOURS).mock(
        side_effect=[httpx.Response(200, json=page1), httpx.Response(200, json=page2)]
    )
    info = run_crawl(store, make_client(), NOW, pause=0)
    assert info.ok
    assert info.departure_count == len(unique)
    assert len(store.snapshot_departures(info.id)) == len(unique)
