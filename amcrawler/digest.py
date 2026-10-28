"""Daily Digest job: baseline, stale warning, once-per-day guard, New Entry mail."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .config import Config
from .diff import new_entries
from .mailer import render_digest, render_quiet, render_stale
from .models import Departure
from .store import Store

logger = logging.getLogger("amcrawler")

STALE_AFTER = timedelta(hours=24)
# Digest run statuses that mean something is wrong (and the CLI exits non-zero)
ERROR_STATUSES = frozenset({"failed", "stale"})
STATUS_LABELS_IT = {
    "sent": "Inviato",
    "baseline": "Riferimento iniziale",
    "empty": "Nessuna novità",
    "failed": "Invio non riuscito",
    "stale": "Dati non aggiornati",
}
# run_digest results for a day the Owner did not ask for a Digest, with the reason logged
SKIP_REASONS = {"paused": "paused", "not_send_day": "not a send day"}


def to_local(dt: datetime, tz: str) -> datetime:
    return dt.astimezone(ZoneInfo(tz))


def today_in(tz: str, now: datetime) -> date:
    return to_local(now, tz).date()


def compute_new_entries(store: Store, today: date,
                        current: list[Departure] | None = None) -> list[Departure]:
    """New Entries since the baseline; `current` is the latest OK snapshot's Departures
    when the caller has already loaded them."""
    if current is None:
        current = store.latest_ok_departures()
    baseline_id = store.baseline_snapshot_id()
    if baseline_id is None or not current:
        return []
    return new_entries(
        current,
        store.snapshot_departures(baseline_id),
        store.digest_filter(),
        store.hidden_ids(),
        today,
    )


def badged_ids(store: Store, today: date,
               current: list[Departure] | None = None) -> set[str]:
    """Departures shown with the New badge: last sent Digest plus pending New Entries."""
    return store.last_sent_entry_ids() | {
        d.id for d in compute_new_entries(store, today, current)}


def _deliver(mailer, render) -> str | None:
    """Render and send one mail; return the error text on failure (recorded, not raised)."""
    try:
        subject, text, html = render()
        mailer.send(subject, text, html)
    except Exception as e:  # noqa: BLE001
        return str(e)
    return None


def _send_stale(store: Store, mailer, now: datetime, last_ok) -> str:
    hours = None if last_ok is None else int((now - last_ok.taken_at).total_seconds() // 3600)
    latest = store.latest_snapshot()
    last_error = latest.error if latest is not None else None

    def render():
        subject, text = render_stale(hours, last_error)
        return subject, text, None

    error = _deliver(mailer, render)
    store.record_digest_run(now, last_ok.id if last_ok else None, "stale", [], error)
    return "stale"


def _finish_empty(store: Store, mailer, cfg: Config, now: datetime, last_ok,
                  snapshot_id: int | None) -> str:
    """End a send day without New Entries: the "nessuna novità" mail when the Owner asked."""
    if store.digest_quiet_mail():
        error = _deliver(mailer, lambda: render_quiet(store.digest_filter(), cfg.public_url))
        if error is not None:
            store.record_digest_run(now, last_ok.id, "failed", [], error)
            return "failed"
    store.record_digest_run(now, snapshot_id, "empty", [])
    return "empty"


def _run(store: Store, mailer, cfg: Config, now: datetime, today: date) -> str:
    if store.digest_run_on(today, cfg.tz) is not None:
        return "skipped"
    last_ok = store.latest_ok_snapshot()
    if last_ok is None or now - last_ok.taken_at > STALE_AFTER:
        return _send_stale(store, mailer, now, last_ok)
    if not store.has_digest_runs():
        store.record_digest_run(now, last_ok.id, "baseline", [])
        return "baseline"
    if last_ok.departure_count == 0:
        # an empty snapshot is never the baseline: keep the current one, so a recovery
        # does not report every Departure as a New Entry
        return _finish_empty(store, mailer, cfg, now, last_ok, store.baseline_snapshot_id())
    entries = compute_new_entries(store, today)
    if not entries:
        return _finish_empty(store, mailer, cfg, now, last_ok, last_ok.id)
    ids = [d.id for d in entries]

    def render():
        subject, html, text = render_digest(
            entries, store.digest_filter(), cfg.public_url, today)
        return subject, text, html

    error = _deliver(mailer, render)
    if error is not None:
        store.record_digest_run(now, last_ok.id, "failed", ids, error)
        return "failed"
    store.record_digest_run(now, last_ok.id, "sent", ids)
    return "sent"


def skip_reason(store: Store, today: date) -> str | None:
    """Why no Digest goes out on `today`, or None on a send day."""
    if not store.digest_enabled():
        return "paused"
    return None if store.is_send_day(today) else "not_send_day"


def run_digest(store: Store, mailer, cfg: Config, now: datetime) -> str:
    today = today_in(cfg.tz, now)
    skipped = skip_reason(store, today)
    if skipped is not None:
        # does nothing at all: no run, no mail, no stale warning, baseline untouched
        logger.info("digest skipped: %s", SKIP_REASONS[skipped])
        return skipped
    try:
        return _run(store, mailer, cfg, now, today)
    finally:
        store.prune_snapshots(now - timedelta(days=365))
