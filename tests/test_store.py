from datetime import date, datetime, timedelta, timezone

from amcrawler.models import Filter
from amcrawler.store import Store
from tests.conftest import dep, itin

NOW = datetime(2026, 10, 28, 6, 0, tzinfo=timezone.utc)
H = timedelta(hours=1)
DAY = timedelta(days=1)


def test_snapshot_roundtrip_joins_catalogue(store):
    store.save_catalogue([itin(id="i1", areas=("Nord Africa",), countries=("EG",))], NOW)
    sid = store.record_snapshot(NOW, [dep(itinerary_id="i1")])
    d = store.snapshot_departures(sid)[0]
    assert d.countries == ("EG",) and d.areas == ("Nord Africa",)
    assert isinstance(d.start_date, date) and isinstance(d.end_date, date)
    assert d == dep(itinerary_id="i1", areas=("Nord Africa",), countries=("EG",))
    assert store.itineraries()["i1"].countries == ("EG",)
    assert store.catalogue_fetched_at() == NOW


def test_save_catalogue_replaces_all(store):
    store.save_catalogue([itin(id="a"), itin(id="b")], NOW)
    store.save_catalogue([itin(id="c")], NOW + H)
    assert set(store.itineraries()) == {"c"}


def test_catalogue_fetched_at_none_when_empty(store):
    assert store.catalogue_fetched_at() is None


def test_snapshot_departure_without_itinerary(store):
    sid = store.record_snapshot(NOW, [dep(itinerary_id="missing", itinerary_code="9999")])
    d = store.snapshot_departures(sid)[0]
    assert d.areas == () and d.countries == () and d.itinerary_code == "9999"


def test_failed_snapshot_not_latest_ok(store):
    ok = store.record_snapshot(NOW, [dep()])
    store.record_snapshot(NOW + H, None, "boom")
    assert store.latest_ok_snapshot().id == ok
    assert store.latest_snapshot().error == "boom"
    assert store.latest_snapshot().ok is False


def test_no_snapshots(store):
    assert store.latest_snapshot() is None and store.latest_ok_snapshot() is None


def test_baseline_ignores_failed_and_stale(store):
    s1 = store.record_snapshot(NOW, [])
    store.record_digest_run(NOW, s1, "empty", [])
    s2 = store.record_snapshot(NOW + DAY, [])
    store.record_digest_run(NOW + DAY, s2, "failed", [], "smtp")
    store.record_digest_run(NOW + 2 * DAY, None, "stale", [])
    assert store.baseline_snapshot_id() == s1


def test_has_digest_runs(store):
    assert not store.has_digest_runs() and store.baseline_snapshot_id() is None
    store.record_digest_run(NOW, None, "stale", [])
    assert store.has_digest_runs()


def test_last_sent_entry_ids(store):
    s = store.record_snapshot(NOW, [])
    store.record_digest_run(NOW, s, "sent", ["a", "b"])
    store.record_digest_run(NOW + DAY, s, "sent", ["c"])
    store.record_digest_run(NOW + 2 * DAY, s, "empty", [])
    assert store.last_sent_entry_ids() == {"c"}


def test_recent_digest_runs_newest_first(store):
    s = store.record_snapshot(NOW, [])
    store.record_digest_run(NOW, s, "baseline", ["a"])
    store.record_digest_run(NOW + DAY, s, "failed", [], "smtp")
    runs = store.recent_digest_runs(limit=10)
    assert [r.status for r in runs] == ["failed", "baseline"]
    assert runs[0].error == "smtp" and runs[1].entry_count == 1
    assert len(store.recent_digest_runs(limit=1)) == 1


def test_digest_run_on_uses_local_date(store):
    s = store.record_snapshot(NOW, [])
    late = datetime(2026, 7, 1, 23, 30, tzinfo=timezone.utc)  # 00:30 BST on 2 Jul
    rid = store.record_digest_run(late, s, "sent", [])
    store.record_digest_run(late + H, s, "failed", [], "x")
    assert store.digest_run_on(date(2026, 7, 2), "Europe/London").id == rid
    assert store.digest_run_on(date(2026, 7, 1), "Europe/London") is None
    assert store.digest_run_on(date(2026, 7, 1), "UTC").id == rid


def test_hidden_roundtrip(store):
    store.hide("d1", "2026-0001", NOW)
    store.hide("d1", "2026-0001", NOW)
    store.hide("d2", "2026-0002", NOW)
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
    old = store.record_snapshot(NOW - 400 * DAY, [dep()])
    new = store.record_snapshot(NOW, [dep()])
    assert store.prune_snapshots(NOW - 365 * DAY) == 1
    assert store.snapshot_departures(old) == []
    assert len(store.snapshot_departures(new)) == 1
    assert store.latest_snapshot().id == new


def test_init_is_idempotent(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    s.init()
    s.save_catalogue([itin()], NOW)
    s.init()
    assert set(s.itineraries()) == {"i1"}
