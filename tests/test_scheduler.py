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
