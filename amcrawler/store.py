"""SQLite store: catalogue, snapshots, digest runs, hidden, pins, Alerts and settings."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
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
    max_participants INTEGER, notes TEXT, catalogued INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY(snapshot_id, departure_id));
CREATE TABLE IF NOT EXISTS digest_runs(
    id INTEGER PRIMARY KEY, run_at TEXT, snapshot_id INTEGER, status TEXT,
    entry_count INTEGER, error TEXT);
CREATE TABLE IF NOT EXISTS digest_entries(
    digest_run_id INTEGER, departure_id TEXT,
    PRIMARY KEY(digest_run_id, departure_id));
CREATE TABLE IF NOT EXISTS hidden(
    departure_id TEXT PRIMARY KEY, code TEXT, hidden_at TEXT);
CREATE TABLE IF NOT EXISTS pins(
    departure_id TEXT PRIMARY KEY, start_date TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS alerts(
    departure_id TEXT PRIMARY KEY, threshold INTEGER, name TEXT, itinerary_code TEXT,
    start_date TEXT, end_date TEXT, participants INTEGER, max_participants INTEGER,
    created_at TEXT);
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


@dataclass(frozen=True)
class Alert:
    """An Alert with the Departure data last seen, so a "gone" mail can quote it."""
    departure_id: str
    threshold: int
    name: str
    itinerary_code: str
    start_date: date
    end_date: date
    participants: int
    max_participants: int


def _ts(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _json_tuple(raw: str | None) -> tuple[str, ...]:
    return tuple(json.loads(raw)) if raw else ()


def _snapshot(row: sqlite3.Row | None) -> SnapshotInfo | None:
    if row is None:
        return None
    return SnapshotInfo(row["id"], datetime.fromisoformat(row["taken_at"]), bool(row["ok"]),
                        row["error"], row["departure_count"])


def _run(row: sqlite3.Row) -> DigestRun:
    return DigestRun(row["id"], datetime.fromisoformat(row["run_at"]), row["snapshot_id"],
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
            columns = {r["name"] for r in conn.execute(
                "PRAGMA table_info(snapshot_departures)")}
            if "catalogued" not in columns:
                conn.execute("ALTER TABLE snapshot_departures "
                             "ADD COLUMN catalogued INTEGER NOT NULL DEFAULT 1")
                conn.execute(
                    "UPDATE snapshot_departures SET catalogued = 0 WHERE itinerary_id "
                    "NOT IN (SELECT id FROM itineraries)")

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
        return datetime.fromisoformat(row["t"]) if row["t"] else None

    def catalogue_attempted_at(self) -> datetime | None:
        """When a catalogue refresh was last tried (successful or not)."""
        raw = self._get_setting("catalogue_attempted_at")
        return datetime.fromisoformat(raw) if raw else None

    def note_catalogue_attempt(self, at: datetime) -> None:
        self._set_setting("catalogue_attempted_at", _ts(at))

    def itineraries(self) -> dict[str, Itinerary]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM itineraries").fetchall()
        return {
            r["id"]: Itinerary(r["id"], r["code"], r["name"], r["formula"],
                               _json_tuple(r["areas_json"]),
                               _json_tuple(r["countries_json"]),
                               bool(r["coordinator_free"]))
            for r in rows
        }

    # snapshots
    def record_snapshot(self, taken_at: datetime, departures: list[Departure] | None,
                        error: str | None = None, fetched_count: int = 0) -> int:
        """Record a snapshot; a rejected one (departures None) keeps `fetched_count`."""
        ok = departures is not None
        deps = departures or []
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO snapshots(taken_at, ok, error, departure_count) VALUES (?,?,?,?)",
                (_ts(taken_at), int(ok), error, len(deps) if ok else fetched_count),
            )
            sid = cur.lastrowid
            conn.executemany(
                "INSERT INTO snapshot_departures VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(sid, d.id, d.itinerary_id, d.itinerary_code, d.code, d.name, d.formula,
                  d.start_date.isoformat(), d.end_date.isoformat(), d.duration,
                  d.participants, d.max_participants, d.notes, int(d.catalogued))
                 for d in deps],
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

    def latest_nonempty_ok_snapshot(self) -> SnapshotInfo | None:
        return self._newest_snapshot("WHERE ok = 1 AND departure_count > 0")

    def rejected_since_last_ok(self, errors: tuple[str, ...]) -> list[SnapshotInfo]:
        """Snapshots rejected with one of `errors` since the last OK snapshot, newest first."""
        marks = ",".join("?" * len(errors))
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM snapshots WHERE ok = 0 AND error IN ({marks}) "
                "AND id > COALESCE((SELECT MAX(id) FROM snapshots WHERE ok = 1), 0) "
                "ORDER BY id DESC", errors).fetchall()
        return [_snapshot(r) for r in rows]

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
                areas=_json_tuple(r["areas_json"]),
                countries=_json_tuple(r["countries_json"]),
                catalogued=bool(r["catalogued"]),
            )
            for r in rows
        ]

    def latest_ok_departures(self) -> list[Departure]:
        snap = self.latest_ok_snapshot()
        return self.snapshot_departures(snap.id) if snap else []

    def formulas(self) -> list[str]:
        with self._conn() as conn:
            return [r["formula"] for r in conn.execute(
                "SELECT DISTINCT formula FROM itineraries "
                "WHERE formula IS NOT NULL AND formula != '' ORDER BY formula")]

    def prune_snapshots(self, before: datetime) -> int:
        cutoff = _ts(before)
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM snapshot_departures WHERE snapshot_id IN "
                "(SELECT id FROM snapshots WHERE taken_at < ?)", (cutoff,))
            return conn.execute(
                "DELETE FROM snapshots WHERE taken_at < ?", (cutoff,)).rowcount

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

    def _run_on(self, day: date, tz: str, statuses: tuple[str, ...] | None) -> DigestRun | None:
        zone = ZoneInfo(tz)
        start = datetime.combine(day, time.min, zone)
        end = datetime.combine(day + timedelta(days=1), time.min, zone)
        sql = "SELECT * FROM digest_runs WHERE run_at >= ? AND run_at < ?"
        args: list = [_ts(start), _ts(end)]
        if statuses is not None:
            sql += f" AND status IN ({','.join('?' * len(statuses))})"
            args.extend(statuses)
        with self._conn() as conn:
            row = conn.execute(sql + " ORDER BY id DESC LIMIT 1", args).fetchone()
        return _run(row) if row else None

    def digest_run_on(self, day: date, tz: str) -> DigestRun | None:
        """The latest baseline/sent/empty run made on `day` in time zone `tz`."""
        return self._run_on(day, tz, _BASELINE_STATUSES)

    def any_digest_run_on(self, day: date, tz: str) -> bool:
        """Whether a Digest run of any status (also failed/stale) was made on `day`."""
        return self._run_on(day, tz, None) is not None

    # hidden
    def hide(self, departure_id: str, code: str, at: datetime) -> None:
        """Hide a Departure; its pin and Alert go too and unhide does not bring them back."""
        with self._conn() as conn:
            conn.execute("INSERT OR REPLACE INTO hidden VALUES (?,?,?)",
                         (departure_id, code, _ts(at)))
            conn.execute("DELETE FROM pins WHERE departure_id = ?", (departure_id,))
            conn.execute("DELETE FROM alerts WHERE departure_id = ?", (departure_id,))

    def unhide(self, departure_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM hidden WHERE departure_id = ?", (departure_id,))

    def hidden_ids(self) -> set[str]:
        with self._conn() as conn:
            return {r["departure_id"] for r in conn.execute("SELECT departure_id FROM hidden")}

    # pins
    def pin(self, departure_id: str, start_date: date, at: datetime) -> None:
        with self._conn() as conn:
            conn.execute("INSERT OR REPLACE INTO pins VALUES (?,?,?)",
                         (departure_id, start_date.isoformat(), _ts(at)))

    def unpin(self, departure_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM pins WHERE departure_id = ?", (departure_id,))

    def pinned_ids(self) -> set[str]:
        with self._conn() as conn:
            return {r["departure_id"] for r in conn.execute("SELECT departure_id FROM pins")}

    def delete_pins_before(self, day: date) -> int:
        """Drop the pins of Departures that left before `day`."""
        with self._conn() as conn:
            return conn.execute("DELETE FROM pins WHERE start_date < ?",
                                (day.isoformat(),)).rowcount

    # Alerts
    def set_alert(self, d: Departure, threshold: int, at: datetime) -> None:
        """Set the Alert on `d`, replacing any earlier threshold."""
        with self._conn() as conn:
            conn.execute("INSERT OR REPLACE INTO alerts VALUES (?,?,?,?,?,?,?,?,?)",
                         (d.id, threshold, d.name, d.itinerary_code, d.start_date.isoformat(),
                          d.end_date.isoformat(), d.participants, d.max_participants,
                          _ts(at)))

    def refresh_alert(self, d: Departure) -> None:
        """Update the last-seen data of `d`'s Alert; the threshold stays."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE alerts SET name = ?, itinerary_code = ?, start_date = ?, end_date = ?, "
                "participants = ?, max_participants = ? WHERE departure_id = ?",
                (d.name, d.itinerary_code, d.start_date.isoformat(), d.end_date.isoformat(),
                 d.participants, d.max_participants, d.id))

    def delete_alert(self, departure_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM alerts WHERE departure_id = ?", (departure_id,))

    def alerts(self) -> list[Alert]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM alerts ORDER BY start_date, departure_id").fetchall()
        return [Alert(r["departure_id"], r["threshold"], r["name"], r["itinerary_code"],
                      date.fromisoformat(r["start_date"]), date.fromisoformat(r["end_date"]),
                      r["participants"], r["max_participants"])
                for r in rows]

    def alert_thresholds(self) -> dict[str, int]:
        return {a.departure_id: a.threshold for a in self.alerts()}

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

    def digest_enabled(self) -> bool:
        raw = self._get_setting("digest_enabled")
        return True if raw is None else bool(raw)

    def set_digest_enabled(self, enabled: bool) -> None:
        self._set_setting("digest_enabled", bool(enabled))

    def digest_weekdays(self) -> list[int]:
        """Weekdays (Monday = 0) the Digest is sent on; every day until saved."""
        raw = self._get_setting("digest_weekdays")
        return list(range(7)) if raw is None else sorted(set(raw))

    def set_digest_weekdays(self, weekdays: list[int]) -> None:
        self._set_setting("digest_weekdays", sorted(set(weekdays)))

    def digest_quiet_mail(self) -> bool:
        return bool(self._get_setting("digest_quiet_mail"))

    def set_digest_quiet_mail(self, enabled: bool) -> None:
        self._set_setting("digest_quiet_mail", bool(enabled))

    def is_send_day(self, day: date) -> bool:
        """Whether a Digest goes out on this (local) day: enabled and weekday chosen."""
        return self.digest_enabled() and day.weekday() in self.digest_weekdays()
