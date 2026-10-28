from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from amcrawler import digest
from amcrawler.crawl import UNKNOWN_ITINERARY_REFRESH, run_crawl
from amcrawler.models import Filter
from amcrawler.source import BASE_URL, make_client, parse_tours_page
from amcrawler.source import parse_catalogue
from tests.conftest import TODAY, dep, itin, load_json, mock_bases, read_text

CRAWL_NOW = datetime(2026, 10, 28, 6, 0, tzinfo=timezone.utc)
TOURS = f"{BASE_URL}/api/operative/tours"


def mock_ok():
    tours = respx.get(TOURS).mock(
        side_effect=[
            httpx.Response(200, json=load_json("tours_page1.json")),
            httpx.Response(200, json=load_json("tours_page2.json")),
        ]
    )
    bases = mock_bases()
    return tours, bases


@respx.mock
def test_crawl_records_ok_snapshot_and_catalogue(store):
    mock_ok()
    info = run_crawl(store, make_client(), CRAWL_NOW, pause=0)
    assert info.ok and info.error is None and info.departure_count > 0
    assert info == store.latest_snapshot()
    assert store.itineraries()
    assert store.catalogue_fetched_at() == CRAWL_NOW


@respx.mock
def test_crawl_skips_catalogue_when_fresh(store):
    _, bases = mock_ok()
    full = parse_catalogue(read_text("bases_sample.js"))
    store.save_catalogue(full, CRAWL_NOW - timedelta(hours=23))
    info = run_crawl(store, make_client(), CRAWL_NOW, pause=0)
    assert info.ok
    assert bases.call_count == 0


@respx.mock
def test_crawl_refreshes_stale_catalogue(store):
    _, bases = mock_ok()
    store.save_catalogue([itin()], CRAWL_NOW - timedelta(hours=25))
    run_crawl(store, make_client(), CRAWL_NOW, pause=0)
    assert bases.call_count == 1


@respx.mock
def test_crawl_catalogue_failure_is_non_fatal(store):
    mock_ok()
    respx.get(f"{BASE_URL}/data/bases.js").mock(return_value=httpx.Response(500))
    info = run_crawl(store, make_client(), CRAWL_NOW, pause=0)
    assert info.ok
    assert store.catalogue_fetched_at() is None


@respx.mock
def test_crawl_api_failure_records_error(store):
    mock_bases()
    respx.get(TOURS).mock(return_value=httpx.Response(503))
    info = run_crawl(store, make_client(), CRAWL_NOW, pause=0)
    assert info.ok is False
    assert info.error


@respx.mock
@pytest.mark.parametrize("previous, expected_ok", [(12, False), (3, True)])
def test_crawl_sanity_guard(store, previous, expected_ok):
    store.record_snapshot(
        CRAWL_NOW - timedelta(days=1), [dep(id=f"p{n}", code=f"c{n}") for n in range(previous)]
    )
    mock_bases()
    empty = {"success": True, "data": {"results": [], "maxResultsPerPage": 30}}
    respx.get(TOURS).mock(return_value=httpx.Response(200, json=empty))
    info = run_crawl(store, make_client(), CRAWL_NOW, pause=0)
    assert info.ok is expected_ok
    if not expected_ok:
        assert info.error == "suspicious empty result"


@respx.mock
@pytest.mark.parametrize("previous, new, expected_ok", [(10, 4, False), (10, 5, True),
                                                        (9, 1, True), (40, 19, False)])
def test_crawl_partial_result_guard(store, previous, new, expected_ok):
    store.record_snapshot(
        CRAWL_NOW - timedelta(days=1), [dep(id=f"p{n}", code=f"c{n}") for n in range(previous)]
    )
    store.save_catalogue([itin()], CRAWL_NOW)
    respx.get(TOURS).mock(return_value=httpx.Response(
        200, json=_tours_payload(*[_tour(f"n{n}", "i1") for n in range(new)])))
    info = run_crawl(store, make_client(), CRAWL_NOW, pause=0)
    assert info.ok is expected_ok
    if not expected_ok:
        assert info.error == "suspicious drop"
        assert store.latest_ok_snapshot().departure_count == previous


@respx.mock
def test_crawl_dedupes_departures(store):
    page1 = load_json("tours_page1.json")
    page2 = load_json("tours_page2.json")
    unique = {d.id for p in (page1, page2) for d in parse_tours_page(p)[0]}
    # pagination over live data can repeat a Departure: replay page-1 results on page 2
    page2["data"]["results"] = page2["data"]["results"] + page1["data"]["results"][:3]
    mock_bases()
    respx.get(TOURS).mock(
        side_effect=[httpx.Response(200, json=page1), httpx.Response(200, json=page2)]
    )
    info = run_crawl(store, make_client(), CRAWL_NOW, pause=0)
    assert info.ok
    assert info.departure_count == len(unique)
    assert len(store.snapshot_departures(info.id)) == len(unique)


@respx.mock
def test_crawl_empty_catalogue_keeps_old_one(store):
    store.save_catalogue([itin(id="old", code="9999")], CRAWL_NOW - timedelta(days=2))
    old_fetched = store.catalogue_fetched_at()
    mock_ok()
    mock_bases("var x = {bases: []};")
    info = run_crawl(store, make_client(), CRAWL_NOW, pause=0)
    assert info.ok
    assert store.catalogue_fetched_at() == old_fetched
    assert list(store.itineraries()) == ["old"]


def _bases_text(*items):
    import json

    bases = [{"id": i, "code": i, "name": i, "tourleaderMode": "regular",
              "category": {"name": "Adventure"}, "countries": [{"isoCode2": "TH"}],
              "geographicAreas": [{"name": area, "ordinal": 1}]} for i, area in items]
    return "var B = {bases: " + json.dumps(bases) + "};"


def _tour(dep_id, itinerary_id):
    return {"assignment": None, "id": dep_id, "code": f"2026-{dep_id}", "notes": "",
            "duration": 12, "pax": 5, "paxMax": 15, "status": "open",
            "startDate": "2026-12-01", "endDate": "2026-12-12",
            "base": {"id": itinerary_id, "code": itinerary_id, "name": itinerary_id,
                     "tourleaderMode": "regular", "category": {"name": "Adventure"}}}


def _tours_payload(*tours):
    return {"success": True, "data": {"maxResultsPerPage": 30, "results": list(tours)}}


@respx.mock
def test_new_itinerary_missing_from_catalogue_is_reported_same_day(store):
    """F1: a Departure of an Itinerary the catalogue lags on must not be lost."""
    store.set_digest_filter(Filter(areas=("Asia",)))
    store.save_catalogue([itin(id="i1", areas=("Europa",))], CRAWL_NOW - timedelta(hours=2))
    base = store.record_snapshot(CRAWL_NOW - timedelta(days=1),
                                 [dep(id="a", itinerary_id="i1", areas=("Europa",))])
    store.record_digest_run(CRAWL_NOW - timedelta(days=1), base, "baseline", [])
    respx.get(TOURS).mock(return_value=httpx.Response(
        200, json=_tours_payload(_tour("a", "i1"), _tour("b", "i2"))))
    bases = mock_bases(_bases_text(("i1", "Europa"), ("i2", "Asia")))
    info = run_crawl(store, make_client(), CRAWL_NOW, pause=0)
    assert info.ok and bases.call_count == 1
    assert "i2" in store.itineraries()
    assert [d.id for d in digest.compute_new_entries(store, TODAY)] == ["b"]


@respx.mock
def test_unknown_itinerary_refresh_is_throttled(store):
    store.save_catalogue([itin(id="i1")], CRAWL_NOW - timedelta(hours=2))
    respx.get(TOURS).mock(return_value=httpx.Response(
        200, json=_tours_payload(_tour("a", "i1"), _tour("b", "i2"))))
    bases = mock_bases(_bases_text(("i1", "Europa")))  # i2 still unknown after refresh
    run_crawl(store, make_client(), CRAWL_NOW, pause=0)
    run_crawl(store, make_client(), CRAWL_NOW + timedelta(minutes=30), pause=0)
    assert bases.call_count == 1
    run_crawl(store, make_client(), CRAWL_NOW + UNKNOWN_ITINERARY_REFRESH
              + timedelta(minutes=1), pause=0)
    assert bases.call_count == 2


@respx.mock
def test_no_forced_refresh_when_catalogue_was_just_refreshed(store):
    respx.get(TOURS).mock(return_value=httpx.Response(
        200, json=_tours_payload(_tour("b", "i2"))))
    bases = mock_bases(_bases_text(("i1", "Europa")))  # stale-triggered refresh lacks i2
    run_crawl(store, make_client(), CRAWL_NOW, pause=0)
    assert bases.call_count == 1


# --- R1: a baseline row without catalogue data is unknown, not "previously matching" ----

def _digest_cfg(tmp_path):
    from amcrawler.config import load_config

    return load_config({
        "AMC_PUBLIC_URL": "https://viaggi.example.com",
        "DIGEST_FROM": "maintainer@example.com",
        "DIGEST_TO": "owner@example.com",
        "AMC_DB_PATH": str(tmp_path / "r1.db"),
        "AMC_DEV_NO_AUTH": "1",
    })


def _seed_asia_baseline(store):
    store.set_digest_filter(Filter(areas=("Asia",)))
    store.save_catalogue([itin(id="i1", areas=("Europa",))], CRAWL_NOW - timedelta(hours=25))
    base = store.record_snapshot(CRAWL_NOW - timedelta(days=1),
                                 [dep(id="a", itinerary_id="i1", areas=("Europa",))])
    store.record_digest_run(CRAWL_NOW - timedelta(days=1), base, "baseline", [])


def _digest_at(store, mailer, tmp_path, now):
    return digest.run_digest(store, mailer, _digest_cfg(tmp_path), now)


def _day1_unknown_i2_then_day2(store, tmp_path, bases_day1):
    """Day 1: the catalogue lacks i2 (bases_day1); day 2: it lists i2 under Asia."""
    from tests.conftest import FakeMailer

    _seed_asia_baseline(store)
    mailer = FakeMailer()
    respx.get(TOURS).mock(return_value=httpx.Response(
        200, json=_tours_payload(_tour("a", "i1"), _tour("b", "i2"))))
    respx.get(f"{BASE_URL}/data/bases.js").mock(side_effect=[
        bases_day1, httpx.Response(200, text=_bases_text(("i1", "Europa"), ("i2", "Asia")))])
    run_crawl(store, make_client(), CRAWL_NOW, pause=0)
    assert _digest_at(store, mailer, tmp_path, CRAWL_NOW + timedelta(hours=1)) == "empty"
    day2 = CRAWL_NOW + timedelta(days=1, hours=1)
    run_crawl(store, make_client(), day2, pause=0)
    assert _digest_at(store, mailer, tmp_path, day2 + timedelta(hours=1)) == "sent"
    return mailer


@respx.mock
def test_new_entry_not_lost_when_forced_catalogue_refresh_fails(store, tmp_path):
    mailer = _day1_unknown_i2_then_day2(store, tmp_path, httpx.Response(503))
    assert "2026-b" in mailer.sent[0][2]


@respx.mock
def test_new_entry_not_lost_when_catalogue_lacks_itinerary_on_day_one(store, tmp_path):
    mailer = _day1_unknown_i2_then_day2(
        store, tmp_path, httpx.Response(200, text=_bases_text(("i1", "Europa"))))
    assert "2026-b" in mailer.sent[0][2]


@respx.mock
def test_uncatalogued_departure_without_area_filter_is_not_reported_daily(store, tmp_path):
    from tests.conftest import FakeMailer

    store.save_catalogue([itin(id="i1")], CRAWL_NOW - timedelta(hours=2))
    mailer = FakeMailer()
    respx.get(TOURS).mock(return_value=httpx.Response(
        200, json=_tours_payload(_tour("b", "i2"))))
    mock_bases(_bases_text(("i1", "Europa")))  # i2 never appears
    run_crawl(store, make_client(), CRAWL_NOW - timedelta(days=1), pause=0)
    store.record_digest_run(CRAWL_NOW - timedelta(days=1),
                            store.latest_ok_snapshot().id, "baseline", [])
    run_crawl(store, make_client(), CRAWL_NOW, pause=0)
    assert _digest_at(store, mailer, tmp_path, CRAWL_NOW + timedelta(hours=1)) == "empty"
    assert mailer.sent == []


# --- R2: the drop guard recovers from a genuine drop ----------------------------------

def _crawl_n(store, n_found, when):
    mock_bases()  # spans beyond a day also refresh the catalogue
    respx.get(TOURS).mock(return_value=httpx.Response(
        200, json=_tours_payload(*[_tour(f"n{k}", "i1") for k in range(n_found)])))
    return run_crawl(store, make_client(), when, pause=0)


def _seed_previous(store, n=40):
    store.save_catalogue([itin()], CRAWL_NOW)
    store.record_snapshot(CRAWL_NOW - timedelta(days=1),
                          [dep(id=f"p{k}", code=f"c{k}") for k in range(n)])


@respx.mock
def test_consistent_drops_spanning_24h_are_accepted(store, caplog):
    import logging

    _seed_previous(store)
    infos = []
    with caplog.at_level(logging.WARNING, logger="amcrawler"):
        for k, found in enumerate((10, 10, 11)):
            infos.append(_crawl_n(store, found, CRAWL_NOW + timedelta(hours=12 * k)))
    assert [i.ok for i in infos] == [False, False, True]
    assert infos[0].departure_count == 10 and infos[1].error == "suspicious drop"
    assert store.latest_ok_snapshot().departure_count == 11
    assert "confirmed over 24.0 h (3 crawls); accepting" in caplog.text
    # later crawls compare to the accepted snapshot
    assert _crawl_n(store, 10, CRAWL_NOW + timedelta(hours=36)).ok


@respx.mock
def test_three_consistent_drops_within_9h_stay_rejected(store):
    _seed_previous(store)
    oks = [_crawl_n(store, 10, CRAWL_NOW + timedelta(hours=3 * k)).ok for k in range(4)]
    assert oks == [False] * 4


@respx.mock
def test_rapid_consistent_crawls_within_1h_stay_rejected(store):
    _seed_previous(store)
    oks = [_crawl_n(store, 10, CRAWL_NOW + timedelta(minutes=6 * k)).ok for k in range(10)]
    assert oks == [False] * 10


@respx.mock
def test_extra_crawls_never_shorten_the_span(store):
    _seed_previous(store)
    for when in (CRAWL_NOW, CRAWL_NOW + timedelta(hours=1), CRAWL_NOW + timedelta(hours=2)):
        _crawl_n(store, 10, when)
    assert not _crawl_n(store, 10, CRAWL_NOW + timedelta(hours=23)).ok
    assert _crawl_n(store, 10, CRAWL_NOW + timedelta(hours=24)).ok


@respx.mock
def test_inconsistent_count_restarts_the_streak(store):
    _seed_previous(store)
    _crawl_n(store, 10, CRAWL_NOW)
    _crawl_n(store, 10, CRAWL_NOW + timedelta(hours=12))
    assert not _crawl_n(store, 15, CRAWL_NOW + timedelta(hours=20)).ok  # breaks the streak
    # the streak is the trailing run of consistent rejections: it restarts at +25h
    assert not _crawl_n(store, 10, CRAWL_NOW + timedelta(hours=25)).ok
    assert not _crawl_n(store, 10, CRAWL_NOW + timedelta(hours=30)).ok
    assert not _crawl_n(store, 10, CRAWL_NOW + timedelta(hours=48)).ok
    assert _crawl_n(store, 10, CRAWL_NOW + timedelta(hours=49)).ok


@respx.mock
def test_consistent_empty_results_spanning_24h_are_accepted(store):
    _seed_previous(store)
    oks = [_crawl_n(store, 0, CRAWL_NOW + timedelta(hours=12 * k)).ok for k in range(3)]
    assert oks == [False, False, True]


@respx.mock
def test_inconsistent_drops_stay_rejected(store):
    _seed_previous(store)
    oks = [_crawl_n(store, n, CRAWL_NOW + timedelta(hours=3 * k)).ok
           for k, n in enumerate((10, 15, 10, 0, 0))]
    assert oks == [False, False, False, False, False]


@respx.mock
def test_a_non_suspicious_failure_does_not_count(store):
    _seed_previous(store)
    _crawl_n(store, 10, CRAWL_NOW)
    respx.get(TOURS).mock(return_value=httpx.Response(503))
    run_crawl(store, make_client(), CRAWL_NOW + timedelta(hours=3), pause=0)
    assert not _crawl_n(store, 10, CRAWL_NOW + timedelta(hours=6)).ok
