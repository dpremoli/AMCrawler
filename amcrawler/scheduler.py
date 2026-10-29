"""In-process scheduler: 3-hourly crawls and the daily Digest."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI

from . import crawl, digest, source
from .config import parse_hhmm

logger = logging.getLogger("amcrawler")

STARTUP_CRAWL_AFTER = timedelta(hours=3)
CATCHUP_WINDOW = timedelta(hours=6)
CATCHUP_DELAY = timedelta(minutes=2)  # lets a startup crawl finish first


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def minus_minutes(hhmm: str, minutes: int) -> tuple[int, int]:
    """Return (hour, minute) of hhmm minus `minutes`, wrapping past midnight."""
    hour, minute = parse_hhmm(hhmm)
    total = (hour * 60 + minute - minutes) % (24 * 60)
    return divmod(total, 60)


def crawl_job(app: FastAPI) -> None:
    """Run one crawl, then check the Alerts when its snapshot was accepted; never raises so
    the scheduler keeps going."""
    try:
        with source.make_client() as client:
            crawl.crawl_and_alert(app.state.store, client, app.state.mailer, app.state.cfg,
                                  datetime.now(timezone.utc))
    except Exception:
        logger.exception("crawl job failed")


def digest_job(app: FastAPI) -> None:
    """Run the daily Digest; never raises so the scheduler keeps going."""
    try:
        digest.run_digest(app.state.store, app.state.mailer, app.state.cfg,
                          datetime.now(timezone.utc))
    except Exception:
        logger.exception("digest job failed")


def _cron_times(send_time: str) -> tuple[tuple[int, int], tuple[int, int]]:
    """((hour, minute) of the Digest, (hour, minute) of the pre-Digest crawl)."""
    return parse_hhmm(send_time), minus_minutes(send_time, 15)


def schedule_catchup(sched: BackgroundScheduler, app: FastAPI, send_time: str) -> None:
    """Jobs are in memory, so downtime over send_time (or moving send_time earlier than
    now) would skip today's Digest: queue a one-off run if it is due and has not run."""
    cfg, store = app.state.cfg, app.state.store
    now = _utcnow()
    hour, minute = parse_hhmm(send_time)
    local = now.astimezone(ZoneInfo(cfg.tz))
    due = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    # real elapsed time: a wall-clock difference is an hour off on DST days
    if not (timedelta(0) <= now - due.astimezone(timezone.utc) < CATCHUP_WINDOW):
        return
    # any run today (also failed/stale) stops the catch-up: it would only resend the same
    # mail on every settings save or restart; the scheduled job may still retry later
    if store.any_digest_run_on(local.date(), cfg.tz):
        return
    if not store.is_send_day(local.date()):
        return
    sched.add_job(digest_job, "date", args=[app], id="digest-catchup",
                  run_date=now + CATCHUP_DELAY, replace_existing=True)


def reschedule(sched: BackgroundScheduler, app: FastAPI, send_time: str) -> None:
    """Move the pre-Digest crawl (send_time - 15 min) and the Digest to send_time."""
    (hour, minute), (pre_hour, pre_minute) = _cron_times(send_time)
    sched.reschedule_job("crawl-predigest", trigger="cron",
                         hour=pre_hour, minute=pre_minute)
    sched.reschedule_job("digest", trigger="cron", hour=hour, minute=minute)
    schedule_catchup(sched, app, send_time)


def start_scheduler(app: FastAPI) -> BackgroundScheduler:
    cfg, store = app.state.cfg, app.state.store
    sched = BackgroundScheduler(
        timezone=cfg.tz,
        job_defaults={"coalesce": True, "misfire_grace_time": 3600, "max_instances": 1},
    )
    (hour, minute), (pre_hour, pre_minute) = _cron_times(store.send_time())
    sched.add_job(crawl_job, "cron", args=[app], id="crawl-3h", hour="*/3", minute=0)
    sched.add_job(crawl_job, "cron", args=[app], id="crawl-predigest",
                  hour=pre_hour, minute=pre_minute)
    sched.add_job(digest_job, "cron", args=[app], id="digest", hour=hour, minute=minute)

    latest = store.latest_ok_snapshot()
    if latest is None or _utcnow() - latest.taken_at > STARTUP_CRAWL_AFTER:
        sched.add_job(crawl_job, "date", args=[app], id="crawl-startup")

    schedule_catchup(sched, app, store.send_time())

    app.state.scheduler = sched
    app.state.on_send_time_change = lambda hhmm: reschedule(sched, app, hhmm)
    sched.start()
    return sched
