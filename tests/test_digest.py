from datetime import datetime, timedelta, timezone

from amcrawler.config import load_config
from amcrawler.digest import badged_ids, compute_new_entries, run_digest, today_in
from tests.conftest import TODAY, FakeMailer, dep

NOW = datetime(2026, 10, 28, 7, 0, tzinfo=timezone.utc)
CFG = load_config({
    "AMC_PUBLIC_URL": "https://viaggi.example.com",
    "DIGEST_FROM": "maintainer@example.com",
    "DIGEST_TO": "owner@example.com",
})


def _baseline(store):
    """Snapshot with d1, then the first (baseline) digest run the day before."""
    store.record_snapshot(NOW - timedelta(days=1, hours=1), [dep(id="d1")])
    assert run_digest(store, FakeMailer(), CFG, NOW - timedelta(days=1)) == "baseline"


def test_today_in_uses_timezone():
    late = datetime(2026, 7, 1, 23, 30, tzinfo=timezone.utc)
    assert today_in("Europe/London", late).isoformat() == "2026-07-02"


def test_first_run_is_baseline_and_sends_nothing(store):
    store.record_snapshot(NOW - timedelta(hours=1), [dep(id="d1")])
    mailer = FakeMailer()
    assert run_digest(store, mailer, CFG, NOW) == "baseline"
    assert mailer.sent == []
    assert store.baseline_snapshot_id() == store.latest_ok_snapshot().id


def test_new_entry_sent_and_recorded(store):
    _baseline(store)
    store.record_snapshot(NOW - timedelta(hours=1), [dep(id="d1"), dep(id="d2", name="Nuova")])
    mailer = FakeMailer()
    assert run_digest(store, mailer, CFG, NOW) == "sent"
    to, subject, text, html = mailer.sent[0]
    assert to == "owner@example.com"
    assert subject.startswith("1 nuova partenza")
    assert html
    assert store.last_sent_entry_ids() == {"d2"}


def test_no_new_entries_records_empty_and_advances_baseline(store):
    _baseline(store)
    sid = store.record_snapshot(NOW - timedelta(hours=1), [dep(id="d1")])
    mailer = FakeMailer()
    assert run_digest(store, mailer, CFG, NOW) == "empty"
    assert mailer.sent == []
    assert store.baseline_snapshot_id() == sid


def test_stale_snapshot_sends_warning_and_keeps_baseline(store):
    _baseline(store)
    before = store.baseline_snapshot_id()
    store.record_snapshot(NOW - timedelta(hours=30), [dep(id="d1")])
    store.record_snapshot(NOW - timedelta(hours=2), None, error="HTTP 500")
    mailer = FakeMailer()
    assert run_digest(store, mailer, CFG, NOW) == "stale"
    assert mailer.sent[0][1].startswith("AMCrawler: nessun aggiornamento")
    assert "HTTP 500" in mailer.sent[0][2]
    assert store.baseline_snapshot_id() == before


def test_stale_send_failure_still_records_stale(store):
    store.record_snapshot(NOW - timedelta(hours=30), [dep(id="d1")])
    assert run_digest(store, FakeMailer(fail=True), CFG, NOW) == "stale"
    run = store.recent_digest_runs()[0]
    assert run.status == "stale" and "smtp down" in run.error


def test_no_snapshot_at_all_is_stale(store):
    mailer = FakeMailer()
    assert run_digest(store, mailer, CFG, NOW) == "stale"
    assert len(mailer.sent) == 1


def test_smtp_failure_records_failed(store):
    _baseline(store)
    before = store.baseline_snapshot_id()
    store.record_snapshot(NOW - timedelta(hours=1), [dep(id="d1"), dep(id="d2")])
    assert run_digest(store, FakeMailer(fail=True), CFG, NOW) == "failed"
    run = store.recent_digest_runs()[0]
    assert run.status == "failed" and run.error == "smtp down"
    assert store.baseline_snapshot_id() == before


def test_digest_runs_once_per_day(store):
    _baseline(store)
    store.record_snapshot(NOW - timedelta(hours=1), [dep(id="d1"), dep(id="d2")])
    mailer = FakeMailer()
    assert run_digest(store, mailer, CFG, NOW) == "sent"
    assert run_digest(store, mailer, CFG, NOW + timedelta(hours=2)) == "skipped"
    assert len(mailer.sent) == 1


def test_badged_ids_union(store):
    _baseline(store)
    store.record_snapshot(NOW - timedelta(hours=1), [dep(id="d1"), dep(id="d2")])
    run_digest(store, FakeMailer(), CFG, NOW)
    store.record_snapshot(NOW + timedelta(hours=1), [dep(id="d1"), dep(id="d2"), dep(id="d3")])
    assert {d.id for d in compute_new_entries(store, TODAY)} == {"d3"}
    assert badged_ids(store, TODAY) == {"d2", "d3"}


def test_compute_new_entries_without_snapshots(store):
    assert compute_new_entries(store, TODAY) == []


def test_failed_send_retried_next_day_diffs_against_baseline(store):
    _baseline(store)  # baseline at S1 = {d1}
    store.record_snapshot(NOW - timedelta(hours=1), [dep(id="d1"), dep(id="d2")])
    assert run_digest(store, FakeMailer(fail=True), CFG, NOW) == "failed"
    later = NOW + timedelta(days=1)
    store.record_snapshot(later - timedelta(hours=1), [dep(id="d1"), dep(id="d2")])
    mailer = FakeMailer()
    assert run_digest(store, mailer, CFG, later) == "sent"
    assert store.last_sent_entry_ids() == {"d2"}


def test_failed_or_stale_run_does_not_block_same_day_retry(store):
    store.record_snapshot(NOW - timedelta(hours=30), [dep(id="d1")])
    assert run_digest(store, FakeMailer(), CFG, NOW) == "stale"
    store.record_snapshot(NOW + timedelta(minutes=10), [dep(id="d1")])
    assert run_digest(store, FakeMailer(), CFG, NOW + timedelta(minutes=20)) != "skipped"


def test_failed_run_does_not_block_same_day_retry(store):
    _baseline(store)
    store.record_snapshot(NOW + timedelta(hours=1), [dep(id="d1"), dep(id="d2")])
    day2 = NOW + timedelta(days=1, hours=2)
    store.record_snapshot(day2 - timedelta(hours=1), [dep(id="d1"), dep(id="d2")])
    assert run_digest(store, FakeMailer(fail=True), CFG, day2) == "failed"
    assert run_digest(store, FakeMailer(), CFG, day2 + timedelta(hours=1)) == "sent"


def test_render_digest_error_records_failed(store, monkeypatch):
    _baseline(store)
    store.record_snapshot(NOW - timedelta(hours=1), [dep(id="d1"), dep(id="d2")])

    def boom(*a, **k):
        raise ValueError("template broke")

    monkeypatch.setattr("amcrawler.digest.render_digest", boom)
    assert run_digest(store, FakeMailer(), CFG, NOW) == "failed"
    run = store.recent_digest_runs()[0]
    assert run.status == "failed" and run.error == "template broke"


def test_render_stale_error_records_stale(store, monkeypatch):
    store.record_snapshot(NOW - timedelta(hours=30), [dep(id="d1")])

    def boom(*a, **k):
        raise ValueError("stale template broke")

    monkeypatch.setattr("amcrawler.digest.render_stale", boom)
    mailer = FakeMailer()
    assert run_digest(store, mailer, CFG, NOW) == "stale"
    assert mailer.sent == []
    run = store.recent_digest_runs()[0]
    assert run.status == "stale" and run.error == "stale template broke"


def test_empty_digest_to_records_failed_without_mailing(store):
    _baseline(store)
    store.record_snapshot(NOW - timedelta(hours=1), [dep(id="d1"), dep(id="d2")])
    cfg = load_config({"AMC_PUBLIC_URL": "https://viaggi.example.com"})
    mailer = FakeMailer()
    assert run_digest(store, mailer, cfg, NOW) == "failed"
    run = store.recent_digest_runs()[0]
    assert run.status == "failed" and run.error == "DIGEST_TO non impostato"
    assert mailer.sent == []


def test_empty_digest_to_on_stale_path_records_error_without_mailing(store):
    store.record_snapshot(NOW - timedelta(hours=30), [dep(id="d1")])
    cfg = load_config({"AMC_PUBLIC_URL": "https://viaggi.example.com"})
    mailer = FakeMailer()
    assert run_digest(store, mailer, cfg, NOW) == "stale"
    run = store.recent_digest_runs()[0]
    assert run.status == "stale" and run.error == "DIGEST_TO non impostato"
    assert mailer.sent == []
