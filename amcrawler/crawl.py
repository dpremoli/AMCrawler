"""The crawl job: refresh the catalogue, fetch Uncovered Departures, record a snapshot."""
from __future__ import annotations

import dataclasses
import logging
from datetime import datetime, timedelta

import httpx

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
SUSPICIOUS_ERRORS = ("suspicious empty result", "suspicious drop")


def _confirmed(store: Store, count: int) -> bool:
    """True when the last CONFIRM_SUSPICIOUS crawls were also rejected as suspicious with
    counts within the tolerance of `count` (exactly 0 for an empty result)."""
    earlier = store.recent_rejected_counts(SUSPICIOUS_ERRORS, CONFIRM_SUSPICIOUS)
    return (len(earlier) == CONFIRM_SUSPICIOUS
            and all(abs(c - count) <= CONFIRM_TOLERANCE * count for c in earlier))


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

    previous = store.latest_ok_snapshot()
    if previous and previous.departure_count >= SANITY_MIN_PREVIOUS:
        reason = None
        if not departures:
            reason = "suspicious empty result"
        elif len(departures) < previous.departure_count * SANITY_MIN_RATIO:
            reason = "suspicious drop"
        if reason and _confirmed(store, len(departures)):
            log.warning("suspicious result confirmed after %d crawls; accepting",
                        CONFIRM_SUSPICIOUS + 1)
            reason = None
        if reason:
            log.warning("crawl returned %d Departures but the previous snapshot had %d: %s",
                        len(departures), previous.departure_count, reason)
            store.record_snapshot(now, None, error=reason, fetched_count=len(departures))
            return store.latest_snapshot()

    store.record_snapshot(now, departures)
    log.info("crawl ok: %d Departures", len(departures))
    return store.latest_snapshot()
