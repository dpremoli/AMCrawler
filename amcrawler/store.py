"""SQLite store: catalogue, snapshots, digest runs, hidden and settings."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Iterator
from zoneinfo import ZoneInfo

from amcrawler.models import Departure, Filter, Itinerary

_SCHEMA = """
CREATE TABLE IF NOT EXISTS itineraries(
    id TEXT PRIMARY KEY, code TEXT, name TEXT, formula TEXT,
    areas_json TEXT, countries_json TEXT, coordinator_free INTEGER,
    fetched_at TEXT);
CREATE TABLE IF NOT EXISTS snapshots(
    id INTEGER PRIMARY KEY, taken_at TEXT, ok INTEGER, error TEXT,
    departure_count INTEGER);
CREATE TABLE IF NOT EXISTS snapshot_departures(
    snapshot_id INTEGER, departure_id TEXT, itinerary_id TEXT,
    itinerary_code TEXT, code TEXT, name TEXT, formula TEXT,
    start_date TEXT, end_date TEXT, duration INTEGER, participants INTEGER,
    max_participants INTEGER, notes TEXT,
    PRIMARY KEY(snapshot_id, departure_id));
CREATE TABLE IF NOT EXISTS digest_runs(
    id INTEGER PRIMARY KEY, run_at TEXT, snapshot_id INTEGER, status TEXT,
    entry_count INTEGER, error TEXT);
CREATE TABLE IF NOT EXISTS digest_entries(
    digest_run_id INTEGER, departure_id TEXT,
    PRIMARY KEY(digest_run_id, departure_id));
CREATE TABLE IF NOT EXISTS hidden(
    departure_id TEXT PRIMARY KEY, code TEXT, hidden_at TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value_json TEXT);
"""

_BASELINE_STATUSES = ("baseline", "sent", "empty")
_STATUS_MARKS = ",".join("?" * len(_BASELINE_STATUSES))


@dataclass(frozen=True)
class SnapshotInfo:
    id: int
    taken_at: datetime
    ok: bool
    error: str | None
    departure_count: int


@dataclass(frozen=True)
class DigestRun:
    id: int
    run_at: datetime
    snapshot_id: int | None
    status: str
    entry_count: int
    error: str | None


def _ts(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _snapshot(row: sqlite3.Row | None) -> SnapshotInfo | None:
    if row is None:
        return None
    return SnapshotInfo(row["id"], _parse_ts(row["taken_at"]), bool(row["ok"]),
                        row["error"], row["departure_count"])


def _run(row: sqlite3.Row) -> DigestRun:
    return DigestRun(row["id"], _parse_ts(row["run_at"]), row["snapshot_id"],
                     row["status"], row["entry_count"], row["error"])


class Store:
    """Opens a new connection per call, so it is safe across threads."""

    def __init__(self, path: str) -> None:
        self.path = path

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=30)) as conn:
            conn.row_factory = sqlite3.Row
            with conn:  # commit on success, roll back on error
                yield conn

    def init(self) -> None:
        with closing(sqlite3.connect(self.path, timeout=30)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    # catalogue
    def save_catalogue(self, items: list[Itinerary], fetched_at: datetime) -> None:
        stamp = _ts(fetched_at)
        with self._conn() as conn:
            conn.execute("DELETE FROM itineraries")
            conn.executemany(
                "INSERT INTO itineraries VALUES (?,?,?,?,?,?,?,?)",
                [(i.id, i.code, i.name, i.formula, json.dumps(list(i.areas)),
                  json.dumps(list(i.countries)), int(i.coordinator_free), stamp)
                 for i in items],
            )

    def catalogue_fetched_at(self) -> datetime | None:
        with self._conn() as conn:
            row = conn.execute("SELECT MAX(fetched_at) AS t FROM itineraries").fetchone()
        return _parse_ts(row["t"]) if row["t"] else None

    def itineraries(self) -> dict[str, Itinerary]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM itineraries").fetchall()
        return {
            r["id"]: Itinerary(r["id"], r["code"], r["name"], r["formula"],
                               tuple(json.loads(r["areas_json"])),
                               tuple(json.loads(r["countries_json"])),
                               bool(r["coordinator_free"]))
            for r in rows
        }

    # snapshots
    def record_snapshot(self, taken_at: datetime, departures: list[Departure] | None,
                        error: str | None = None) -> int:
        ok = departures is not None
        deps = departures or []
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO snapshots(taken_at, ok, error, departure_count) VALUES (?,?,?,?)",
                (_ts(taken_at), int(ok), error, len(deps)),
            )
            sid = cur.lastrowid
            conn.executemany(
                "INSERT INTO snapshot_departures VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(sid, d.id, d.itinerary_id, d.itinerary_code, d.code, d.name, d.formula,
                  d.start_date.isoformat(), d.end_date.isoformat(), d.duration,
                  d.participants, d.max_participants, d.notes) for d in deps],
            )
        return sid

    def _newest_snapshot(self, where: str) -> SnapshotInfo | None:
        with self._conn() as conn:
            row = conn.execute(
                f"SELECT * FROM snapshots {where} ORDER BY id DESC LIMIT 1").fetchone()
        return _snapshot(row)

    def latest_snapshot(self) -> SnapshotInfo | None:
        return self._newest_snapshot("")

    def latest_ok_snapshot(self) -> SnapshotInfo | None:
        return self._newest_snapshot("WHERE ok = 1")

    def snapshot_departures(self, snapshot_id: int) -> list[Departure]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT sd.*, i.areas_json, i.countries_json FROM snapshot_departures sd "
                "LEFT JOIN itineraries i ON i.id = sd.itinerary_id "
                "WHERE sd.snapshot_id = ? ORDER BY sd.start_date, sd.departure_id",
                (snapshot_id,),
            ).fetchall()
        return [
            Departure(
                id=r["departure_id"], code=r["code"], itinerary_id=r["itinerary_id"],
                itinerary_code=r["itinerary_code"], name=r["name"], formula=r["formula"],
                start_date=date.fromisoformat(r["start_date"]),
                end_date=date.fromisoformat(r["end_date"]),
                duration=r["duration"], participants=r["participants"],
                max_participants=r["max_participants"], notes=r["notes"],
                areas=tuple(json.loads(r["areas_json"])) if r["areas_json"] else (),
                countries=tuple(json.loads(r["countries_json"])) if r["countries_json"] else (),
            )
            for r in rows
        ]

    def prune_snapshots(self, before: datetime) -> int:
        cutoff = _ts(before)
        with self._conn() as conn:
            ids = [r["id"] for r in conn.execute(
                "SELECT id FROM snapshots WHERE taken_at < ?", (cutoff,))]
            for sid in ids:
                conn.execute("DELETE FROM snapshot_departures WHERE snapshot_id = ?", (sid,))
                conn.execute("DELETE FROM snapshots WHERE id = ?", (sid,))
        return len(ids)

    # digest runs
    def record_digest_run(self, run_at: datetime, snapshot_id: int | None, status: str,
                          entry_ids: list[str], error: str | None = None) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO digest_runs(run_at, snapshot_id, status, entry_count, error) "
                "VALUES (?,?,?,?,?)",
                (_ts(run_at), snapshot_id, status, len(entry_ids), error),
            )
            rid = cur.lastrowid
            conn.executemany(
                "INSERT OR IGNORE INTO digest_entries VALUES (?,?)",
                [(rid, e) for e in entry_ids],
            )
        return rid

    def has_digest_runs(self) -> bool:
        with self._conn() as conn:
            return conn.execute("SELECT 1 FROM digest_runs LIMIT 1").fetchone() is not None

    def baseline_snapshot_id(self) -> int | None:
        with self._conn() as conn:
            row = conn.execute(
                f"SELECT snapshot_id FROM digest_runs WHERE status IN ({_STATUS_MARKS}) "
                "AND snapshot_id IS NOT NULL ORDER BY id DESC LIMIT 1",
                _BASELINE_STATUSES,
            ).fetchone()
        return row["snapshot_id"] if row else None

    def last_sent_entry_ids(self) -> set[str]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id FROM digest_runs WHERE status = 'sent' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return set()
            return {r["departure_id"] for r in conn.execute(
                "SELECT departure_id FROM digest_entries WHERE digest_run_id = ?",
                (row["id"],))}

    def recent_digest_runs(self, limit: int = 10) -> list[DigestRun]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM digest_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [_run(r) for r in rows]

    def digest_run_on(self, day: date, tz: str) -> DigestRun | None:
        zone = ZoneInfo(tz)
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM digest_runs WHERE status IN ({_STATUS_MARKS}) "
                "ORDER BY id DESC",
                _BASELINE_STATUSES,
            ).fetchall()
        for r in rows:
            run = _run(r)
            if run.run_at.astimezone(zone).date() == day:
                return run
        return None

    # hidden
    def hide(self, departure_id: str, code: str, at: datetime) -> None:
        with self._conn() as conn:
            conn.execute("INSERT OR REPLACE INTO hidden VALUES (?,?,?)",
                         (departure_id, code, _ts(at)))

    def unhide(self, departure_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM hidden WHERE departure_id = ?", (departure_id,))

    def hidden_ids(self) -> set[str]:
        with self._conn() as conn:
            return {r["departure_id"] for r in conn.execute("SELECT departure_id FROM hidden")}

    # settings
    def _get_setting(self, key: str):
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value_json FROM settings WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value_json"]) if row else None

    def _set_setting(self, key: str, value) -> None:
        with self._conn() as conn:
            conn.execute("INSERT OR REPLACE INTO settings VALUES (?,?)",
                         (key, json.dumps(value)))

    def digest_filter(self) -> Filter:
        raw = self._get_setting("digest_filter")
        return Filter.from_dict(raw) if raw is not None else Filter()

    def set_digest_filter(self, f: Filter) -> None:
        self._set_setting("digest_filter", f.to_dict())

    def send_time(self) -> str:
        return self._get_setting("send_time") or "07:00"

    def set_send_time(self, hhmm: str) -> None:
        self._set_setting("send_time", hhmm)
