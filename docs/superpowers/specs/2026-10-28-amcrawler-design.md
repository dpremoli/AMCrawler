# AMCrawler — design spec

Date: 2026-10-28 · Status: approved in conversation (grilling rounds 1–3)

Vocabulary follows [CONTEXT.md](../../../CONTEXT.md). Decisions with lasting
weight are in [docs/adr](../../adr/).

## 1. Goal

Recreate Avventure nel Mondo's "riepilogo dei viaggi in partenza senza
Coordinatore con almeno un partecipante" for one **Owner** (a Coordinator):

- a daily **Digest** email listing **New Entries**;
- a phone-first web GUI to browse **Uncovered Departures** (list + world map),
  hide Departures, and edit the **Digest Filter**.

Runs as one Docker container on the maintainer's Unraid NAS, reached only
through Cloudflare Tunnel → swag → container, gated by Cloudflare Access (Google).

**Never commit** the public hostname, allowed emails, or SMTP credentials. They
live only in the NAS `.env` and the Cloudflare dashboard. Every file in the repo
uses placeholders (`viaggi.example.com`, `owner@example.com`).

## 2. Data source (verified 2026-10-28)

Base URL `https://www.viaggiavventurenelmondo.it`. Public, no login.

### 2.1 Uncovered Departures

`GET /api/operative/tours?fromTotalPax=1&isFull=0&hasAssignment=0&spg={page}`

- Response: `{"success": true, "data": {"results": [...], "maxResultsPerPage": 30}}`.
- Page `spg` starts at 1. Stop when a page returns fewer than `maxResultsPerPage`
  results. Safety cap: 50 pages.
- `hasAssignment=0` already excludes Departures of Coordinator-free Itineraries
  (`base.tourleaderMode == "no-tourleader"`). Still drop any result whose
  `tourleaderMode != "regular"` or whose `assignment` is non-null (defence in depth).
- Fields used per result: `id`, `code` (e.g. `2026-0041`), `startDate`,
  `endDate` (`YYYY-MM-DD`), `duration`, `pax` (Participants), `paxMax`,
  `notes`, `status`, `base.id`, `base.code`, `base.name`,
  `base.category.name` (Formula), `base.tourleaderMode`.
- **Do not persist any Coordinator data** (`assignment.tourleader` holds names and
  phone numbers). Test fixtures must have it stripped.
- On 2026-10-28 this returned 59 Departures over 2 pages in ~0.25 s.

### 2.2 Itinerary catalogue

`GET /data/bases.js` → `"use strict";var BASES={bases:[...],search:...};`

Not JSON (unquoted top-level keys). Parse by locating `bases:` and using
`json.JSONDecoder().raw_decode` from the following `[`.

Per Itinerary (1,299 on 2026-10-28) use: `id`, `code`, `name`,
`category.name`, `tourleaderMode`, `countries[] {isoCode2, name}`,
`geographicAreas[] {name, slug, ordinal}` (18 Areas).

Departures join to Itineraries on `base.id`. A Departure whose Itinerary is
missing from the catalogue still appears, with no Areas/Countries.

### 2.3 Country folding

The catalogue uses 14 pseudo-codes for sub-national places. Fold them before
anything else:

| Code | Name | Folds to |
|---|---|---|
| CT, IA, RD, ND | Centro Italia, Sicilia, Sardegna, Nord Italia | IT |
| ZE, MI | Azzorre, Madeira | PT |
| LE | Isole Baleari | ES |
| ZI | Scozia | GB |
| TB | Tibet | CN |
| AK | Alaska | US |
| PX | Patagonia | AR **and** CL |
| IC | Isole Canarie | ES |
| SU | Sud Italia | IT |
| AN | Antille Olandesi | CW **and** BQ |

`XK` (Kosovo) is a real user-assigned code: it is kept as-is and displayed as
"Kosovo" (pycountry does not know it).

After folding, deduplicate. Any other code that is not a valid ISO 3166-1
alpha-2 is logged once per crawl and kept as-is (filterable, not on the map).

### 2.4 Link to a Departure

`{base}/viaggi/{itinerary code}` (e.g. `/viaggi/1410`) — the Itinerary page,
which lists its Departures. There is no per-Departure URL.

### 2.5 Politeness

`User-Agent: AMCrawler/1.0 (personal use)`, sequential requests, 1 s pause
between pages, 20 s timeout, catalogue fetched at most once per 24 h.

## 3. Architecture

One Python 3.12 process: FastAPI + Jinja2 served by uvicorn (**exactly one
worker**: the scheduler lives in-process), APScheduler, SQLite (stdlib
`sqlite3`), httpx. Frontend: server-rendered HTML, small vanilla JS, d3-geo +
topojson-client + world-atlas `countries-50m.json` vendored under `static/`
(no CDN).

Package `amcrawler/`, one responsibility per module:

| Module | Responsibility | Depends on |
|---|---|---|
| `models.py` | Dataclasses `Itinerary`, `Departure`, `Filter`; no I/O | — |
| `geo.py` | Pseudo-code folding; alpha-2 → ISO numeric (via `pycountry`) for the map; Italian country names | `pycountry` |
| `source.py` | Fetch + parse §2.1 and §2.2 into models; raises `SourceError` on any shape/HTTP problem | httpx, models, geo |
| `filters.py` | `matches(departure, filter, today) -> bool`; pure | models |
| `diff.py` | `new_entries(current, baseline, filter, hidden, today) -> list[Departure]`; pure | filters |
| `store.py` | SQLite schema, migrations, all reads/writes | models |
| `crawl.py` | One crawl: source → store snapshot; sanity guard | source, store |
| `digest.py` | One Digest run: diff → render → send; failure warning | diff, store, mailer |
| `mailer.py` | Render Jinja email templates; SMTP send | — |
| `auth.py` | Cloudflare Access JWT middleware | PyJWT |
| `web.py` | FastAPI app, routes, templates | everything above |
| `scheduler.py` | APScheduler jobs wired at app startup | crawl, digest |
| `config.py` | Settings from env vars (§10) | — |

Pure modules (`filters`, `diff`, `geo`, `models`) carry the logic and the bulk of
the tests.

## 4. Data model (SQLite at `/data/amcrawler.db`)

```
itineraries(id TEXT PK, code TEXT, name TEXT, formula TEXT,
            areas_json TEXT, countries_json TEXT, coordinator_free INT,
            fetched_at TEXT)
snapshots(id INTEGER PK, taken_at TEXT, ok INT, error TEXT, departure_count INT)
snapshot_departures(snapshot_id INT, departure_id TEXT, itinerary_id TEXT,
            itinerary_code TEXT, code TEXT, name TEXT, formula TEXT, start_date TEXT, end_date TEXT,
            duration INT, participants INT, max_participants INT, notes TEXT,
            PRIMARY KEY(snapshot_id, departure_id))
digest_runs(id INTEGER PK, run_at TEXT, snapshot_id INT,
            status TEXT,  -- 'baseline' | 'sent' | 'empty' | 'failed' | 'stale'
            entry_count INT, error TEXT)
digest_entries(digest_run_id INT, departure_id TEXT, PRIMARY KEY(...))
hidden(departure_id TEXT PK, code TEXT, hidden_at TEXT)
settings(key TEXT PK, value_json TEXT)  -- 'digest_filter', 'send_time'
```

Areas/Countries/Formula for a Departure come from the joined Itinerary at read
time (the catalogue rarely changes; snapshot rows keep name/formula for display
history). Snapshots older than 365 days are deleted by the daily Digest job.

## 5. Filter model

`Filter` fields (all optional; empty = no constraint):

- `areas: list[str]` (Area names), `countries: list[str]` (folded alpha-2),
  `formulas: list[str]`
- `days_from: int | None`, `days_to: int | None` — departure date window relative
  to *today* in `Europe/London`
- `duration_min`, `duration_max: int | None` (days)
- `min_participants: int` (default 1)

Semantics: OR within a list field, AND across fields. A Departure matches
`areas` if any of its Itinerary's Areas is selected; same for `countries`.
Departures with no catalogue data fail any non-empty `areas`/`countries`
constraint.

**Search** uses the same model, encoded in the URL query string
(`?area=Sud+Est+Asiatico&formula=Discovery&dfrom=14&dto=120&...`) so Back and
bookmarks work. The **Digest Filter** is stored in `settings`.

Default Digest Filter on first start: `min_participants=1`, everything else empty.

## 6. New Entries (ADR 0002)

```
baseline = snapshot of the most recent digest_run (any status except 'failed'/'stale')
current  = most recent snapshot with ok=1
new = [d for d in current
       if d.id not in hidden
       and matches(d, digest_filter, today)
       and not (d.id in baseline and matches(baseline[d.id], digest_filter, today))]
```

- Both sides use **today's** filter and **today's** date, so editing the filter
  never floods the next Digest, while a Departure crossing the minimum
  Participants or entering the date window *is* new.
- Every daily run records a `digest_run` (even with zero entries, status
  `empty`) so the baseline advances daily.
- **First ever run** (no previous digest_run): record status `baseline`, send
  nothing. Avoids a 59-Departure first email.
- Ordering: by `start_date`, then name.

**GUI "New" badge** (Q13 A): a Departure is badged if it is in the
`digest_entries` of the latest `sent` run, **or** it would be a New Entry if the
Digest ran now. Same set drives the map's accent colour.

## 7. Schedule (timezone `Europe/London`)

- Crawl: every 3 h at :00 (00, 03, …, 21) **and** at `send_time − 15 min`.
  Also once at startup if the last ok snapshot is older than 3 h.
- Digest: daily at `send_time` (default `07:00`, editable in GUI; changing it
  reschedules both jobs live).
- Catalogue refresh: inside the crawl, if `itineraries.fetched_at` is older than 24 h,
  **or** (forced) if a fetched Departure belongs to an Itinerary the catalogue does not
  know yet, at most once per hour (last attempt kept in `settings`). Without this a
  new Itinerary has no Areas/Countries, fails an Area/Country Digest Filter, and is
  never reported once the catalogue catches up.
- APScheduler `coalesce=True`, `misfire_grace_time=3600`, `max_instances=1`.

## 8. Crawl and failure handling

- Any `SourceError` (HTTP ≠ 200, `success` false, missing keys, JSON error,
  timeout) → `snapshots` row with `ok=0`, `error` set. Retry at next schedule.
- **Sanity guard:** if the API returns 0 Departures while the previous ok
  snapshot had ≥ 10, record `ok=0, error='suspicious empty result'`. Protects the
  baseline from a broken API wiping the list.
- Catalogue failure is non-fatal if a previous catalogue exists (logged, reused).
  The GUI status strip turns red with "catalogo non aggiornato" when the catalogue
  is older than 72 h.
- **Digest when data is stale:** if the latest ok snapshot is older than 24 h at
  `send_time`, send a short warning email instead
  ("AMCrawler: nessun aggiornamento da N ore — ultimo errore: …"), record
  status `stale`, and do not advance the baseline.
- SMTP failure → status `failed`, error stored, baseline not advanced; shown in
  the GUI status strip.

## 9. Digest email

- To `DIGEST_TO`, from `DIGEST_FROM` (the maintainer's Gmail), via
  `smtp.gmail.com:587` STARTTLS + app password.
- Subject (Italian): `3 nuove partenze senza coordinatore · 29 ott`;
  singular `1 nuova partenza senza coordinatore · 29 ott`. Month abbreviations
  `gen feb mar apr mag giu lug ago set ott nov dic`, day without leading zero.
- Not sent on days with zero New Entries.
- HTML: single column, max-width 600 px, inline CSS, readable in the Gmail app.
  Plain-text alternative part included.
- One card per New Entry: name (bold), code, `dd mmm – dd mmm` dates,
  duration (`16 giorni`), Participants `8/15 iscritti`, Formula, Countries
  (Italian names), notes truncated to 2 lines (~140 chars + "…"), button
  "Vedi viaggio" → §2.4 link.
- Footer: link "Apri AMCrawler" → `AMC_PUBLIC_URL`, and the active Digest
  Filter in one line.
- Templates: `templates/email/digest.html`, `digest.txt`, `stale.txt`.

## 10. Configuration (env vars, `.env` on the NAS only)

| Var | Example (placeholder) | Notes |
|---|---|---|
| `AMC_PUBLIC_URL` | `https://viaggi.example.com` | links in email |
| `CF_ACCESS_TEAM_DOMAIN` | `example.cloudflareaccess.com` | JWKS at `https://{team}/cdn-cgi/access/certs` |
| `CF_ACCESS_AUD` | `abc123…` | Access application AUD tag |
| `SMTP_HOST` / `SMTP_PORT` | `smtp.gmail.com` / `587` | |
| `SMTP_USER` / `SMTP_PASSWORD` | `maintainer@example.com` / app password | |
| `DIGEST_FROM` / `DIGEST_TO` | `maintainer@example.com` / `owner@example.com` | |
| `TZ` | `Europe/London` | |
| `AMC_DB_PATH` | `/data/amcrawler.db` | |
| `AMC_DEV_NO_AUTH` | `0` | `1` disables JWT check for local dev; logs a warning on every request |

Repo ships `.env.example` with placeholders; `.gitignore` excludes `.env`,
`data/`, `*.db`.

## 11. Authentication (ADR 0001)

Middleware on every route except `GET /health`:

1. Read `Cf-Access-Jwt-Assertion` header (fallback: `CF_Authorization` cookie).
2. Verify RS256 signature against JWKS from `CF_ACCESS_TEAM_DOMAIN` (cached 1 h,
   refetched on unknown `kid`), `aud == CF_ACCESS_AUD`,
   `iss == https://{CF_ACCESS_TEAM_DOMAIN}`, not expired.
3. Failure → `403` plain text "Accesso negato". No redirect, no login page.

Who may sign in is decided only by the Access policy (Owner + maintainer).

## 12. GUI (Italian, phone first)

Design targets 390 px wide first; ≥ 900 px shows list and map side by side.
Tap targets ≥ 44 px. System font stack. Light and dark via
`prefers-color-scheme`.

### Routes

| Route | Purpose |
|---|---|
| `GET /` | Main view. Query string = Search. List + map. |
| `POST /hide/{departure_id}` / `POST /unhide/{departure_id}` | JSON `{ok}`; JS updates in place |
| `GET /settings` · `POST /settings` | Digest Filter + send time |
| `POST /settings/from-search` | Copy current Search into Digest Filter, redirect to `/settings` |
| `GET /digest/preview` | Render what the Digest would send now (HTML, in browser) |
| `POST /digest/test` | Send that preview to `DIGEST_TO` now, marked "[PROVA]"; does not touch digest_runs |
| `GET /health` | `200 {"ok": true, "last_ok_snapshot": "..."}`; no auth |
| `GET /manifest.webmanifest`, `/static/*` | PWA + assets |

### Main view `/`

- **Top bar:** title, `Lista | Mappa` segmented toggle (phone only), filter button
  opening a bottom sheet with the Search form, settings icon.
- **Status strip:** "Aggiornato alle 15:00 · 59 partenze · 4 nuove". Red if the
  last crawl failed or the last Digest failed.
- **Active filter chips** under the bar, each removable.
- **List:** cards sorted by start date. Card = name, `NUOVA` badge if New,
  dates, duration, `8/15 iscritti`, Formula, Countries, notes (2 lines, tap to
  expand), "Vedi viaggio" link (new tab), "Nascondi" button. Hide removes the card
  with a 5 s "Annulla" snackbar.
- **Map:** d3 `geoNaturalEarth1`, countries shaded: accent colour if they contain
  ≥ 1 New Departure in the current results; otherwise a neutral sequential shade
  by count; uncoloured if 0. A Departure counts in every Country it visits.
  Tap/click a country → adds a `Paese` chip and (phone) switches to the list.
  Countries without a 50m shape appear as chips under the map "Non sulla mappa:
  Maldive (2)".
- **Hidden view:** `?nascoste=1` shows only Hidden Departures with "Ripristina".
- Map data is embedded in the page as JSON (`<script type="application/json">`),
  so no extra API.

### Settings `/settings`

Digest Filter form (same fields as Search), send time (`HH:MM`), read-only
recipient address, last 10 digest_runs with status, "Anteprima" and
"Invia prova" buttons.

### PWA

`manifest.webmanifest` (name "AMCrawler", `display: standalone`, theme colour,
192/512 px icons) linked with `crossorigin="use-credentials"` so Access cookies
are sent. No service worker, no offline mode.

## 13. Deployment

- `Dockerfile`: `python:3.12-slim`, non-root user, `pip install` from pinned
  `requirements.txt`, `HEALTHCHECK` curl-less (python one-liner on `/health`),
  `CMD uvicorn --factory amcrawler.web:build_app --host 0.0.0.0 --port 8000 --workers 1`.
- `docker-compose.yml`: service/container `amcrawler`, `build: .`,
  `env_file: .env`, volume `/mnt/user/appdata/amcrawler/data:/data`,
  network `proxy` (external), `restart: unless-stopped`, **no `ports`**.
- NAS layout: repo cloned at `/mnt/user/appdata/amcrawler/app`, `.env` next to
  `docker-compose.yml` there (gitignored). Update = `git pull && docker compose up -d --build`.
- `deploy/swag/viaggi.subdomain.conf.sample`: standard linuxserver swag subdomain
  conf, `server_name viaggi.*;`, `set $upstream_app amcrawler; set $upstream_port 8000; set $upstream_proto http;`.
- `deploy/README.md`: step-by-step — clone, `.env`, compose up, copy swag conf,
  Cloudflare Tunnel public hostname → swag (same as existing apps), Access
  self-hosted app with Google IdP, policy "Allow emails" (Owner + maintainer),
  session duration 1 month, copy AUD tag into `.env`; Gmail app password steps.

## 14. Testing

- `pytest`. Fixtures captured from the live API/catalogue on 2026-10-28,
  trimmed, with `assignment.tourleader` removed.
- Unit: `source` parsing (both endpoints, pagination stop, malformed payloads →
  `SourceError`), `geo` folding incl. Patagonia → 2 countries, `filters` (each
  field, empty = any, OR/AND), `diff` (the four agreed scenarios: filter widened →
  not new; 3→4 Participants with min 4 → new; Coordinator drop-out re-entry → new;
  1→0→1 between Digests → not new; plus hidden, first-run baseline), subject
  formatting (singular/plural, Italian months), `auth` with a locally generated
  RSA key + fake JWKS (valid, wrong aud, expired, missing, `/health` open).
- Integration: `crawl` + `digest` with httpx mocked (respx) and a fake SMTP
  (captures messages) on a temp SQLite: stale warning, sanity guard, SMTP failure.
- Web: FastAPI `TestClient` with `AMC_DEV_NO_AUTH=1` — `/` renders with fixtures,
  Search query params filter, hide/unhide, settings save + from-search, preview.
- Live smoke test `tests/test_live.py` marked `live`, skipped unless
  `-m live`: real API returns ≥ 1 page with expected keys.

## 15. Out of scope

Multiple users or per-user settings, push notifications, cost filter, departure
cities, favourites/stars, offline mode, map image in email, storing any
Coordinator personal data.
