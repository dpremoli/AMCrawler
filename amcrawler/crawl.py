"""The crawl job: refresh the catalogue, fetch Uncovered Departures, record a snapshot."""
from __future__ import annotations

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

    previous = store.latest_ok_snapshot()
    if not departures and previous and previous.departure_count >= SANITY_MIN_PREVIOUS:
        log.warning("crawl returned no Departures but the previous snapshot had %d",
                    previous.departure_count)
        store.record_snapshot(now, None, error="suspicious empty result")
        return store.latest_snapshot()

    store.record_snapshot(now, departures)
    log.info("crawl ok: %d Departures", len(departures))
    return store.latest_snapshot()
