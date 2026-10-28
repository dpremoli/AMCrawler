from datetime import datetime, timedelta, timezone

from amcrawler.config import load_config
from amcrawler.digest import badged_ids, compute_new_entries, run_digest, today_in
from tests.conftest import TODAY, FakeMailer, dep

DIGEST_NOW = datetime(2026, 10, 28, 7, 0, tzinfo=timezone.utc)
CFG = load_config({
    "AMC_PUBLIC_URL": "https://viaggi.example.com",
    "DIGEST_FROM": "maintainer@example.com",
    "DIGEST_TO": "owner@example.com",
})


def _baseline(store):
    """Snapshot with d1, then the first (baseline) digest run the day before."""
    store.record_snapshot(DIGEST_NOW - timedelta(days=1, hours=1), [dep(id="d1")])
    assert run_digest(store, FakeMailer(), CFG, DIGEST_NOW - timedelta(days=1)) == "baseline"


def test_today_in_uses_timezone():
    late = datetime(2026, 7, 1, 23, 30, tzinfo=timezone.utc)
    assert today_in("Europe/London", late).isoformat() == "2026-07-02"


def test_first_run_is_baseline_and_sends_nothing(store):
    store.record_snapshot(DIGEST_NOW - timedelta(hours=1), [dep(id="d1")])
    mailer = FakeMailer()
    assert run_digest(store, mailer, CFG, DIGEST_NOW) == "baseline"
    assert mailer.sent == []
    assert store.baseline_snapshot_id() == store.latest_ok_snapshot().id


def test_new_entry_sent_and_recorded(store):
    _baseline(store)
    store.record_snapshot(DIGEST_NOW - timedelta(hours=1), [dep(id="d1"), dep(id="d2", name="Nuova")])
    mailer = FakeMailer()
    assert run_digest(store, mailer, CFG, DIGEST_NOW) == "sent"
    to, subject, text, html = mailer.sent[0]
    assert to == "owner@example.com"
    assert subject.startswith("1 nuova partenza")
    assert html
    assert store.last_sent_entry_ids() == {"d2"}


def test_no_new_entries_records_empty_and_advances_baseline(store):
    _baseline(store)
    sid = store.record_snapshot(DIGEST_NOW - timedelta(hours=1), [dep(id="d1")])
    mailer = FakeMailer()
    assert run_digest(store, mailer, CFG, DIGEST_NOW) == "empty"
    assert mailer.sent == []
    assert store.baseline_snapshot_id() == sid


def test_stale_snapshot_sends_warning_and_keeps_baseline(store):
    _baseline(store)
    before = store.baseline_snapshot_id()
    store.record_snapshot(DIGEST_NOW - timedelta(hours=30), [dep(id="d1")])
    store.record_snapshot(DIGEST_NOW - timedelta(hours=2), None, error="HTTP 500")
    mailer = FakeMailer()
    assert run_digest(store, mailer, CFG, DIGEST_NOW) == "stale"
    assert mailer.sent[0][1].startswith("AMCrawler: nessun aggiornamento")
    assert "HTTP 500" in mailer.sent[0][2]
    assert store.baseline_snapshot_id() == before


def test_stale_send_failure_still_records_stale(store):
    store.record_snapshot(DIGEST_NOW - timedelta(hours=30), [dep(id="d1")])
    assert run_digest(store, FakeMailer(fail=True), CFG, DIGEST_NOW) == "stale"
    run = store.recent_digest_runs()[0]
    assert run.status == "stale" and "smtp down" in run.error


def test_no_snapshot_at_all_is_stale(store):
    mailer = FakeMailer()
    assert run_digest(store, mailer, CFG, DIGEST_NOW) == "stale"
    assert len(mailer.sent) == 1
    assert mailer.sent[0][1] == "AMCrawler: nessun aggiornamento riuscito finora"


def test_smtp_failure_records_failed(store):
    _baseline(store)
    before = store.baseline_snapshot_id()
    store.record_snapshot(DIGEST_NOW - timedelta(hours=1), [dep(id="d1"), dep(id="d2")])
    assert run_digest(store, FakeMailer(fail=True), CFG, DIGEST_NOW) == "failed"
    run = store.recent_digest_runs()[0]
    assert run.status == "failed" and run.error == "smtp down"
    assert store.baseline_snapshot_id() == before


def test_digest_runs_once_per_day(store):
    _baseline(store)
    store.record_snapshot(DIGEST_NOW - timedelta(hours=1), [dep(id="d1"), dep(id="d2")])
    mailer = FakeMailer()
    assert run_digest(store, mailer, CFG, DIGEST_NOW) == "sent"
    assert run_digest(store, mailer, CFG, DIGEST_NOW + timedelta(hours=2)) == "skipped"
    assert len(mailer.sent) == 1


def test_badged_ids_union(store):
    _baseline(store)
    store.record_snapshot(DIGEST_NOW - timedelta(hours=1), [dep(id="d1"), dep(id="d2")])
    run_digest(store, FakeMailer(), CFG, DIGEST_NOW)
    store.record_snapshot(DIGEST_NOW + timedelta(hours=1), [dep(id="d1"), dep(id="d2"), dep(id="d3")])
    assert {d.id for d in compute_new_entries(store, TODAY)} == {"d3"}
    assert badged_ids(store, TODAY) == {"d2", "d3"}


def test_compute_new_entries_without_snapshots(store):
    assert compute_new_entries(store, TODAY) == []


def test_failed_send_retried_next_day_diffs_against_baseline(store):
    _baseline(store)  # baseline at S1 = {d1}
    store.record_snapshot(DIGEST_NOW - timedelta(hours=1), [dep(id="d1"), dep(id="d2")])
    assert run_digest(store, FakeMailer(fail=True), CFG, DIGEST_NOW) == "failed"
    later = DIGEST_NOW + timedelta(days=1)
    store.record_snapshot(later - timedelta(hours=1), [dep(id="d1"), dep(id="d2")])
    mailer = FakeMailer()
    assert run_digest(store, mailer, CFG, later) == "sent"
    assert store.last_sent_entry_ids() == {"d2"}


def test_failed_or_stale_run_does_not_block_same_day_retry(store):
    store.record_snapshot(DIGEST_NOW - timedelta(hours=30), [dep(id="d1")])
    assert run_digest(store, FakeMailer(), CFG, DIGEST_NOW) == "stale"
    store.record_snapshot(DIGEST_NOW + timedelta(minutes=10), [dep(id="d1")])
    assert run_digest(store, FakeMailer(), CFG, DIGEST_NOW + timedelta(minutes=20)) != "skipped"


def test_failed_run_does_not_block_same_day_retry(store):
    _baseline(store)
    store.record_snapshot(DIGEST_NOW + timedelta(hours=1), [dep(id="d1"), dep(id="d2")])
    day2 = DIGEST_NOW + timedelta(days=1, hours=2)
    store.record_snapshot(day2 - timedelta(hours=1), [dep(id="d1"), dep(id="d2")])
    assert run_digest(store, FakeMailer(fail=True), CFG, day2) == "failed"
    assert run_digest(store, FakeMailer(), CFG, day2 + timedelta(hours=1)) == "sent"


def test_render_digest_error_records_failed(store, monkeypatch):
    _baseline(store)
    store.record_snapshot(DIGEST_NOW - timedelta(hours=1), [dep(id="d1"), dep(id="d2")])

    def boom(*a, **k):
        raise ValueError("template broke")

    monkeypatch.setattr("amcrawler.digest.render_digest", boom)
    assert run_digest(store, FakeMailer(), CFG, DIGEST_NOW) == "failed"
    run = store.recent_digest_runs()[0]
    assert run.status == "failed" and run.error == "template broke"


def test_render_stale_error_records_stale(store, monkeypatch):
    store.record_snapshot(DIGEST_NOW - timedelta(hours=30), [dep(id="d1")])

    def boom(*a, **k):
        raise ValueError("stale template broke")

    monkeypatch.setattr("amcrawler.digest.render_stale", boom)
    mailer = FakeMailer()
    assert run_digest(store, mailer, CFG, DIGEST_NOW) == "stale"
    assert mailer.sent == []
    run = store.recent_digest_runs()[0]
    assert run.status == "stale" and run.error == "stale template broke"


def test_empty_digest_to_records_failed_without_mailing(store):
    _baseline(store)
    store.record_snapshot(DIGEST_NOW - timedelta(hours=1), [dep(id="d1"), dep(id="d2")])
    cfg = load_config({"AMC_PUBLIC_URL": "https://viaggi.example.com"})
    mailer = FakeMailer(to="")
    assert run_digest(store, mailer, cfg, DIGEST_NOW) == "failed"
    run = store.recent_digest_runs()[0]
    assert run.status == "failed" and run.error == "DIGEST_TO non impostato"
    assert mailer.sent == []


def test_empty_digest_to_on_stale_path_records_error_without_mailing(store):
    store.record_snapshot(DIGEST_NOW - timedelta(hours=30), [dep(id="d1")])
    cfg = load_config({"AMC_PUBLIC_URL": "https://viaggi.example.com"})
    mailer = FakeMailer(to="")
    assert run_digest(store, mailer, cfg, DIGEST_NOW) == "stale"
    run = store.recent_digest_runs()[0]
    assert run.status == "stale" and run.error == "DIGEST_TO non impostato"
    assert mailer.sent == []


def test_empty_snapshot_never_becomes_the_baseline(store):
    d1, d2, d3 = dep(id="d1"), dep(id="d2"), dep(id="d3", name="Terza")
    s1 = store.record_snapshot(DIGEST_NOW - timedelta(days=3), [d1, d2])
    store.record_digest_run(DIGEST_NOW - timedelta(days=3), s1, "baseline", [])
    store.record_snapshot(DIGEST_NOW - timedelta(hours=1), [])  # accepted empty snapshot
    mailer = FakeMailer()
    assert run_digest(store, mailer, CFG, DIGEST_NOW) == "empty"
    assert store.baseline_snapshot_id() == s1
    assert mailer.sent == []
    later = DIGEST_NOW + timedelta(days=1)
    s3 = store.record_snapshot(later - timedelta(hours=1), [d1, d2, d3])
    assert run_digest(store, mailer, CFG, later) == "sent"
    assert store.last_sent_entry_ids() == {"d3"}
    assert store.baseline_snapshot_id() == s3


def test_first_ever_run_with_no_departures_is_a_baseline(store):
    sid = store.record_snapshot(DIGEST_NOW - timedelta(hours=1), [])
    assert run_digest(store, FakeMailer(), CFG, DIGEST_NOW) == "baseline"
    assert store.baseline_snapshot_id() == sid


# --- send days, pause and the "nessuna novità" mail -------------------------------

def _with_new_entry(store):
    _baseline(store)
    store.record_snapshot(DIGEST_NOW - timedelta(hours=1),
                          [dep(id="d1"), dep(id="d2", name="Nuova")])


def test_paused_digest_does_nothing(store, caplog):
    _with_new_entry(store)
    store.set_digest_enabled(False)
    mailer = FakeMailer()
    runs, baseline = store.recent_digest_runs(50), store.baseline_snapshot_id()
    with caplog.at_level("INFO", logger="amcrawler"):
        assert run_digest(store, mailer, CFG, DIGEST_NOW) == "paused"
    assert mailer.sent == []
    assert store.recent_digest_runs(50) == runs
    assert store.baseline_snapshot_id() == baseline
    assert "digest skipped: paused" in caplog.text


def test_not_a_send_day_does_nothing_even_when_stale(store, caplog):
    _with_new_entry(store)
    store.set_digest_weekdays([0])  # DIGEST_NOW is a Wednesday
    mailer = FakeMailer()
    runs = store.recent_digest_runs(50)
    later = DIGEST_NOW + timedelta(days=3)  # the snapshot is stale by then
    with caplog.at_level("INFO", logger="amcrawler"):
        assert run_digest(store, mailer, CFG, later) == "not_send_day"
    assert mailer.sent == [] and store.recent_digest_runs(50) == runs
    assert "digest skipped: not a send day" in caplog.text


def test_send_day_uses_the_local_date(store):
    _with_new_entry(store)
    store.set_digest_weekdays([3])  # Thursday
    cfg = load_config({"AMC_PUBLIC_URL": "https://viaggi.example.com",
                       "DIGEST_FROM": "maintainer@example.com",
                       "DIGEST_TO": "owner@example.com", "TZ": "Pacific/Auckland"})
    now = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)  # Thursday 01:00 in Auckland
    assert run_digest(store, FakeMailer(), cfg, now) == "sent"


def test_after_a_pause_the_next_digest_reports_everything_since_baseline(store):
    _with_new_entry(store)
    store.set_digest_weekdays([0])
    assert run_digest(store, FakeMailer(), CFG, DIGEST_NOW) == "not_send_day"
    next_day = DIGEST_NOW + timedelta(days=1)
    store.record_snapshot(next_day - timedelta(hours=1),
                          [dep(id="d1"), dep(id="d2", name="Nuova"), dep(id="d3", name="Altra")])
    store.set_digest_weekdays([0, 1, 2, 3])
    assert run_digest(store, FakeMailer(), CFG, next_day) == "sent"
    assert store.last_sent_entry_ids() == {"d2", "d3"}


def test_quiet_mail_sent_when_empty_and_asked(store):
    _baseline(store)
    store.record_snapshot(DIGEST_NOW - timedelta(hours=1), [dep(id="d1")])
    store.set_digest_quiet_mail(True)
    mailer = FakeMailer()
    assert run_digest(store, mailer, CFG, DIGEST_NOW) == "empty"
    assert [m[1] for m in mailer.sent] == ["AMCrawler: nessuna novità"]
    assert "https://viaggi.example.com" in mailer.sent[0][2]
    assert store.recent_digest_runs(1)[0].status == "empty"


def test_quiet_mail_also_for_an_empty_snapshot(store):
    _baseline(store)
    store.record_snapshot(DIGEST_NOW - timedelta(hours=1), [])
    store.set_digest_quiet_mail(True)
    mailer = FakeMailer()
    assert run_digest(store, mailer, CFG, DIGEST_NOW) == "empty"
    assert len(mailer.sent) == 1


def test_no_quiet_mail_unless_asked(store):
    _baseline(store)
    store.record_snapshot(DIGEST_NOW - timedelta(hours=1), [dep(id="d1")])
    mailer = FakeMailer()
    assert run_digest(store, mailer, CFG, DIGEST_NOW) == "empty"
    assert mailer.sent == []


def test_no_quiet_mail_for_a_baseline_run(store):
    store.record_snapshot(DIGEST_NOW - timedelta(hours=1), [dep(id="d1")])
    store.set_digest_quiet_mail(True)
    mailer = FakeMailer()
    assert run_digest(store, mailer, CFG, DIGEST_NOW) == "baseline"
    assert mailer.sent == []


def test_quiet_mail_failure_records_failed_and_allows_retry(store):
    _baseline(store)
    sid = store.record_snapshot(DIGEST_NOW - timedelta(hours=1), [dep(id="d1")])
    store.set_digest_quiet_mail(True)
    assert run_digest(store, FakeMailer(fail=True), CFG, DIGEST_NOW) == "failed"
    run = store.recent_digest_runs(1)[0]
    assert run.status == "failed" and "smtp down" in run.error
    assert store.digest_run_on(today_in(CFG.tz, DIGEST_NOW), CFG.tz) is None
    mailer = FakeMailer()
    assert run_digest(store, mailer, CFG, DIGEST_NOW) == "empty"
    assert len(mailer.sent) == 1 and store.baseline_snapshot_id() == sid
