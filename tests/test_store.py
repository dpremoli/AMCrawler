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


def test_init_adds_pin_and_alert_tables_to_an_older_database(tmp_path):
    import sqlite3
    from contextlib import closing

    path = str(tmp_path / "old.db")
    with closing(sqlite3.connect(path)) as conn:  # a database from before pins and Alerts
        conn.execute("CREATE TABLE hidden(departure_id TEXT PRIMARY KEY, code TEXT, "
                     "hidden_at TEXT)")
        conn.execute("INSERT INTO hidden VALUES ('d9', 'c', 'x')")
        conn.commit()
    s = Store(path)
    s.init()
    s.pin("d1", date(2026, 12, 1), STORE_NOW)
    s.set_alert(dep(id="d1"), 7, STORE_NOW)
    assert s.pinned_ids() == {"d1"} and set(s.alert_thresholds()) == {"d1"}
    assert s.hidden_ids() == {"d9"}


def test_pin_roundtrip(store):
    store.pin("d1", date(2026, 12, 1), STORE_NOW)
    store.pin("d1", date(2026, 12, 1), STORE_NOW)
    store.pin("d2", date(2026, 11, 1), STORE_NOW)
    assert store.pinned_ids() == {"d1", "d2"}
    store.unpin("d1")
    store.unpin("d1")
    assert store.pinned_ids() == {"d2"}


def test_delete_pins_before_keeps_today_and_later(store):
    store.pin("old", date(2026, 10, 27), STORE_NOW)
    store.pin("today", date(2026, 10, 28), STORE_NOW)
    store.pin("later", date(2026, 11, 1), STORE_NOW)
    assert store.delete_pins_before(date(2026, 10, 28)) == 1
    assert store.pinned_ids() == {"today", "later"}


def test_alert_roundtrip_keeps_last_seen_data(store):
    d = dep(id="d1", itinerary_code="0042", name="Patagonia Trek", participants=8,
            max_participants=14)
    store.set_alert(d, 12, STORE_NOW)
    a = store.alerts()[0]
    assert (a.departure_id, a.threshold, a.name, a.itinerary_code) == (
        "d1", 12, "Patagonia Trek", "0042")
    assert (a.start_date, a.end_date) == (d.start_date, d.end_date)
    assert (a.participants, a.max_participants) == (8, 14)
    assert store.alert_thresholds() == {"d1": 12}


def test_setting_an_alert_again_replaces_the_threshold(store):
    store.set_alert(dep(id="d1"), 7, STORE_NOW)
    store.set_alert(dep(id="d1", participants=6), 9, STORE_NOW + H)
    assert [(a.threshold, a.participants) for a in store.alerts()] == [(9, 6)]


def test_refresh_alert_updates_only_the_last_seen_fields(store):
    store.set_alert(dep(id="d1", participants=5), 9, STORE_NOW)
    store.refresh_alert(dep(id="d1", participants=7, name="Nuovo nome", max_participants=12))
    a = store.alerts()[0]
    assert (a.threshold, a.participants, a.name, a.max_participants) == (9, 7, "Nuovo nome", 12)


def test_delete_alert(store):
    store.set_alert(dep(id="d1"), 7, STORE_NOW)
    store.delete_alert("d1")
    store.delete_alert("d1")
    assert store.alerts() == []


def test_hiding_removes_the_pin_and_the_alert(store):
    store.pin("d1", date(2026, 12, 1), STORE_NOW)
    store.pin("d2", date(2026, 12, 1), STORE_NOW)
    store.set_alert(dep(id="d1"), 7, STORE_NOW)
    store.hide("d1", "2026-0001", STORE_NOW)
    assert store.pinned_ids() == {"d2"} and store.alerts() == []
    store.unhide("d1")  # Annulla does not bring them back
    assert store.pinned_ids() == {"d2"} and store.alerts() == []


def test_alert_misses_count_reset_and_survive_a_restore(store):
    store.set_alert(dep(id="d1"), 7, STORE_NOW)
    assert store.alerts()[0].misses == 0
    assert store.note_alert_miss("d1") == 1 and store.note_alert_miss("d1") == 2
    a = store.alerts()[0]
    assert store.claim_alert("d1", 7) and store.alerts() == []
    store.restore_alert(a, STORE_NOW)
    assert store.alerts()[0].misses == 2
    store.refresh_alert(dep(id="d1"))
    assert store.alerts()[0].misses == 0


def test_claim_alert_succeeds_once_and_only_for_the_same_threshold(store):
    store.set_alert(dep(id="d1"), 7, STORE_NOW)
    assert not store.claim_alert("d1", 9)  # replaced meanwhile by another number
    assert store.claim_alert("d1", 7)
    assert not store.claim_alert("d1", 7)


def test_init_adds_the_misses_column_to_an_older_alerts_table(tmp_path):
    import sqlite3
    from contextlib import closing

    path = str(tmp_path / "old.db")
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE alerts(departure_id TEXT PRIMARY KEY, threshold INTEGER, "
                     "name TEXT, itinerary_code TEXT, start_date TEXT, end_date TEXT, "
                     "participants INTEGER, max_participants INTEGER, created_at TEXT)")
        conn.execute("INSERT INTO alerts VALUES ('d1', 7, 'N', '0001', '2026-12-01', "
                     "'2026-12-05', 3, 10, 'x')")
        conn.commit()
    s = Store(path)
    s.init()
    assert s.alerts()[0].misses == 0
