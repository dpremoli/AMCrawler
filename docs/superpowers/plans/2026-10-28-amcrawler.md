# AMCrawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A single-container web app that crawls Avventure nel Mondo's Uncovered Departures, emails a daily Digest of New Entries, and offers a phone-first list + world-map GUI behind Cloudflare Access.

**Architecture:** One Python process (FastAPI + Jinja2 + APScheduler + SQLite). Pure modules (`models`, `geo`, `filters`, `diff`) hold the logic; `source`/`store`/`mailer`/`auth` wrap I/O; `crawl`/`digest` orchestrate; `web`/`scheduler` wire it up. Frontend is server-rendered HTML + vanilla JS + vendored d3/topojson.

**Tech Stack:** Python ≥ 3.12 (container 3.12, dev machine 3.14), FastAPI, uvicorn, Jinja2, httpx, APScheduler 3.x, PyJWT[crypto], pycountry, python-multipart; tests: pytest, respx. Frontend: d3 7.9.0, topojson-client 3.1.0, world-atlas 2.0.2 (`countries-50m.json`).

**Spec:** `docs/superpowers/specs/2026-10-28-amcrawler-design.md` — read it alongside this plan. Glossary: `CONTEXT.md`. ADRs: `docs/adr/`.

## Global Constraints

- Never write the real hostname, any real email, or credentials into any tracked file. Placeholders only: `viaggi.example.com`, `owner@example.com`, `maintainer@example.com`.
- Never persist or log Coordinator personal data (`assignment.tourleader`); fixtures must have it removed.
- Code must run on Python 3.12 and 3.14 (no 3.13+-only syntax or stdlib APIs).
- All user-facing text (GUI, email) is Italian. Code identifiers and comments are English and use the glossary terms (Departure, Itinerary, Uncovered, New Entry, Digest Filter, Search, Hidden, Owner).
- Timezone for "today", schedules and display: `Europe/London` (from `Config.tz`). Store timestamps as ISO-8601 UTC strings.
- uvicorn runs with exactly one worker.
- Line endings: LF for every file (`.gitattributes` `* text=auto eol=lf`).
- Dev commands (Git Bash, repo root): venv at `.venv`, run tests with `.venv/Scripts/python -m pytest`.
- No external network calls in the default test run; the live test is opt-in (`-m live`).

## Review Focus

1. **Departure whose Itinerary is missing from the catalogue** (new Itinerary added after the daily catalogue fetch) → still listed, still emailable, no Areas/Countries, map ignores it, link still works via `itinerary_code`. Test: `test_snapshot_departure_without_itinerary` (Task 4).
2. **Notes with HTML/special characters or very long text** → escaped in GUI and email, truncated to 140 chars + "…" in the email. Test: `test_digest_escapes_and_truncates_notes` (Task 6).
3. **Send time edited to an invalid value** (`7`, `25:00`, empty) → rejected with an Italian error message, schedule unchanged. Test: `test_settings_rejects_bad_send_time` (Task 10).
4. **Crawl and Digest overlapping / app restarted mid-day** → no duplicate Digest for the same London date: a second `run_digest` on a date that already has a `sent`/`empty`/`baseline` run does nothing. Test: `test_digest_runs_once_per_day` (Task 7).
5. **Search query string with junk values** (`?dfrom=abc&pmin=-3&formula=`) → ignored fields, page still renders 200. Test: `test_index_ignores_bad_query_values` (Task 9).

---

### Task 1: Scaffold, config, models, geo

**Files:**
- Create: `.gitignore`, `.gitattributes`, `.env.example`, `requirements.in`, `requirements.txt`, `requirements-dev.txt`, `pytest.ini`
- Create: `amcrawler/__init__.py`, `amcrawler/config.py`, `amcrawler/models.py`, `amcrawler/geo.py`
- Test: `tests/__init__.py`, `tests/test_models.py`, `tests/test_geo.py`

**Interfaces:**
- Produces:
  - `config.Config` (frozen dataclass): `public_url: str, cf_team_domain: str, cf_aud: str, smtp_host: str, smtp_port: int, smtp_user: str, smtp_password: str, digest_from: str, digest_to: str, tz: str, db_path: str, dev_no_auth: bool`
  - `config.load_config(env: Mapping[str, str] = os.environ) -> Config` — env names per spec §10; defaults `smtp_host="smtp.gmail.com"`, `smtp_port=587`, `tz="Europe/London"`, `db_path="/data/amcrawler.db"`, `dev_no_auth = env.get("AMC_DEV_NO_AUTH") == "1"`, others `""`. Never raises.
  - `models.Itinerary(id: str, code: str, name: str, formula: str, areas: tuple[str, ...], countries: tuple[str, ...], coordinator_free: bool)` — frozen; `countries` already folded.
  - `models.Departure(id: str, code: str, itinerary_id: str, itinerary_code: str, name: str, formula: str, start_date: date, end_date: date, duration: int, participants: int, max_participants: int, notes: str, areas: tuple[str, ...] = (), countries: tuple[str, ...] = ())` — frozen.
  - `models.Filter(areas=(), countries=(), formulas=(), days_from: int|None=None, days_to: int|None=None, duration_min: int|None=None, duration_max: int|None=None, min_participants: int = 1)` — frozen, tuples of str; methods `to_dict() -> dict`, `classmethod from_dict(d: Mapping) -> Filter`, `to_query() -> list[tuple[str, str]]`, `classmethod from_query(pairs: Iterable[tuple[str, str]]) -> Filter`, `is_empty() -> bool` (true when equal to `Filter()`).
  - Query keys: `area`, `country`, `formula` (repeatable), `dfrom`, `dto`, `dmin`, `dmax`, `pmin`. `from_query` silently drops empty strings, non-integers and negative ints; `pmin` < 1 → 1.
  - `geo.PSEUDO_CODES: dict[str, tuple[str, ...]]` — spec §2.3 table exactly.
  - `geo.fold_countries(codes: Iterable[str]) -> tuple[str, ...]` — order-preserving, deduplicated.
  - `geo.iso_numeric(alpha2: str) -> str | None` — 3-digit zero-padded string matching world-atlas ids (e.g. `"380"` for IT, `"004"` for AF); None if unknown.
  - `geo.country_name_it(alpha2: str) -> str` — Italian name via `gettext.translation("iso3166-1", pycountry.LOCALES_DIR, languages=["it"])`, fallback English name, fallback the code.

- [ ] **Step 1: Repo hygiene files**

`.gitattributes`: `* text=auto eol=lf`. `.gitignore`: `.venv/`, `__pycache__/`, `.pytest_cache/`, `.env`, `data/`, `*.db`, `*.db-*`. `.env.example`: every var from spec §10 with placeholder values. `pytest.ini`: `testpaths = tests`, `markers = live: hits the real website`, `addopts = -m "not live"`.

- [ ] **Step 2: Dependencies**

`requirements.in`: `fastapi`, `uvicorn`, `jinja2`, `httpx`, `apscheduler<4`, `pyjwt[crypto]`, `pycountry`, `python-multipart`. Create `.venv` (`python -m venv .venv`), `pip install -r requirements.in pytest respx`, then write the resolved exact versions of the runtime packages (and their deps) to `requirements.txt` via `pip freeze` minus pytest/respx, and `requirements-dev.txt` = `-r requirements.txt` + pinned `pytest`, `respx`.

- [ ] **Step 3: Write failing tests**

```python
# tests/test_models.py
def test_filter_query_roundtrip():
    f = Filter(areas=("Sud Est Asiatico",), formulas=("Discovery", "Adventure"), days_from=14, days_to=120, min_participants=4)
    assert Filter.from_query(f.to_query()) == f

def test_filter_from_query_drops_junk():
    f = Filter.from_query([("dfrom", "abc"), ("pmin", "-3"), ("formula", ""), ("dmax", "20")])
    assert f == Filter(duration_max=20)

def test_filter_dict_roundtrip_and_empty():
    f = Filter(countries=("JP",), duration_min=8)
    assert Filter.from_dict(f.to_dict()) == f
    assert Filter().is_empty() and not f.is_empty()

# tests/test_geo.py
def test_fold_pseudo_codes():
    assert fold_countries(["CT", "IT", "IA"]) == ("IT",)
    assert fold_countries(["PX"]) == ("AR", "CL")
    assert fold_countries(["ZI", "TB", "AK", "LE", "MI"]) == ("GB", "CN", "US", "ES", "PT")

def test_iso_numeric():
    assert iso_numeric("IT") == "380"
    assert iso_numeric("AF") == "004"
    assert iso_numeric("ZZ") is None

def test_country_name_it():
    assert country_name_it("JP") == "Giappone"
    assert country_name_it("ZZ") == "ZZ"

def test_load_config_defaults():
    c = load_config({})
    assert c.smtp_port == 587 and c.tz == "Europe/London" and c.dev_no_auth is False
    assert load_config({"AMC_DEV_NO_AUTH": "1"}).dev_no_auth is True
```

- [ ] **Step 4: Run** `.venv/Scripts/python -m pytest tests/test_models.py tests/test_geo.py -v` → FAIL (import errors).
- [ ] **Step 5: Implement** `config.py`, `models.py`, `geo.py` per Interfaces.
- [ ] **Step 6: Run** same command → all PASS.
- [ ] **Step 7: Commit** `git add -A && git commit -m "feat: scaffold, config, domain models and country folding"`

---

### Task 2: Source (API + catalogue parsing) with fixtures

**Files:**
- Create: `amcrawler/source.py`, `scripts/capture_fixtures.py`
- Create: `tests/fixtures/tours_page1.json`, `tests/fixtures/tours_page2.json`, `tests/fixtures/bases_sample.js`
- Test: `tests/test_source.py`, `tests/test_live.py`

**Interfaces:**
- Consumes: `models.Departure`, `models.Itinerary`, `geo.fold_countries`
- Produces:
  - `source.BASE_URL = "https://www.viaggiavventurenelmondo.it"`
  - `source.SourceError(Exception)`
  - `source.make_client() -> httpx.Client` — base_url BASE_URL, header `User-Agent: AMCrawler/1.0 (personal use)`, timeout 20 s.
  - `source.parse_tours_page(payload: dict) -> tuple[list[Departure], int]` — returns Departures (areas/countries empty) and `maxResultsPerPage`. Drops results with non-null `assignment` or `base.tourleaderMode != "regular"`. Raises `SourceError` if `success` is not true or required keys are missing/wrong type.
  - `source.fetch_uncovered(client: httpx.Client, pause: float = 1.0) -> list[Departure]` — query params `fromTotalPax=1, isFull=0, hasAssignment=0, spg=n`; stop when a page has fewer than `maxResultsPerPage`; max 50 pages; `time.sleep(pause)` between pages; any `httpx.HTTPError` / non-200 / JSON error → `SourceError`.
  - `source.parse_catalogue(js_text: str) -> list[Itinerary]` — find `bases:` then `json.JSONDecoder().raw_decode` from the next `[`; `countries` = `fold_countries(c["isoCode2"] ...)`; `areas` = names of `geographicAreas` in ascending `ordinal`; `coordinator_free = tourleaderMode != "regular"`. Raises `SourceError` on failure.
  - `source.fetch_catalogue(client) -> list[Itinerary]` — GET `/data/bases.js`.
  - `source.itinerary_url(itinerary_code: str) -> str` → `f"{BASE_URL}/viaggi/{itinerary_code}"`.

- [ ] **Step 1: Capture fixtures**

Write `scripts/capture_fixtures.py` that fetches pages 1–2 of the tours endpoint and `/data/bases.js`, **deletes `assignment.tourleader` everywhere** (replace `assignment` with `{"id": "x", "status": "active"}` where non-null), and saves: both tour pages (keep up to 30 results each; trim page 2 to 5 results), and `bases_sample.js` = the original `"use strict";var BASES={bases:[...],search:...};` wrapper containing only the Itineraries referenced by those Departures plus one with `tourleaderMode: "no-tourleader"` and one using country `PX`. Run it once; commit outputs. Add one hand-edited result to `tours_page1.json` with a non-null `assignment` (must be dropped by the parser). Then `grep -ri "phone01\|tourleader\"" tests/fixtures` must return nothing.

- [ ] **Step 2: Write failing tests**

```python
def test_parse_tours_page_fields():
    deps, size = parse_tours_page(load_json("tours_page1.json"))
    assert size == 30
    d = deps[0]
    assert d.code.startswith("2026-") and isinstance(d.start_date, date)
    assert d.participants >= 1 and d.max_participants >= d.participants
    assert d.itinerary_code and d.name and d.formula

def test_parse_tours_page_drops_assigned():
    payload = load_json("tours_page1.json")
    assigned = [r["code"] for r in payload["data"]["results"] if r["assignment"]]
    deps, _ = parse_tours_page(payload)
    assert assigned and not set(assigned) & {d.code for d in deps}

@pytest.mark.parametrize("payload", [{"success": False}, {"success": True, "data": {}}, {"success": True, "data": {"results": [{"code": 1}], "maxResultsPerPage": 30}}])
def test_parse_tours_page_rejects_bad_shape(payload):
    with pytest.raises(SourceError):
        parse_tours_page(payload)

@respx.mock
def test_fetch_uncovered_paginates_and_stops():
    route = respx.get(f"{BASE_URL}/api/operative/tours").mock(side_effect=[
        httpx.Response(200, json=load_json("tours_page1.json")),
        httpx.Response(200, json=load_json("tours_page2.json"))])
    deps = fetch_uncovered(make_client(), pause=0)
    assert route.call_count == 2
    assert route.calls[0].request.url.params["hasAssignment"] == "0"
    assert route.calls[1].request.url.params["spg"] == "2"
    assert len(deps) == len({d.id for d in deps})

@respx.mock
def test_fetch_uncovered_http_error():
    respx.get(f"{BASE_URL}/api/operative/tours").mock(return_value=httpx.Response(503))
    with pytest.raises(SourceError):
        fetch_uncovered(make_client(), pause=0)

def test_parse_catalogue():
    its = {i.code: i for i in parse_catalogue(read_text("bases_sample.js"))}
    assert any(i.coordinator_free for i in its.values())
    pat = next(i for i in its.values() if "AR" in i.countries)
    assert "CL" in pat.countries and "PX" not in pat.countries
    assert all(i.areas for i in its.values())

def test_parse_catalogue_garbage():
    with pytest.raises(SourceError):
        parse_catalogue("var X = 1;")

def test_itinerary_url():
    assert itinerary_url("1410") == "https://www.viaggiavventurenelmondo.it/viaggi/1410"
```

`tests/test_live.py` (marked `live`): `fetch_uncovered(make_client())` returns ≥ 1 Departure and `fetch_catalogue` ≥ 500 Itineraries.

- [ ] **Step 3: Run** `.venv/Scripts/python -m pytest tests/test_source.py -v` → FAIL.
- [ ] **Step 4: Implement** `source.py`.
- [ ] **Step 5: Run** → PASS. Then `.venv/Scripts/python -m pytest -m live -v` → PASS (needs internet).
- [ ] **Step 6: Commit** `feat: fetch and parse uncovered departures and itinerary catalogue`

---

### Task 3: Filters and New Entry diff

**Files:**
- Create: `amcrawler/filters.py`, `amcrawler/diff.py`
- Test: `tests/test_filters.py`, `tests/test_diff.py`

**Interfaces:**
- Consumes: `models.Departure`, `models.Filter`
- Produces:
  - `filters.matches(d: Departure, f: Filter, today: date) -> bool` — spec §5: OR within list, AND across; window on `(d.start_date - today).days` inclusive; duration inclusive; `d.participants >= f.min_participants`; Departure with empty `areas` fails non-empty `f.areas` (same for countries).
  - `filters.apply(deps: Iterable[Departure], f: Filter, today: date) -> list[Departure]` — keeps input order.
  - `diff.new_entries(current: list[Departure], baseline: list[Departure], f: Filter, hidden: set[str], today: date) -> list[Departure]` — spec §6 rule; sorted by `(start_date, name)`; `hidden` holds Departure ids.

- [ ] **Step 1: Write failing tests.** Use a helper `dep(**overrides) -> Departure` with sane defaults (start 30 days after `TODAY = date(2026, 10, 28)`, 5 participants, max 15, duration 12, formula "Adventure", areas ("Sud Est Asiatico",), countries ("TH",)).

```python
# test_filters.py
def test_empty_filter_matches_everything(): assert matches(dep(), Filter(), TODAY)
def test_or_within_and_across():
    f = Filter(formulas=("Discovery", "Adventure"), countries=("JP",))
    assert not matches(dep(), f, TODAY)                     # formula ok, country not
    assert matches(dep(countries=("JP", "KR")), f, TODAY)
def test_window_inclusive():
    f = Filter(days_from=30, days_to=30)
    assert matches(dep(), f, TODAY)
    assert not matches(dep(start_date=TODAY + timedelta(days=31)), f, TODAY)
def test_duration_and_min_participants():
    assert not matches(dep(duration=12), Filter(duration_max=11), TODAY)
    assert not matches(dep(participants=3), Filter(min_participants=4), TODAY)
def test_no_catalogue_fails_area_constraint():
    assert not matches(dep(areas=(), countries=()), Filter(areas=("Sud Est Asiatico",)), TODAY)
    assert matches(dep(areas=(), countries=()), Filter(), TODAY)

# test_diff.py — the four agreed scenarios + extras
def test_widened_filter_is_not_new():
    africa = dep(id="a", areas=("Africa Centrale",))
    assert new_entries([africa], [africa], Filter(areas=("Sud Est Asiatico", "Africa Centrale")), set(), TODAY) == []
def test_crossing_min_participants_is_new():
    f = Filter(min_participants=4)
    assert [d.id for d in new_entries([dep(id="x", participants=4)], [dep(id="x", participants=3)], f, set(), TODAY)] == ["x"]
def test_reentry_after_coordinator_dropout_is_new():
    assert [d.id for d in new_entries([dep(id="x")], [], Filter(), set(), TODAY)] == ["x"]
def test_present_at_both_digests_is_not_new():
    assert new_entries([dep(id="x", participants=1)], [dep(id="x", participants=1)], Filter(), set(), TODAY) == []
def test_hidden_never_new():
    assert new_entries([dep(id="x")], [], Filter(), {"x"}, TODAY) == []
def test_sorted_by_start_then_name():
    a = dep(id="a", name="B", start_date=TODAY + timedelta(days=5))
    b = dep(id="b", name="A", start_date=TODAY + timedelta(days=5))
    c = dep(id="c", start_date=TODAY + timedelta(days=1))
    assert [d.id for d in new_entries([a, b, c], [], Filter(), set(), TODAY)] == ["c", "b", "a"]
```

- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_filters.py tests/test_diff.py -v` → FAIL.
- [ ] **Step 3: Implement** both modules.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: filter matching and New Entry diff`

---

### Task 4: Store (SQLite)

**Files:**
- Create: `amcrawler/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: models
- Produces:
  - `store.SnapshotInfo(id: int, taken_at: datetime, ok: bool, error: str | None, departure_count: int)` (frozen dataclass)
  - `store.DigestRun(id: int, run_at: datetime, snapshot_id: int | None, status: str, entry_count: int, error: str | None)`
  - `class Store(path: str)` — opens a new `sqlite3` connection per method (thread-safe for APScheduler + web), WAL mode; `init()` creates the spec §4 schema **plus** column `snapshot_departures.itinerary_code TEXT`; idempotent.
  - Catalogue: `save_catalogue(items: list[Itinerary], fetched_at: datetime) -> None` (replace all rows), `catalogue_fetched_at() -> datetime | None`, `itineraries() -> dict[str, Itinerary]` (key = Itinerary id).
  - Snapshots: `record_snapshot(taken_at: datetime, departures: list[Departure] | None, error: str | None = None) -> int` (ok = departures is not None), `latest_snapshot() -> SnapshotInfo | None`, `latest_ok_snapshot() -> SnapshotInfo | None`, `snapshot_departures(snapshot_id: int) -> list[Departure]` (joined to catalogue: `areas`/`countries` from Itinerary; missing Itinerary → empty tuples), `prune_snapshots(before: datetime) -> int`.
  - Digest runs: `record_digest_run(run_at: datetime, snapshot_id: int | None, status: str, entry_ids: list[str], error: str | None = None) -> int`, `has_digest_runs() -> bool`, `baseline_snapshot_id() -> int | None` (latest run with status in `baseline|sent|empty`), `last_sent_entry_ids() -> set[str]`, `recent_digest_runs(limit: int = 10) -> list[DigestRun]`, `digest_run_on(day: date, tz: str) -> DigestRun | None` (a run whose `run_at` in `tz` falls on `day`, status in `baseline|sent|empty`).
  - Hidden: `hide(departure_id: str, code: str, at: datetime)`, `unhide(departure_id: str)`, `hidden_ids() -> set[str]`.
  - Settings: `digest_filter() -> Filter` (default `Filter()`), `set_digest_filter(f: Filter)`, `send_time() -> str` (default `"07:00"`), `set_send_time(hhmm: str)`.

- [ ] **Step 1: Write failing tests** (use `tmp_path`):

```python
def test_snapshot_roundtrip_joins_catalogue(store): ...
    # save_catalogue([itin(id="i1", areas=("Nord Africa",), countries=("EG",))]); record_snapshot with dep(itinerary_id="i1")
    # → snapshot_departures(sid)[0].countries == ("EG",) and dates are date objects
def test_snapshot_departure_without_itinerary(store):
    sid = store.record_snapshot(NOW, [dep(itinerary_id="missing", itinerary_code="9999")])
    d = store.snapshot_departures(sid)[0]
    assert d.areas == () and d.countries == () and d.itinerary_code == "9999"
def test_failed_snapshot_not_latest_ok(store):
    ok = store.record_snapshot(NOW, [dep()]); store.record_snapshot(NOW + H, None, "boom")
    assert store.latest_ok_snapshot().id == ok and store.latest_snapshot().error == "boom"
def test_baseline_ignores_failed_and_stale(store):
    s1 = store.record_snapshot(NOW, []); store.record_digest_run(NOW, s1, "empty", [])
    s2 = store.record_snapshot(NOW + DAY, []); store.record_digest_run(NOW + DAY, s2, "failed", [], "smtp")
    store.record_digest_run(NOW + 2 * DAY, None, "stale", [])
    assert store.baseline_snapshot_id() == s1
def test_last_sent_entry_ids(store): ...   # two 'sent' runs → ids of the latest only
def test_hidden_roundtrip(store): ...       # hide, hidden_ids, unhide
def test_settings_defaults_and_roundtrip(store):
    assert store.digest_filter() == Filter() and store.send_time() == "07:00"
    store.set_digest_filter(Filter(formulas=("Discovery",))); store.set_send_time("06:30")
    assert store.digest_filter().formulas == ("Discovery",) and store.send_time() == "06:30"
def test_prune_snapshots(store): ...         # old snapshot + its departures deleted, new kept
def test_init_is_idempotent(tmp_path): ...
```

- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_store.py -v` → FAIL.
- [ ] **Step 3: Implement** `store.py`. Dates in `snapshot_departures` as `YYYY-MM-DD`; timestamps as ISO UTC; JSON for tuples.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: SQLite store for catalogue, snapshots, digest runs, hidden and settings`

---

### Task 5: Crawl

**Files:**
- Create: `amcrawler/crawl.py`
- Test: `tests/test_crawl.py`

**Interfaces:**
- Consumes: `source.fetch_uncovered`, `source.fetch_catalogue`, `source.SourceError`, `Store`
- Produces: `crawl.run_crawl(store: Store, client: httpx.Client, now: datetime, pause: float = 1.0) -> SnapshotInfo`
  1. If `catalogue_fetched_at()` is None or older than 24 h: try `fetch_catalogue` → `save_catalogue`; on `SourceError` log a warning and continue.
  2. `fetch_uncovered`; on `SourceError` record failed snapshot with `str(error)`.
  3. Sanity guard: result empty and previous ok snapshot had `departure_count >= 10` → record failed snapshot `"suspicious empty result"`.
  4. Otherwise record ok snapshot. Return `store.latest_snapshot()`.
  Log with `logging.getLogger("amcrawler")`; never log Coordinator data.

- [ ] **Step 1: Write failing tests** with respx serving the fixtures:
  `test_crawl_records_ok_snapshot_and_catalogue`, `test_crawl_skips_catalogue_when_fresh` (bases route not called within 24 h), `test_crawl_catalogue_failure_is_non_fatal` (bases 500 → snapshot still ok), `test_crawl_api_failure_records_error` (tours 503 → `ok is False`, error non-empty), `test_crawl_sanity_guard` (previous ok had 12, now 0 results → `ok is False`, `error == "suspicious empty result"`; with previous 3 → ok).
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_crawl.py -v` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: crawl job with catalogue refresh and sanity guard`

---

### Task 6: Mailer and email templates

**Files:**
- Create: `amcrawler/mailer.py`, `amcrawler/templates/email/digest.html`, `amcrawler/templates/email/digest.txt`, `amcrawler/templates/email/stale.txt`
- Test: `tests/test_mailer.py`

**Interfaces:**
- Consumes: models, `geo.country_name_it`, `source.itinerary_url`
- Produces:
  - `mailer.MONTHS_IT = ("gen","feb","mar","apr","mag","giu","lug","ago","set","ott","nov","dic")`
  - `mailer.short_date(d: date) -> str` → `"8 nov"`; `mailer.date_range(start: date, end: date) -> str` → `"8 nov – 23 nov"` (en dash with spaces).
  - `mailer.subject_for(count: int, day: date) -> str` — spec §9 exact copy: `f"{n} nuove partenze senza coordinatore · {short_date(day)}"`, singular `"1 nuova partenza senza coordinatore · …"`.
  - `mailer.truncate_notes(text: str, limit: int = 140) -> str` — strip; if longer, cut at last space ≤ limit and append `"…"`.
  - `mailer.describe_filter(f: Filter) -> str` — Italian one-liner, e.g. `"Aree: Sud Est Asiatico · Formula: Discovery · Partenza tra 14 e 120 giorni · Durata 8–17 giorni · Almeno 4 iscritti"`; `Filter()` → `"Tutte le partenze senza coordinatore"`. Used by email footer and GUI.
  - `mailer.render_digest(entries: list[Departure], f: Filter, public_url: str, day: date) -> tuple[str, str, str]` → `(subject, html, text)`; Jinja env with autoescape for `.html`.
  - `mailer.render_stale(hours: int, last_error: str | None) -> tuple[str, str]` → subject `"AMCrawler: nessun aggiornamento da {hours} ore"`, text body including the error.
  - `class mailer.SmtpMailer(host: str, port: int, user: str, password: str, sender: str)` with `send(to: str, subject: str, text: str, html: str | None = None) -> None` (smtplib STARTTLS, `EmailMessage` multipart/alternative). Any other object with the same `send` signature is a valid mailer (tests use a fake that records calls).

- [ ] **Step 1: Write failing tests:**

```python
def test_subject_plural_singular():
    assert subject_for(3, date(2026, 10, 29)) == "3 nuove partenze senza coordinatore · 29 ott"
    assert subject_for(1, date(2026, 1, 5)) == "1 nuova partenza senza coordinatore · 5 gen"
def test_date_range(): assert date_range(date(2026,11,8), date(2026,11,23)) == "8 nov – 23 nov"
def test_truncate_notes():
    assert truncate_notes("breve") == "breve"
    out = truncate_notes("parola " * 40); assert len(out) <= 141 and out.endswith("…")
def test_describe_filter_empty(): assert describe_filter(Filter()) == "Tutte le partenze senza coordinatore"
def test_render_digest_contents():
    subj, html, text = render_digest([dep(name="Giappone Discovery", itinerary_code="9650", participants=8, max_participants=15, countries=("JP",))], Filter(), "https://viaggi.example.com", date(2026,10,29))
    assert "Giappone Discovery" in html and "8/15 iscritti" in html and "Giappone" in html
    assert "https://www.viaggiavventurenelmondo.it/viaggi/9650" in html and "https://viaggi.example.com" in html
    assert "Giappone Discovery" in text
def test_digest_escapes_and_truncates_notes():
    _, html, _ = render_digest([dep(notes="<b>x</b> " + "lungo " * 60)], Filter(), "https://viaggi.example.com", date(2026,10,29))
    assert "<b>x</b>" not in html and "&lt;b&gt;" in html and "…" in html
```

- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_mailer.py -v` → FAIL.
- [ ] **Step 3: Implement** module + templates. HTML email: single column, `max-width:600px`, inline styles only, one card per Departure with the fields of spec §9, button "Vedi viaggio", footer link "Apri AMCrawler" + `describe_filter`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: Italian Digest email rendering and SMTP mailer`

---

### Task 7: Digest job

**Files:**
- Create: `amcrawler/digest.py`
- Test: `tests/test_digest.py`

**Interfaces:**
- Consumes: `Store`, `diff.new_entries`, `mailer.render_digest`, `mailer.render_stale`, `Config`
- Produces:
  - `digest.today_in(tz: str, now: datetime) -> date`
  - `digest.compute_new_entries(store: Store, today: date) -> list[Departure]` — baseline = `baseline_snapshot_id()` (None → empty list) vs `latest_ok_snapshot()` (None → `[]`), with `store.digest_filter()` and `store.hidden_ids()`.
  - `digest.badged_ids(store: Store, today: date) -> set[str]` — `last_sent_entry_ids() | {d.id for d in compute_new_entries(...)}` (spec §6 "GUI New badge").
  - `digest.run_digest(store: Store, mailer, cfg: Config, now: datetime) -> str` returns the recorded status (or `"skipped"`):
    1. `today = today_in(cfg.tz, now)`; if `store.digest_run_on(today, cfg.tz)` exists → return `"skipped"` (no record).
    2. Latest ok snapshot missing or older than 24 h → send stale email to `cfg.digest_to` (hours = whole hours since last ok, or since never → "mai"-safe: use 0 and error "nessuno snapshot riuscito"), record `stale`; if that send raises, still record `stale` with the error.
    3. No previous digest runs → record `baseline` with latest ok snapshot id.
    4. `entries = compute_new_entries`; empty → record `empty`.
    5. Else render + `mailer.send(cfg.digest_to, subject, text, html)`; success → `sent` with entry ids; exception → `failed` with `str(e)`.
    6. Finally `store.prune_snapshots(now - timedelta(days=365))`.

- [ ] **Step 1: Write failing tests** (temp Store, `FakeMailer` recording `(to, subject, text, html)`, a `cfg` built via `load_config({...placeholders...})`):
  `test_first_run_is_baseline_and_sends_nothing`, `test_new_entry_sent_and_recorded` (subject starts `"1 nuova partenza"`, status `sent`, `last_sent_entry_ids()` contains it), `test_no_new_entries_records_empty_and_advances_baseline`, `test_stale_snapshot_sends_warning_and_keeps_baseline` (latest ok 30 h old → subject starts `"AMCrawler: nessun aggiornamento"`, `baseline_snapshot_id()` unchanged), `test_smtp_failure_records_failed` (mailer raises → status `failed`, baseline unchanged), `test_digest_runs_once_per_day` (second call same London date → `"skipped"`, mailer called once), `test_badged_ids_union`.
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_digest.py -v` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: daily Digest job with baseline, stale warning and once-per-day guard`

---

### Task 8: Cloudflare Access auth middleware

**Files:**
- Create: `amcrawler/auth.py`
- Test: `tests/test_auth.py`

**Interfaces:**
- Produces:
  - `auth.AuthError(Exception)`
  - `class auth.AccessVerifier(team_domain: str, aud: str, jwks_fetcher: Callable[[], dict] | None = None, cache_seconds: int = 3600)` — default fetcher GETs `https://{team_domain}/cdn-cgi/access/certs`; `verify(token: str) -> dict` checks RS256, `aud`, `iss == f"https://{team_domain}"`, `exp`; refetches JWKS once on unknown `kid`; raises `AuthError`.
  - `auth.install_auth(app: FastAPI, verifier: AccessVerifier | None, dev_no_auth: bool) -> None` — HTTP middleware: path `/health` always allowed; `dev_no_auth` → allow and `logger.warning("AMC_DEV_NO_AUTH attivo: autenticazione disattivata")`; else token from header `Cf-Access-Jwt-Assertion` or cookie `CF_Authorization`; missing/invalid or `verifier is None` → `PlainTextResponse("Accesso negato", 403)`.

- [ ] **Step 1: Write failing tests** generating an RSA key with `cryptography`, a JWKS dict built via `jwt.algorithms.RSAAlgorithm.to_jwk`, tokens via `jwt.encode(..., headers={"kid": "k1"})`, and a tiny FastAPI app with `/x` and `/health`:
  `test_valid_header_token_allowed`, `test_valid_cookie_token_allowed`, `test_wrong_aud_rejected`, `test_wrong_issuer_rejected`, `test_expired_rejected`, `test_missing_token_rejected` (403, body "Accesso negato"), `test_health_open_without_token`, `test_unknown_kid_refetches_once` (fetcher called twice, then accepted), `test_dev_no_auth_allows`, `test_no_verifier_rejects`.
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_auth.py -v` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: verify Cloudflare Access JWT on every request`

---

### Task 9: Web app — main view, hide/unhide, health

**Files:**
- Create: `amcrawler/web.py`, `amcrawler/templates/base.html`, `amcrawler/templates/index.html`, `amcrawler/templates/_card.html`, `amcrawler/static/app.css`, `amcrawler/static/app.js`
- Test: `tests/test_web.py`, `tests/conftest.py` (shared `dep`, `itin`, `store`, `client` fixtures — move the Task 3/4 helpers here)

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `web.create_app(cfg: Config, store: Store | None = None, mailer=None, start_scheduler: bool = True, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> FastAPI` — creates `Store(cfg.db_path)` + `init()` if not given; mailer defaults to `SmtpMailer` from cfg; installs auth (verifier built only when `cf_team_domain` and `cf_aud` set); stores objects on `app.state`; when `start_scheduler` is true, lifespan calls `scheduler.start_scheduler(app)` (Task 11 provides it; until then guard with `start_scheduler=False` in tests).
  - `web.build_app() -> FastAPI` = `create_app(load_config())` (uvicorn `--factory` target).
  - Routes in this task: `GET /`, `POST /hide/{departure_id}`, `POST /unhide/{departure_id}`, `GET /health`.
  - `GET /` context: Search = `Filter.from_query(request.query_params.multi_items())`; `nascoste=1` → only Hidden Departures, else all non-hidden; results = `filters.apply(...)` on latest ok snapshot sorted by start date; `badged = digest.badged_ids(...)`; status strip (last crawl time in London `HH:MM`, count, new count, red when latest snapshot not ok or latest digest run `failed`/`stale`); filter options = sorted distinct Areas, Formulas, Countries (`(code, country_name_it)`) from the catalogue; map payload embedded as `<script type="application/json" id="map-data">` = `{"departures":[{"id","countries":[...],"new":bool}], "names": {code: name}, "numeric": {code: iso_numeric}}`.
  - `POST /hide/{id}` → `{"ok": true}` (404 JSON if id not in latest ok snapshot); `POST /unhide/{id}` → `{"ok": true}`.
  - `GET /health` → `{"ok": true, "last_ok_snapshot": iso | null}`.

- [ ] **Step 1: Write failing tests** (`TestClient(create_app(cfg_dev, store=seeded_store, mailer=FakeMailer(), start_scheduler=False))`, `cfg_dev` has `dev_no_auth=True`):
  `test_index_renders_cards` (200; names present; `"8/15 iscritti"`; "Vedi viaggio" href to itinerary_url), `test_index_search_filters` (`?formula=Discovery` hides Adventure card), `test_index_ignores_bad_query_values` (`?dfrom=abc&pmin=-3&formula=` → 200, all cards), `test_new_badge_shown` (Departure not in baseline shows `NUOVA`), `test_hide_and_unhide` (POST hide → absent from `/`, present in `/?nascoste=1`; unhide → back), `test_hide_unknown_404`, `test_health_ok`, `test_map_data_embedded` (parse the JSON script; contains `"numeric"` with `"JP": "392"` when a JP Departure exists), `test_auth_enforced_when_not_dev` (cfg with dev_no_auth False and no verifier → `/` 403, `/health` 200).
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_web.py -v` → FAIL.
- [ ] **Step 3: Implement** routes + templates + CSS/JS. Layout per spec §12: top bar, `Lista | Mappa` toggle (≤ 899 px), filter bottom sheet (GET form to `/` with the query keys from Task 1), removable chips, status strip, cards with notes clamped to 2 lines (tap to expand), "Nascondi" with 5 s "Annulla" snackbar (JS calls hide, undo calls unhide), ≥ 44 px tap targets, `prefers-color-scheme` dark theme, `<html lang="it">`, viewport meta. Map container present but empty (Task 10 draws it).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: phone-first main view with Search, cards, hide/undo and health`

---

### Task 10: World map, settings page, preview/test send, PWA

**Files:**
- Create: `amcrawler/static/map.js`, `amcrawler/static/vendor/d3.v7.9.0.min.js`, `amcrawler/static/vendor/topojson-client.3.1.0.min.js`, `amcrawler/static/vendor/countries-50m.json`, `amcrawler/static/icon-192.png`, `amcrawler/static/icon-512.png`, `amcrawler/templates/settings.html`
- Modify: `amcrawler/web.py`, `amcrawler/templates/base.html`, `amcrawler/templates/index.html`, `amcrawler/static/app.css`
- Test: `tests/test_web_settings.py`

**Interfaces:**
- Consumes: Task 9 app + map payload; `store` settings; `digest.compute_new_entries`; `mailer.render_digest`
- Produces routes: `GET /settings`, `POST /settings`, `POST /settings/from-search`, `GET /digest/preview`, `POST /digest/test`, `GET /manifest.webmanifest`. Also `app.state.on_send_time_change: Callable[[str], None]` hook (no-op default; Task 11 sets it to reschedule).

- [ ] **Step 1: Vendor assets** — download `https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js`, `https://cdn.jsdelivr.net/npm/topojson-client@3.1.0/dist/topojson-client.min.js`, `https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/countries-50m.json` into `static/vendor/` with the filenames above. Generate the two PNG icons (simple globe glyph on the theme colour) with a short Python script using only the stdlib or Pillow if available; commit the PNGs, not the script.
- [ ] **Step 2: Write failing tests:**
  `test_settings_page_shows_filter_and_runs`, `test_settings_save_filter_and_time` (POST form fields `area`, `formula`, `dfrom`, …, `send_time=06:30` → 303 to `/settings`; store updated; hook called with `"06:30"`), `test_settings_rejects_bad_send_time` (`7`, `25:00`, `""` → 400 page containing "Orario non valido", store unchanged, hook not called), `test_settings_from_search` (POST with `query="formula=Discovery&pmin=4"` → digest filter equals `Filter(formulas=("Discovery",), min_participants=4)`), `test_digest_preview_renders` (200 HTML containing the would-be New Entries, or "Nessuna nuova partenza" when none), `test_digest_test_send` (FakeMailer receives subject starting `"[PROVA] "`; `recent_digest_runs()` unchanged), `test_manifest` (JSON, `display == "standalone"`, two icons), `test_manifest_linked_with_credentials` (`/` HTML contains `rel="manifest"` and `crossorigin="use-credentials"`).
- [ ] **Step 3: Run** `.venv/Scripts/python -m pytest tests/test_web_settings.py -v` → FAIL.
- [ ] **Step 4: Implement** routes + `settings.html` (Digest Filter form = same fields as Search, `send_time` input type time, read-only recipient, last 10 runs with Italian status labels, "Anteprima" link, "Invia prova" button) + `/` "Usa per il Digest" button posting the current query string to `/settings/from-search`.
- [ ] **Step 5: Implement `map.js`** — reads `#map-data`; `d3.geoNaturalEarth1()` fit to container; draw `topojson.feature(world, world.objects.countries)`; per feature id: count of current-result Departures whose `countries` map (via `numeric`) to that id; fill accent (`var(--accent)`) if any is `new`, else `d3.scaleSequential` over neutral greys by count, none if 0; `<title>` tooltip "Giappone · 3 partenze"; click → navigate to current URL with `country=XX` appended and (phone) `#lista`. Countries with Departures but no shape (no feature with that numeric id) → chips under the map "Non sulla mappa: Maldive (2)". Redraw on resize (debounced).
- [ ] **Step 6: Run** `.venv/Scripts/python -m pytest -v` → all PASS. Manual check: `AMC_DEV_NO_AUTH=1 AMC_DB_PATH=./data/dev.db .venv/Scripts/python -m uvicorn --factory amcrawler.web:build_app --port 8000`, run one crawl (Task 11 command, or `python -c` calling `run_crawl`), open `http://localhost:8000` at 390 px and desktop widths: map draws, tap a country filters, toggle works, hide/undo works, dark mode readable.
- [ ] **Step 7: Commit** `feat: world map, Digest settings, preview/test send and PWA manifest`

---

### Task 11: Scheduler wiring

**Files:**
- Create: `amcrawler/scheduler.py`, `amcrawler/__main__.py`
- Modify: `amcrawler/web.py` (lifespan uses it)
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `crawl.run_crawl`, `digest.run_digest`, `source.make_client`, app.state
- Produces:
  - `scheduler.minus_minutes(hhmm: str, minutes: int) -> tuple[int, int]` (wraps past midnight: `("00:10", 15) -> (23, 55)`)
  - `scheduler.start_scheduler(app: FastAPI) -> BackgroundScheduler` — `BackgroundScheduler(timezone=cfg.tz, job_defaults={"coalesce": True, "misfire_grace_time": 3600, "max_instances": 1})`; jobs `crawl-3h` (cron `hour="*/3", minute=0`), `crawl-predigest` (cron at send_time − 15 min), `digest` (cron at send_time); one-off `crawl-startup` (date trigger now) if latest ok snapshot is None or older than 3 h; sets `app.state.on_send_time_change` to `reschedule`; stored on `app.state.scheduler`; lifespan shuts it down.
  - `scheduler.reschedule(sched: BackgroundScheduler, send_time: str) -> None` — reschedules `crawl-predigest` and `digest`.
  - Job functions wrap everything in try/except + `logger.exception` so a crash never kills the scheduler.
  - `python -m amcrawler crawl` / `python -m amcrawler digest` — run one job immediately with `load_config()` (for manual testing on the NAS: `docker exec amcrawler python -m amcrawler crawl`).

- [ ] **Step 1: Write failing tests:** `test_minus_minutes` (`"07:00"→(6,45)`, `"00:10"→(23,55)`), `test_start_scheduler_registers_jobs` (temp app with `start_scheduler=False`, call `start_scheduler(app)` with scheduler then immediately `shutdown(wait=False)`: job ids `{"crawl-3h","crawl-predigest","digest"}` ⊆ ids; `crawl-startup` present on empty DB; digest trigger fields hour 7 minute 0 in `Europe/London`), `test_reschedule_moves_digest` (`reschedule(s, "06:30")` → digest hour 6 minute 30, predigest 6:15).
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_scheduler.py -v` → FAIL.
- [ ] **Step 3: Implement** + wire into `create_app` lifespan.
- [ ] **Step 4: Run** full suite → PASS.
- [ ] **Step 5: Commit** `feat: in-process scheduler for crawls and the daily Digest`

---

### Task 12: Container and deployment docs

**Files:**
- Create: `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `deploy/swag/viaggi.subdomain.conf.sample`, `deploy/README.md`, `README.md`

**Interfaces:**
- Consumes: `amcrawler.web:build_app`, `/health`
- Produces: image that runs `uvicorn --factory amcrawler.web:build_app --host 0.0.0.0 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips="*"`.

- [ ] **Step 1: Dockerfile** — `python:3.12-slim`; `PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Europe/London`; install `tzdata`? (not needed: Python `zoneinfo` needs system tz data or the `tzdata` pip package — add `tzdata` to `requirements.in` and re-pin in this task); non-root user `app` (uid 99/gid 100 to match Unraid `nobody:users`); `/data` owned by it; `HEALTHCHECK CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5).status==200 else 1)"`.
- [ ] **Step 2: `docker-compose.yml`** — spec §13 exactly: service and `container_name: amcrawler`, `build: .`, `env_file: .env`, `volumes: ["/mnt/user/appdata/amcrawler/data:/data"]`, `networks: [proxy]` with `proxy: external: true`, `restart: unless-stopped`, no `ports`.
- [ ] **Step 3: swag sample** — linuxserver subdomain template: `listen 443 ssl; listen [::]:443 ssl; server_name viaggi.*; include /config/nginx/ssl.conf; client_max_body_size 0; location / { include /config/nginx/proxy.conf; include /config/nginx/resolver.conf; set $upstream_app amcrawler; set $upstream_port 8000; set $upstream_proto http; proxy_pass $upstream_proto://$upstream_app:$upstream_port; }`.
- [ ] **Step 4: `deploy/README.md`** — numbered steps (placeholders only): create private GitHub repo + push; on NAS `git clone` to `/mnt/user/appdata/amcrawler/app`; `mkdir -p /mnt/user/appdata/amcrawler/data && chown 99:100`; `cp .env.example .env` and fill; Gmail app password (Google Account → Security → 2-Step Verification → App passwords); copy swag sample to `/mnt/user/appdata/swag/nginx/proxy-confs/viaggi.subdomain.conf` and `docker restart swag`; Cloudflare Zero Trust → Networks → Tunnels → existing tunnel → Public hostname `viaggi.<domain>` → service pointing at swag the same way the existing hostnames do; Access → Applications → Self-hosted, same hostname, identity provider Google, policy Allow → Emails (Owner + maintainer), session duration 1 month; copy Application Audience (AUD) tag + team domain into `.env`; `docker compose up -d --build`; verify `docker exec amcrawler python -m amcrawler crawl`, open the site on the phone, "Aggiungi a schermata Home"; update procedure `git pull && docker compose up -d --build`. `README.md`: one paragraph + links to spec, CONTEXT.md, deploy/README.md, dev commands.
- [ ] **Step 5: Verify** `docker build -t amcrawler:test .` succeeds; `docker run --rm -d --name amc-test -e AMC_DEV_NO_AUTH=1 -e AMC_DB_PATH=/data/t.db -p 8001:8000 amcrawler:test`; `curl -s localhost:8001/health` → `{"ok":true,...}`; `docker exec amc-test python -m amcrawler crawl` exits 0; `curl -s localhost:8001/ | grep -c "iscritti"` ≥ 1; `docker rm -f amc-test`. Confirm `grep -rniE "<your-domain-fragment>|gmail\.com\"|@gmail" --exclude-dir=.git --exclude-dir=.venv .` finds only `smtp.gmail.com`.
- [ ] **Step 6: Commit** `feat: Dockerfile, compose, swag sample and deployment guide`
