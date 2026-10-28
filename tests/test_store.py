from datetime import date, datetime, timedelta, timezone

from amcrawler.models import Filter
from amcrawler.store import Store
from tests.conftest import dep, itin

STORE_NOW = datetime(2026, 10, 28, 6, 0, tzinfo=timezone.utc)
H = timedelta(hours=1)
DAY = timedelta(days=1)


def test_snapshot_roundtrip_joins_catalogue(store):
    store.save_catalogue([itin(id="i1", areas=("Nord Africa",), countries=("EG",))], STORE_NOW)
    sid = store.record_snapshot(STORE_NOW, [dep(itinerary_id="i1")])
    d = store.snapshot_departures(sid)[0]
    assert d.countries == ("EG",) and d.areas == ("Nord Africa",)
    assert isinstance(d.start_date, date) and isinstance(d.end_date, date)
    assert d == dep(itinerary_id="i1", areas=("Nord Africa",), countries=("EG",))
    assert store.itineraries()["i1"].countries == ("EG",)
    assert store.catalogue_fetched_at() == STORE_NOW


def test_save_catalogue_replaces_all(store):
    store.save_catalogue([itin(id="a"), itin(id="b")], STORE_NOW)
    store.save_catalogue([itin(id="c")], STORE_NOW + H)
    assert set(store.itineraries()) == {"c"}


def test_catalogue_fetched_at_none_when_empty(store):
    assert store.catalogue_fetched_at() is None


def test_snapshot_departure_without_itinerary(store):
    sid = store.record_snapshot(STORE_NOW, [dep(itinerary_id="missing", itinerary_code="9999")])
    d = store.snapshot_departures(sid)[0]
    assert d.areas == () and d.countries == () and d.itinerary_code == "9999"


def test_failed_snapshot_not_latest_ok(store):
    ok = store.record_snapshot(STORE_NOW, [dep()])
    store.record_snapshot(STORE_NOW + H, None, "boom")
    assert store.latest_ok_snapshot().id == ok
    assert store.latest_snapshot().error == "boom"
    assert store.latest_snapshot().ok is False


def test_no_snapshots(store):
    assert store.latest_snapshot() is None and store.latest_ok_snapshot() is None


def test_baseline_ignores_failed_and_stale(store):
    s1 = store.record_snapshot(STORE_NOW, [])
    store.record_digest_run(STORE_NOW, s1, "empty", [])
    s2 = store.record_snapshot(STORE_NOW + DAY, [])
    store.record_digest_run(STORE_NOW + DAY, s2, "failed", [], "smtp")
    store.record_digest_run(STORE_NOW + 2 * DAY, None, "stale", [])
    assert store.baseline_snapshot_id() == s1


def test_has_digest_runs(store):
    assert not store.has_digest_runs() and store.baseline_snapshot_id() is None
    store.record_digest_run(STORE_NOW, None, "stale", [])
    assert store.has_digest_runs()


def test_last_sent_entry_ids(store):
    s = store.record_snapshot(STORE_NOW, [])
    store.record_digest_run(STORE_NOW, s, "sent", ["a", "b"])
    store.record_digest_run(STORE_NOW + DAY, s, "sent", ["c"])
    store.record_digest_run(STORE_NOW + 2 * DAY, s, "empty", [])
    assert store.last_sent_entry_ids() == {"c"}


def test_recent_digest_runs_newest_first(store):
    s = store.record_snapshot(STORE_NOW, [])
    store.record_digest_run(STORE_NOW, s, "baseline", ["a"])
    store.record_digest_run(STORE_NOW + DAY, s, "failed", [], "smtp")
    runs = store.recent_digest_runs(limit=10)
    assert [r.status for r in runs] == ["failed", "baseline"]
    assert runs[0].error == "smtp" and runs[1].entry_count == 1
    assert len(store.recent_digest_runs(limit=1)) == 1


def test_digest_run_on_uses_local_date(store):
    s = store.record_snapshot(STORE_NOW, [])
    late = datetime(2026, 7, 1, 23, 30, tzinfo=timezone.utc)  # 00:30 BST on 2 Jul
    rid = store.record_digest_run(late, s, "sent", [])
    store.record_digest_run(late + H, s, "failed", [], "x")
    assert store.digest_run_on(date(2026, 7, 2), "Europe/London").id == rid
    assert store.digest_run_on(date(2026, 7, 1), "Europe/London") is None
    assert store.digest_run_on(date(2026, 7, 1), "UTC").id == rid


def test_hidden_roundtrip(store):
    store.hide("d1", "2026-0001", STORE_NOW)
    store.hide("d1", "2026-0001", STORE_NOW)
    store.hide("d2", "2026-0002", STORE_NOW)
    assert store.hidden_ids() == {"d1", "d2"}
    store.unhide("d1")
    assert store.hidden_ids() == {"d2"}


def test_settings_defaults_and_roundtrip(store):
    assert store.digest_filter() == Filter() and store.send_time() == "07:00"
    store.set_digest_filter(Filter(formulas=("Discovery",)))
    store.set_send_time("06:30")
    assert store.digest_filter().formulas == ("Discovery",)
    assert store.send_time() == "06:30"


def test_prune_snapshots(store):
    old = store.record_snapshot(STORE_NOW - 400 * DAY, [dep()])
    new = store.record_snapshot(STORE_NOW, [dep()])
    assert store.prune_snapshots(STORE_NOW - 365 * DAY) == 1
    assert store.snapshot_departures(old) == []
    assert len(store.snapshot_departures(new)) == 1
    assert store.latest_snapshot().id == new


def test_init_is_idempotent(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    s.init()
    s.save_catalogue([itin()], STORE_NOW)
    s.init()
    assert set(s.itineraries()) == {"i1"}


def test_init_migrates_old_snapshot_departures_adding_catalogued(tmp_path):
    import sqlite3

    from amcrawler.store import Store

    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE itineraries(id TEXT PRIMARY KEY, code TEXT, name TEXT, formula TEXT,
            areas_json TEXT, countries_json TEXT, coordinator_free INTEGER, fetched_at TEXT);
        CREATE TABLE snapshots(id INTEGER PRIMARY KEY, taken_at TEXT, ok INTEGER,
            error TEXT, departure_count INTEGER);
        CREATE TABLE snapshot_departures(snapshot_id INTEGER, departure_id TEXT,
            itinerary_id TEXT, itinerary_code TEXT, code TEXT, name TEXT, formula TEXT,
            start_date TEXT, end_date TEXT, duration INTEGER, participants INTEGER,
            max_participants INTEGER, notes TEXT, PRIMARY KEY(snapshot_id, departure_id));
        INSERT INTO itineraries VALUES('i1','1','n','f','[\"Asia\"]','[\"TH\"]',0,'2026-10-28T00:00:00+00:00');
        INSERT INTO snapshots VALUES(1,'2026-10-28T00:00:00+00:00',1,NULL,2);
        INSERT INTO snapshot_departures VALUES(1,'a','i1','1','c','n','f','2026-12-01','2026-12-12',12,5,15,'');
        INSERT INTO snapshot_departures VALUES(1,'b','i2','2','c','n','f','2026-12-01','2026-12-12',12,5,15,'');
    """)
    conn.commit()
    conn.close()
    s = Store(path)
    s.init()
    s.init()  # idempotent
    got = {d.id: d.catalogued for d in s.snapshot_departures(1)}
    assert got == {"a": True, "b": False}


def test_catalogued_flag_round_trips(store):
    from datetime import datetime, timezone

    from tests.conftest import dep

    sid = store.record_snapshot(datetime(2026, 10, 28, tzinfo=timezone.utc),
                                [dep(id="a", catalogued=False), dep(id="b")])
    assert {d.id: d.catalogued for d in store.snapshot_departures(sid)} == {"a": False, "b": True}


def test_rejected_since_last_ok_filters_errors_and_stops_at_last_ok(store):
    store.record_snapshot(STORE_NOW, None, "suspicious drop", fetched_count=1)  # before OK
    store.record_snapshot(STORE_NOW + H, [dep()])
    a = store.record_snapshot(STORE_NOW + 2 * H, None, "suspicious drop", fetched_count=10)
    store.record_snapshot(STORE_NOW + 3 * H, None, "HTTP 503")
    b = store.record_snapshot(STORE_NOW + 4 * H, None, "suspicious empty result")
    got = store.rejected_since_last_ok(("suspicious empty result", "suspicious drop"))
    assert [s.id for s in got] == [b, a]
    assert got[1].departure_count == 10


def test_latest_nonempty_ok_snapshot_skips_empty_ones(store):
    full = store.record_snapshot(STORE_NOW, [dep()])
    store.record_snapshot(STORE_NOW + H, [])
    assert store.latest_ok_snapshot().id != full
    assert store.latest_nonempty_ok_snapshot().id == full


def test_send_day_settings_defaults_and_roundtrip(store):
    assert store.digest_enabled() is True
    assert store.digest_weekdays() == [0, 1, 2, 3, 4, 5, 6]
    assert store.digest_quiet_mail() is False
    store.set_digest_enabled(False)
    store.set_digest_weekdays([4, 0, 0])
    store.set_digest_quiet_mail(True)
    assert store.digest_enabled() is False
    assert store.digest_weekdays() == [0, 4]
    assert store.digest_quiet_mail() is True


def test_is_send_day_needs_enabled_and_weekday(store):
    wednesday = date(2026, 10, 28)
    assert store.is_send_day(wednesday)
    store.set_digest_weekdays([0, 1])
    assert not store.is_send_day(wednesday) and store.is_send_day(date(2026, 10, 26))
    store.set_digest_weekdays([2])
    assert store.is_send_day(wednesday)
    store.set_digest_enabled(False)
    assert not store.is_send_day(wednesday)
