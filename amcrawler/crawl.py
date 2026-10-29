"""The crawl job: refresh the catalogue, fetch Uncovered Departures, record a snapshot."""
from __future__ import annotations

import dataclasses
import logging
import threading
from datetime import datetime, timedelta

import httpx

from . import alerts
from .config import Config
from .models import Departure
from .source import SourceError, fetch_catalogue, fetch_uncovered
from .store import SnapshotInfo, Store

log = logging.getLogger("amcrawler")

CATALOGUE_MAX_AGE = timedelta(hours=24)
UNKNOWN_ITINERARY_REFRESH = timedelta(hours=1)
SANITY_MIN_PREVIOUS = 10
SANITY_MIN_RATIO = 0.5
CONFIRM_SUSPICIOUS = 2  # earlier rejections that must agree before a drop is accepted
CONFIRM_TOLERANCE = 0.10
CONFIRM_SPAN = timedelta(hours=24)  # the agreeing rejections must reach back this far
SUSPICIOUS_ERRORS = ("suspicious empty result", "suspicious drop")


_RUN_LOCK = threading.Lock()


def _confirmed(store: Store, count: int, now: datetime) -> tuple[timedelta, int] | None:
    """(span, streak length) of the current streak when this suspicious result is confirmed,
    else None.

    The streak is the trailing run of suspicious rejections since the last OK snapshot
    whose counts are all within CONFIRM_TOLERANCE of `count` (0 only matches 0). It confirms
    when it holds at least CONFIRM_SUSPICIOUS rejections and its oldest was taken at least
    CONFIRM_SPAN ago, so extra crawls (startup, CLI) can never shorten the wait."""
    streak = []
    for snap in store.rejected_since_last_ok(SUSPICIOUS_ERRORS):  # newest first
        if abs(snap.departure_count - count) > CONFIRM_TOLERANCE * count:
            break
        streak.append(snap)
    if len(streak) < CONFIRM_SUSPICIOUS:
        return None
    span = now - streak[-1].taken_at
    return (span, len(streak)) if span >= CONFIRM_SPAN else None


def _refresh_catalogue(store: Store, client: httpx.Client, now: datetime) -> None:
    """Try to refresh the catalogue; a failure is logged and never fatal."""
    store.note_catalogue_attempt(now)
    try:
        store.save_catalogue(fetch_catalogue(client), now)
    except SourceError as exc:
        log.warning("catalogue refresh failed: %s", exc)


def run_crawl(store: Store, client: httpx.Client, now: datetime,
              pause: float = 1.0) -> SnapshotInfo:
    fetched = store.catalogue_fetched_at()
    refreshed = fetched is None or now - fetched > CATALOGUE_MAX_AGE
    if refreshed:
        _refresh_catalogue(store, client, now)

    try:
        departures = fetch_uncovered(client, pause=pause)
    except SourceError as exc:
        log.warning("crawl failed: %s", exc)
        store.record_snapshot(now, None, error=str(exc))
        return store.latest_snapshot()

    # pagination over live data can repeat a Departure: keep the first occurrence
    unique: dict[str, Departure] = {}
    for d in departures:
        unique.setdefault(d.id, d)
    departures = list(unique.values())

    # A new Itinerary the catalogue does not know yet has no Areas/Countries, so it
    # would fail an Area/Country Digest Filter and be lost: refresh now (throttled).
    known = store.itineraries() if not refreshed else {}
    if not refreshed and any(d.itinerary_id not in known for d in departures):
        attempted = store.catalogue_attempted_at()
        if attempted is None or now - attempted >= UNKNOWN_ITINERARY_REFRESH:
            log.info("Departure of an unknown Itinerary: refreshing the catalogue")
            _refresh_catalogue(store, client, now)

    # remember which Departures had catalogue data now (see diff.new_entries)
    known = store.itineraries()
    departures = [d if d.itinerary_id in known else dataclasses.replace(d, catalogued=False)
                  for d in departures]

    # an accepted empty snapshot must not switch the guard off for a partial recovery
    previous = store.latest_nonempty_ok_snapshot()
    latest_ok = store.latest_ok_snapshot()
    empty_after_empty = not departures and latest_ok is not None and latest_ok.departure_count == 0
    # empty after an accepted empty snapshot is not a drop (a persistent empty list)
    if previous and not empty_after_empty and previous.departure_count >= SANITY_MIN_PREVIOUS:
        reason = None
        if not departures:
            reason = "suspicious empty result"
        elif len(departures) < previous.departure_count * SANITY_MIN_RATIO:
            reason = "suspicious drop"
        confirmed = _confirmed(store, len(departures), now) if reason else None
        if confirmed is not None:
            span, rejected = confirmed
            log.warning("suspicious result confirmed over %.1f h (%d crawls); accepting",
                        span.total_seconds() / 3600, rejected + 1)
            reason = None
        if reason:
            log.warning("crawl returned %d Departures but the previous snapshot had %d: %s",
                        len(departures), previous.departure_count, reason)
            store.record_snapshot(now, None, error=reason, fetched_count=len(departures))
            return store.latest_snapshot()

    store.record_snapshot(now, departures)
    log.info("crawl ok: %d Departures", len(departures))
    return store.latest_snapshot()


def crawl_and_alert(store: Store, client: httpx.Client, mailer, cfg: Config,
                    now: datetime) -> SnapshotInfo | None:
    """One crawl, then the Alert check and pin cleanup when its snapshot was accepted.

    Scheduled crawls can overlap (3-hourly, pre-Digest, startup): a call made while another
    runs in this process skips and returns None, as that crawl is about to be fresh anyway."""
    if not _RUN_LOCK.acquire(blocking=False):
        log.info("crawl already running: skipped")
        return None
    try:
        snap = run_crawl(store, client, now)
        if snap.ok:
            alerts.run_alerts(store, mailer, cfg, now, store.snapshot_departures(snap.id))
        return snap
    finally:
        _RUN_LOCK.release()
