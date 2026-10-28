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
            duration INT, participants INT, max_participants INT, notes TEXT, catalogued INT NOT NULL DEFAULT 1,
            PRIMARY KEY(snapshot_id, departure_id))
digest_runs(id INTEGER PK, run_at TEXT, snapshot_id INT,
            status TEXT,  -- 'baseline' | 'sent' | 'empty' | 'failed' | 'stale'
            entry_count INT, error TEXT)
digest_entries(digest_run_id INT, departure_id TEXT, PRIMARY KEY(...))
hidden(departure_id TEXT PK, code TEXT, hidden_at TEXT)
settings(key TEXT PK, value_json TEXT)  -- 'digest_filter', 'send_time',
                                          -- 'digest_enabled', 'digest_weekdays', 'digest_quiet_mail'
```

Areas/Countries/Formula for a Departure come from the joined Itinerary at read
time (the catalogue rarely changes; snapshot rows keep name/formula for display
history). `catalogued` records whether the Itinerary had a catalogue row when the
Departure was crawled (see §6). Snapshots older than 365 days are deleted by the daily Digest job.

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
       and not (d.id in baseline and previously_matching(baseline[d.id]))]

previously_matching(b) = matches(b, digest_filter, today)
                         and (b.catalogued or not (filter.areas or filter.countries))
```

- A baseline row with no catalogue data at crawl time (`catalogued` false) is unknown
  when the filter constrains Areas/Countries: it could not match then, so it counts as
  not previously matching and is reported once the catalogue knows it. Without
  Area/Country constraints catalogue data is irrelevant and the normal rule applies
  (a permanently uncatalogued Departure is not reported every day).

- Both sides use **today's** filter and **today's** date, so editing the filter
  never floods the next Digest, while a Departure crossing the minimum
  Participants or entering the date window *is* new.
- Every daily run records a `digest_run` (even with zero entries, status
  `empty`) so the baseline advances daily.
- **An empty snapshot never becomes the baseline (G2).** When the latest ok snapshot has
  0 Departures (an accepted drop to empty), the run is recorded as `empty` with the
  *current* baseline's `snapshot_id`, so the baseline does not advance; when the API
  recovers the next Digest diffs against the pre-outage baseline and reports only the
  genuinely new Departures. The first-ever run with 0 Departures still records `baseline`
  with that snapshot (accepted: there is nothing older to protect). The sanity guard
  compares with the latest ok snapshot that has Departures, so a partial result after an
  accepted empty one also needs the 24 h confirmation, while an empty result right after
  an accepted empty snapshot is not a drop and is accepted at once (a persistent empty list
  must not cycle through rejections).
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
  reschedules both jobs live). The job only acts on a **send day**: Digest enabled
  (`digest_enabled`, default on) and today's weekday (local, Monday = 0) in
  `digest_weekdays` (default all seven). On any other day it does nothing: no
  `digest_run`, no mail, no stale warning, baseline untouched, one INFO line
  (`digest skipped: paused` / `digest skipped: not a send day`). The CLI
  `python -m amcrawler digest` follows the same rule, prints the reason and exits 0.
  After a pause or skipped days the next Digest therefore reports every New Entry
  since the last baseline.
- Catch-up: jobs live in memory, so at scheduler start and whenever `send_time` is
  changed, if local now is past today's `send_time` by less than 6 h (real
  elapsed time, so DST days are not an hour off), today is a send day and **no Digest
  run of any status** exists for today, a one-off `digest-catchup` job (date trigger, now + 2 min, after any
  startup crawl) runs the Digest. A `failed`/`stale` run today therefore also stops the
  catch-up (else every settings save or restart in the window would resend the same
  mail); the scheduled job still skips only a baseline/sent/empty run, so a failed or
  stale run can be retried at the next scheduled time.
- Catalogue refresh: inside the crawl, if `itineraries.fetched_at` is older than 24 h,
  **or** (forced) if a fetched Departure belongs to an Itinerary the catalogue does not
  know yet, at most once per hour (last attempt kept in `settings`). This makes the
  catalogue catch up the same day in the normal case; if it still cannot (refresh
  failed, throttled, or the Itinerary is not listed yet), the `catalogued` flag (§6)
  guarantees the Departure is reported once the catalogue does know it.
- APScheduler `coalesce=True`, `misfire_grace_time=3600`, `max_instances=1`.

## 8. Crawl and failure handling

- Any `SourceError` (HTTP ≠ 200, `success` false, missing keys, JSON error,
  timeout) → `snapshots` row with `ok=0`, `error` set. Retry at next schedule.
- **Sanity guard:** if the API returns 0 Departures while the previous ok
  snapshot had ≥ 10, record `ok=0, error='suspicious empty result'`. Protects the
  baseline from a broken API wiping the list. The same guard records
  `error='suspicious drop'` when the new count is below 50 % of a previous ok
  snapshot that had ≥ 10 (partial API results). A rejected snapshot keeps the fetched
  count in `departure_count`. **Recovery:** the guard compares with the last ok snapshot,
  which stops advancing, so a genuine drop would be rejected forever; when the previous
  suspicious rejections form a **streak** (the trailing run of rejected snapshots since the
  last ok snapshot (other failures, e.g. HTTP errors, are skipped) whose counts are all within ±10 % of this one; exactly 0 for an empty
  result) of at least `CONFIRM_SUSPICIOUS = 2` and **the oldest of them is at least
  `CONFIRM_SPAN = 24 h` old**, this result is accepted (ok=1) and a WARNING
  "suspicious result confirmed over <h> h (<n> crawls); accepting" (n = streak + this crawl) is logged. (G1) The 24 h
  span means a broken API serving 200-OK empty or partial data for a few hours, or extra
  crawls from restarts/CLI, cannot be accepted; an inconsistent count in between restarts
  the streak from that point.
- The parser also drops full Departures (`pax >= paxMax`, `paxMax > 0`), even
  though the request already asks for `isFull=0`.
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
- Not sent on days with zero New Entries, unless `digest_quiet_mail` is on: then a
  short "nessuna novità" mail goes out (subject `AMCrawler: nessuna novità`; says no
  new Departures match the Digest Filter today, with the "Apri AMCrawler" link and
  the filter line). The run stays `empty` when it is sent and is `failed` when the
  send fails, like a failed Digest. Never sent for a `baseline` run.
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
| `POST /digest/current` | Mail every Departure matching the current Digest Filter (latest OK snapshot, Hidden excluded, by start date then name) to `DIGEST_TO`, subject `AMCrawler: partenze attuali (N)`; records nothing. No match: nothing is sent. Redirects to `/settings?elenco=ok&n=N` (or `vuoto`, `destinatario`, `smtp`, `errore`) |
| `GET /health` | `200 {"ok": true, "last_ok_snapshot": "..."}`; no auth |
| `GET /manifest.webmanifest`, `/static/*` | PWA + assets |

### Main view `/`

- **Top bar:** title, `Lista | Mappa` segmented toggle (phone only), filter button
  opening a bottom sheet with the Search form, settings icon.
- **Status strip:** "Aggiornato alle 15:00 · 59 partenze · 4 nuove" ("Aggiornato il 26/10 alle 15:00" when the last OK snapshot is not from today). Red if the
  last crawl failed, the last Digest failed, or the catalogue is older than 72 h
  ("catalogo non aggiornato").
- **Active filter chips** under the bar, each removable.
- **Search box** under the top bar (`q`, GET `/` and the Nascoste view): every
  whitespace-separated term must occur, case- and accent-insensitively, in the
  Itinerary name, Departure code, Itinerary code, Formula, Italian Country names or
  Area names. It is part of Search only, never of the Digest Filter ("Usa per il
  Digest" does not carry it), is applied before list, map, counts and status strip,
  shows as a removable chip, and "Azzera" clears it.
- **List:** cards sorted by start date. Card ("Striscia date", mockup in
  `docs/design/card-variants.html`) = name, `NUOVA` badge if New, Countries and
  Formula, a date strip (weekday + start date, duration, weekday + end date; the year
  only when it is not the current one), `8/15 iscritti` with a countdown ("Parte
  oggi", "Parte domani", "Parte tra N giorni" up to 21 days in an amber pill,
  "tra N giorni" beyond, "In corso" once started), one block per seat (a continuous
  bar when the seat count is unknown or above 30), notes (2 lines, tap to expand),
  the Departure code, "Vedi" link (new tab) and "Nascondi" button. Hide removes the
  card with a 5 s "Annulla" snackbar.
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

Digest Filter form (same fields as Search), "Digest attivo", send weekdays (L M M G V S D
toggles; at least one when enabled), "Invia anche senza novità", send time (`HH:MM`),
read-only recipient address, last 10 digest_runs with status, "Anteprima", "Invia prova"
and "Invia elenco attuale" buttons, and an "Account" section with "Esci" (link to
`/cdn-cgi/access/logout`, handled by Cloudflare Access at the edge). One "Salva" saves all fields.

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
