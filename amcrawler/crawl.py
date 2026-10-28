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
SANITY_MIN_PREVIOUS = 10


def run_crawl(store: Store, client: httpx.Client, now: datetime,
              pause: float = 1.0) -> SnapshotInfo:
    fetched = store.catalogue_fetched_at()
    if fetched is None or now - fetched > CATALOGUE_MAX_AGE:
        try:
            catalogue = fetch_catalogue(client)
            store.save_catalogue(catalogue, now)
        except SourceError as exc:
            log.warning("catalogue refresh failed: %s", exc)

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

    previous = store.latest_ok_snapshot()
    if not departures and previous and previous.departure_count >= SANITY_MIN_PREVIOUS:
        log.warning("crawl returned no Departures but the previous snapshot had %d",
                    previous.departure_count)
        store.record_snapshot(now, None, error="suspicious empty result")
        return store.latest_snapshot()

    store.record_snapshot(now, departures)
    log.info("crawl ok: %d Departures", len(departures))
    return store.latest_snapshot()
