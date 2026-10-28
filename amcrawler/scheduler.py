"""In-process scheduler: 3-hourly crawls and the daily Digest."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI

from . import crawl, digest, source
from .config import parse_hhmm

logger = logging.getLogger("amcrawler")

STARTUP_CRAWL_AFTER = timedelta(hours=3)


def minus_minutes(hhmm: str, minutes: int) -> tuple[int, int]:
    """Return (hour, minute) of hhmm minus `minutes`, wrapping past midnight."""
    hour, minute = parse_hhmm(hhmm)
    total = (hour * 60 + minute - minutes) % (24 * 60)
    return divmod(total, 60)


def crawl_job(app: FastAPI) -> None:
    """Run one crawl; never raises so the scheduler keeps going."""
    try:
        with source.make_client() as client:
            crawl.run_crawl(app.state.store, client, datetime.now(timezone.utc))
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


def reschedule(sched: BackgroundScheduler, send_time: str) -> None:
    """Move the pre-Digest crawl (send_time - 15 min) and the Digest to send_time."""
    (hour, minute), (pre_hour, pre_minute) = _cron_times(send_time)
    sched.reschedule_job("crawl-predigest", trigger="cron",
                         hour=pre_hour, minute=pre_minute)
    sched.reschedule_job("digest", trigger="cron", hour=hour, minute=minute)


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
    if latest is None or datetime.now(timezone.utc) - latest.taken_at > STARTUP_CRAWL_AFTER:
        sched.add_job(crawl_job, "date", args=[app], id="crawl-startup")

    app.state.scheduler = sched
    app.state.on_send_time_change = lambda hhmm: reschedule(sched, hhmm)
    sched.start()
    return sched
