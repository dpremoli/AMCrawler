import logging
from datetime import datetime, timedelta, timezone

import pytest
from apscheduler.schedulers.background import BackgroundScheduler

from amcrawler import scheduler
from amcrawler.web import create_app
from tests.conftest import FakeMailer


def _fields(job):
    return {f.name: str(f) for f in job.trigger.fields}


@pytest.fixture
def added_ids(monkeypatch):
    """Record every job id passed to add_job (a one-off may run and vanish at once)."""
    ids = []
    original = BackgroundScheduler.add_job

    def spy(self, *args, **kwargs):
        ids.append(kwargs.get("id"))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(BackgroundScheduler, "add_job", spy)
    return ids


@pytest.fixture
def started(cfg_dev, store, monkeypatch, added_ids):
    """start_scheduler with job functions stubbed so nothing touches the network."""
    monkeypatch.setattr(scheduler, "crawl_job", lambda app: None)
    monkeypatch.setattr(scheduler, "digest_job", lambda app: None)
    app = create_app(cfg_dev, store=store, mailer=FakeMailer(), start_scheduler=False)
    sched = scheduler.start_scheduler(app)
    yield app, sched
    sched.shutdown(wait=False)


def test_minus_minutes():
    assert scheduler.minus_minutes("07:00", 15) == (6, 45)
    assert scheduler.minus_minutes("00:10", 15) == (23, 55)


def test_start_scheduler_registers_jobs(started, added_ids):
    app, sched = started
    jobs = {j.id: j for j in sched.get_jobs()}
    assert {"crawl-3h", "crawl-predigest", "digest"} <= set(jobs)
    assert "crawl-startup" in added_ids
    f = _fields(jobs["digest"])
    assert (f["hour"], f["minute"]) == ("7", "0")
    assert str(jobs["digest"].trigger.timezone) == "Europe/London"
    f = _fields(jobs["crawl-predigest"])
    assert (f["hour"], f["minute"]) == ("6", "45")
    f = _fields(jobs["crawl-3h"])
    assert (f["hour"], f["minute"]) == ("*/3", "0")
    assert app.state.scheduler is sched


def test_startup_crawl_only_when_data_is_stale(cfg_dev, store, monkeypatch, added_ids):
    monkeypatch.setattr(scheduler, "crawl_job", lambda app: None)
    monkeypatch.setattr(scheduler, "digest_job", lambda app: None)
    store.record_snapshot(datetime.now(timezone.utc) - timedelta(minutes=10), [])
    app = create_app(cfg_dev, store=store, mailer=FakeMailer(), start_scheduler=False)
    sched = scheduler.start_scheduler(app)
    try:
        assert "crawl-startup" not in added_ids
    finally:
        sched.shutdown(wait=False)


def test_reschedule_moves_digest(started):
    app, sched = started
    app.state.on_send_time_change("06:30")
    jobs = {j.id: j for j in sched.get_jobs()}
    f = _fields(jobs["digest"])
    assert (f["hour"], f["minute"]) == ("6", "30")
    f = _fields(jobs["crawl-predigest"])
    assert (f["hour"], f["minute"]) == ("6", "15")


def test_jobs_swallow_and_log_errors(cfg_dev, store, monkeypatch, caplog):
    app = create_app(cfg_dev, store=store, mailer=FakeMailer(), start_scheduler=False)

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(scheduler.source, "make_client", boom)
    monkeypatch.setattr(scheduler.digest, "run_digest", boom)
    with caplog.at_level(logging.ERROR, logger="amcrawler"):
        scheduler.crawl_job(app)
        scheduler.digest_job(app)
    assert len([r for r in caplog.records if r.exc_info]) == 2


@pytest.mark.parametrize("bad", ["", "7:00", "24:00", "07:60", "07-00", "٠٧:٠٠", "07:00 "])
def test_parse_hhmm_rejects_bad_values(bad):
    from amcrawler.config import parse_hhmm

    with pytest.raises(ValueError):
        parse_hhmm(bad)


def test_parse_hhmm_ok():
    from amcrawler.config import parse_hhmm

    assert parse_hhmm("07:05") == (7, 5) and parse_hhmm("23:59") == (23, 59)


# --- F2: missed Digest catch-up -------------------------------------------------

def _start_at(cfg_dev, store, monkeypatch, utc_now):
    monkeypatch.setattr(scheduler, "crawl_job", lambda app: None)
    monkeypatch.setattr(scheduler, "digest_job", lambda app: None)
    monkeypatch.setattr(scheduler, "_utcnow", lambda: utc_now)
    app = create_app(cfg_dev, store=store, mailer=FakeMailer(), start_scheduler=False)
    return app, scheduler.start_scheduler(app)


@pytest.fixture
def added_kwargs(monkeypatch):
    """(job id, add_job kwargs) for every add_job call; one-off ("date") jobs are not
    really added, because under the fake clock they would fire and vanish at once."""
    calls = []
    original = BackgroundScheduler.add_job

    def spy(self, func, trigger=None, *args, **kwargs):
        calls.append((kwargs.get("id"), kwargs))
        if trigger == "date":
            return None
        return original(self, func, trigger, *args, **kwargs)

    monkeypatch.setattr(BackgroundScheduler, "add_job", spy)
    return calls


def _added(calls):
    return [i for i, _ in calls]


@pytest.mark.parametrize("hour, minute, expected", [
    (8, 0, True),      # an hour after the 07:00 send time
    (12, 59, True),    # still inside the 6 h window
    (13, 1, False),    # more than 6 h after
    (6, 30, False),    # before send time: the normal job will run
])
def test_catchup_window_at_start(cfg_dev, store, monkeypatch, added_kwargs, hour, minute,
                                 expected):
    now = datetime(2026, 10, 28, hour, minute, tzinfo=timezone.utc)  # London == UTC here
    app, sched = _start_at(cfg_dev, store, monkeypatch, now)
    try:
        assert ("digest-catchup" in _added(added_kwargs)) is expected
    finally:
        sched.shutdown(wait=False)


def test_no_catchup_when_digest_already_ran_today(cfg_dev, store, monkeypatch, added_kwargs):
    now = datetime(2026, 10, 28, 8, 0, tzinfo=timezone.utc)
    sid = store.record_snapshot(now - timedelta(hours=1), [])
    store.record_digest_run(now - timedelta(minutes=30), sid, "sent", [])
    _, sched = _start_at(cfg_dev, store, monkeypatch, now)
    try:
        assert "digest-catchup" not in _added(added_kwargs)
    finally:
        sched.shutdown(wait=False)


def test_catchup_runs_after_the_startup_crawl(cfg_dev, store, monkeypatch, added_kwargs):
    now = datetime(2026, 10, 28, 8, 0, tzinfo=timezone.utc)
    _, sched = _start_at(cfg_dev, store, monkeypatch, now)
    try:
        kwargs = dict(added_kwargs)["digest-catchup"]
        assert kwargs["run_date"] > now + timedelta(minutes=1)
        assert "crawl-startup" in _added(added_kwargs)
    finally:
        sched.shutdown(wait=False)


def test_reschedule_earlier_than_now_schedules_catchup(cfg_dev, store, monkeypatch,
                                                       added_kwargs):
    now = datetime(2026, 10, 28, 8, 0, tzinfo=timezone.utc)
    store.set_send_time("09:00")
    app, sched = _start_at(cfg_dev, store, monkeypatch, now)
    try:
        assert "digest-catchup" not in _added(added_kwargs)
        app.state.on_send_time_change("07:30")
        assert "digest-catchup" in _added(added_kwargs)
        # a later move replaces the pending job instead of stacking a second one
        app.state.on_send_time_change("07:45")
        assert all(k.get("replace_existing") for i, k in added_kwargs
                   if i == "digest-catchup")
    finally:
        sched.shutdown(wait=False)


# --- R3: catch-up skips a day that already has any Digest run --------------------------

@pytest.mark.parametrize("status", ["stale", "failed"])
def test_no_catchup_when_today_has_a_stale_or_failed_run(cfg_dev, store, monkeypatch,
                                                         added_kwargs, status):
    now = datetime(2026, 10, 28, 8, 0, tzinfo=timezone.utc)
    sid = store.record_snapshot(now - timedelta(hours=1), [])
    store.record_digest_run(now - timedelta(minutes=30), sid, status, [], "x")
    app, sched = _start_at(cfg_dev, store, monkeypatch, now)
    try:
        app.state.on_send_time_change("07:30")
        assert "digest-catchup" not in _added(added_kwargs)
    finally:
        sched.shutdown(wait=False)


def test_catchup_when_no_run_today_even_if_yesterday_had_one(cfg_dev, store, monkeypatch,
                                                            added_kwargs):
    now = datetime(2026, 10, 28, 8, 0, tzinfo=timezone.utc)
    sid = store.record_snapshot(now - timedelta(days=1), [])
    store.record_digest_run(now - timedelta(days=1), sid, "stale", [], "x")
    _, sched = _start_at(cfg_dev, store, monkeypatch, now)
    try:
        assert "digest-catchup" in _added(added_kwargs)
    finally:
        sched.shutdown(wait=False)


def test_scheduled_digest_still_retries_after_a_failed_run(store):
    from datetime import date

    now = datetime(2026, 10, 28, 8, 0, tzinfo=timezone.utc)
    store.record_digest_run(now, None, "failed", [], "x")
    assert store.digest_run_on(date(2026, 10, 28), "Europe/London") is None
    assert store.any_digest_run_on(date(2026, 10, 28), "Europe/London") is True
    assert store.any_digest_run_on(date(2026, 10, 27), "Europe/London") is False


# --- R4: the window is real elapsed time, also on DST days -----------------------------

def test_catchup_window_uses_real_elapsed_time_on_a_dst_day(cfg_dev, store, monkeypatch,
                                                            added_kwargs):
    import dataclasses

    # Europe/Rome, 2027-03-28: clocks jump 02:00 -> 03:00. send_time 01:00 CET is 00:00 UTC;
    # 07:30 CEST is 05:30 UTC: 5.5 h really elapsed (wall clock says 6.5 h).
    cfg = dataclasses.replace(cfg_dev, tz="Europe/Rome")
    store.set_send_time("01:00")
    now = datetime(2027, 3, 28, 5, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(scheduler, "crawl_job", lambda app: None)
    monkeypatch.setattr(scheduler, "digest_job", lambda app: None)
    monkeypatch.setattr(scheduler, "_utcnow", lambda: now)
    app = create_app(cfg, store=store, mailer=FakeMailer(), start_scheduler=False)
    sched = scheduler.start_scheduler(app)
    try:
        assert "digest-catchup" in _added(added_kwargs)
    finally:
        sched.shutdown(wait=False)


def test_no_catchup_on_a_day_that_is_not_a_send_day(cfg_dev, store, monkeypatch,
                                                    added_kwargs):
    store.set_digest_weekdays([0])  # 28 Oct 2026 is a Wednesday
    now = datetime(2026, 10, 28, 8, 0, tzinfo=timezone.utc)
    _, sched = _start_at(cfg_dev, store, monkeypatch, now)
    try:
        assert "digest-catchup" not in _added(added_kwargs)
    finally:
        sched.shutdown(wait=False)


def test_no_catchup_while_the_digest_is_paused(cfg_dev, store, monkeypatch, added_kwargs):
    store.set_digest_enabled(False)
    now = datetime(2026, 10, 28, 8, 0, tzinfo=timezone.utc)
    _, sched = _start_at(cfg_dev, store, monkeypatch, now)
    try:
        assert "digest-catchup" not in _added(added_kwargs)
    finally:
        sched.shutdown(wait=False)
