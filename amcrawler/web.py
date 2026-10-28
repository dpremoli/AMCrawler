"""FastAPI app: main view, hide/unhide, health, auth wiring."""
from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qsl, urlencode
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import digest, filters
from .auth import AccessVerifier, install_auth
from .config import Config, load_config
from .geo import country_name_it, iso_numeric
from .mailer import SmtpMailer, date_range, render_digest, truncate_notes
from .models import Departure, Filter
from .source import itinerary_url
from .store import Store

_PKG = Path(__file__).parent
_BAD_DIGEST = ("failed", "stale")
_RUN_LABELS = {
    "sent": "Inviato",
    "baseline": "Riferimento iniziale",
    "empty": "Nessuna novità",
    "failed": "Invio non riuscito",
    "stale": "Dati non aggiornati",
}
_SEND_TIME = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
log = logging.getLogger(__name__)

_INT_CHIPS = {
    "dfrom": "Partenza tra almeno {} giorni",
    "dto": "Partenza entro {} giorni",
    "dmin": "Durata minima {} giorni",
    "dmax": "Durata massima {} giorni",
    "pmin": "Almeno {} iscritti",
}


def _chip_label(key: str, value: str) -> str:
    if key == "area":
        return f"Area: {value}"
    if key == "country":
        return f"Paese: {country_name_it(value)}"
    if key == "formula":
        return f"Formula: {value}"
    return _INT_CHIPS[key].format(value)


def _href(pairs: list[tuple[str, str]], hidden_view: bool) -> str:
    if hidden_view:
        pairs = pairs + [("nascoste", "1")]
    return "/?" + urlencode(pairs) if pairs else "/"


def _chips(f: Filter, hidden_view: bool) -> list[dict]:
    pairs = f.to_query()
    return [
        {"label": _chip_label(k, v),
         "href": _href(pairs[:i] + pairs[i + 1:], hidden_view)}
        for i, (k, v) in enumerate(pairs)
    ]


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def create_app(
    cfg: Config,
    store: Store | None = None,
    mailer=None,
    start_scheduler: bool = True,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> FastAPI:
    if store is None:
        store = Store(cfg.db_path)
        store.init()
    if mailer is None:
        mailer = SmtpMailer(cfg.smtp_host, cfg.smtp_port, cfg.smtp_user,
                            cfg.smtp_password, cfg.digest_from)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_scheduler:
            from . import scheduler  # imported lazily: only needed when running for real

            scheduler.start_scheduler(app)
        try:
            yield
        finally:
            sched = getattr(app.state, "scheduler", None)
            if sched is not None:
                sched.shutdown(wait=False)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.cfg = cfg
    app.state.store = store
    app.state.mailer = mailer
    app.state.now = now
    app.state.on_send_time_change = lambda hhmm: None  # Task 11 reschedules here

    verifier = None
    if cfg.cf_team_domain and cfg.cf_aud:
        verifier = AccessVerifier(cfg.cf_team_domain, cfg.cf_aud)
    install_auth(app, verifier, cfg.dev_no_auth)

    templates = Jinja2Templates(directory=str(_PKG / "templates"))
    templates.env.trim_blocks = True
    templates.env.lstrip_blocks = True
    templates.env.globals.update(
        date_range=date_range, truncate_notes=truncate_notes,
        country_name_it=country_name_it, itinerary_url=itinerary_url)
    templates.env.filters["country_name_it"] = country_name_it
    app.state.templates = templates

    app.mount("/static", StaticFiles(directory=str(_PKG / "static")), name="static")

    def latest_ok_departures() -> list[Departure]:
        snap = store.latest_ok_snapshot()
        return store.snapshot_departures(snap.id) if snap else []

    def status_strip(shown: int, new: int) -> dict:
        latest = store.latest_snapshot()
        last_ok = store.latest_ok_snapshot()
        runs = store.recent_digest_runs(1)
        digest_bad = bool(runs) and runs[0].status in _BAD_DIGEST
        crawl_bad = latest is not None and not latest.ok
        bad = crawl_bad or digest_bad
        if last_ok is None:
            text = "Nessun aggiornamento riuscito"
        else:
            at = last_ok.taken_at.astimezone(ZoneInfo(cfg.tz)).strftime("%H:%M")
            text = (f"Aggiornato alle {at} · {_plural(shown, 'partenza', 'partenze')}"
                    f" · {_plural(new, 'nuova', 'nuove')}")
        if crawl_bad:
            text += " · ultimo aggiornamento non riuscito"
        elif digest_bad:
            text += " · ultimo Digest non riuscito"
        return {"text": text, "bad": bad}

    @app.get("/")
    def index(request: Request):
        today = digest.today_in(cfg.tz, now())
        search = Filter.from_query(request.query_params.multi_items())
        hidden_view = request.query_params.get("nascoste") == "1"
        hidden = store.hidden_ids()
        pool = [d for d in latest_ok_departures() if (d.id in hidden) == hidden_view]
        results = sorted(filters.apply(pool, search, today),
                         key=lambda d: (d.start_date, d.name, d.id))
        badged = digest.badged_ids(store, today)
        new_count = sum(1 for d in results if d.id in badged)

        itineraries = list(store.itineraries().values())
        codes = {c for i in itineraries for c in i.countries}
        map_codes = sorted({c for d in results for c in d.countries})
        map_data = {
            "departures": [{"id": d.id, "name": d.name, "countries": list(d.countries),
                            "new": d.id in badged} for d in results],
            "names": {c: country_name_it(c) for c in map_codes},
            "numeric": {c: iso_numeric(c) for c in map_codes},
        }
        ctx = {
            "results": results,
            "badged": badged,
            "hidden_view": hidden_view,
            "search": search,
            "chips": _chips(search, hidden_view),
            "status": status_strip(len(results), new_count),
            "areas": sorted({a for i in itineraries for a in i.areas}),
            "formulas": sorted({i.formula for i in itineraries if i.formula}),
            "countries": sorted(((c, country_name_it(c)) for c in codes),
                                key=lambda t: t[1]),
            "map_data": map_data,
            "list_href": _href(search.to_query(), False),
            "hidden_href": _href(search.to_query(), True),
            "search_query": urlencode(search.to_query()),
        }
        return templates.TemplateResponse(request, "index.html", ctx)

    @app.post("/hide/{departure_id}")
    def hide(departure_id: str):
        match = next((d for d in latest_ok_departures() if d.id == departure_id), None)
        if match is None:
            return JSONResponse({"ok": False, "error": "partenza non trovata"},
                                status_code=404)
        store.hide(match.id, match.code, now())
        return {"ok": True}

    @app.post("/unhide/{departure_id}")
    def unhide(departure_id: str):
        store.unhide(departure_id)
        return {"ok": True}

    @app.get("/manifest.webmanifest")
    def manifest():
        return JSONResponse({
            "name": "AMCrawler",
            "short_name": "AMCrawler",
            "lang": "it",
            "start_url": "/",
            "display": "standalone",
            "background_color": "#ffffff",
            "theme_color": "#0b5cad",
            "icons": [
                {"src": "/static/icon-192.png", "sizes": "192x192",
                 "type": "image/png", "purpose": "any"},
                {"src": "/static/icon-512.png", "sizes": "512x512",
                 "type": "image/png", "purpose": "any"},
                {"src": "/static/icon.svg", "sizes": "any",
                 "type": "image/svg+xml", "purpose": "any"},
            ],
        }, media_type="application/manifest+json")

    def settings_page(request: Request, flt: Filter, send_time: str,
                      error: str | None = None, status_code: int = 200):
        itineraries = list(store.itineraries().values())
        tz = ZoneInfo(cfg.tz)
        runs = [{"at": r.run_at.astimezone(tz).strftime("%d/%m/%Y %H:%M"),
                 "label": _RUN_LABELS.get(r.status, r.status),
                 "count": r.entry_count, "bad": r.status in _BAD_DIGEST}
                for r in store.recent_digest_runs(10)]
        ctx = {
            "search": flt,
            "send_time": send_time,
            "error": error,
            "test_result": request.query_params.get("prova"),
            "recipient": cfg.digest_to,
            "runs": runs,
            "areas": sorted({a for i in itineraries for a in i.areas}),
            "formulas": sorted({i.formula for i in itineraries if i.formula}),
            "countries": sorted(
                ((c, country_name_it(c)) for c in {c for i in itineraries
                                                    for c in i.countries}),
                key=lambda t: t[1]),
        }
        return templates.TemplateResponse(request, "settings.html", ctx,
                                          status_code=status_code)

    @app.get("/settings")
    def settings(request: Request):
        return settings_page(request, store.digest_filter(), store.send_time())

    @app.post("/settings")
    async def save_settings(request: Request):
        form = await request.form()
        flt = Filter.from_query(form.multi_items())
        send_time = str(form.get("send_time", "")).strip()
        if not _SEND_TIME.fullmatch(send_time):
            return settings_page(request, flt, send_time,
                                 error="Orario non valido: usa il formato HH:MM.",
                                 status_code=400)
        store.set_digest_filter(flt)
        store.set_send_time(send_time)
        app.state.on_send_time_change(send_time)
        return RedirectResponse("/settings", status_code=303)

    @app.post("/settings/from-search")
    async def settings_from_search(request: Request):
        form = await request.form()
        pairs = parse_qsl(str(form.get("query", "")))
        store.set_digest_filter(Filter.from_query(pairs))
        return RedirectResponse("/settings", status_code=303)

    def pending_digest() -> tuple[str, str, str, int]:
        entries = digest.compute_new_entries(store, digest.today_in(cfg.tz, now()))
        subject, html, text = render_digest(
            entries, store.digest_filter(), cfg.public_url,
            digest.today_in(cfg.tz, now()))
        return subject, html, text, len(entries)

    @app.get("/digest/preview")
    def digest_preview(request: Request):
        subject, html, _text, count = pending_digest()
        if count == 0:
            return templates.TemplateResponse(request, "preview_empty.html", {})
        return HTMLResponse(html)

    @app.post("/digest/test")
    def digest_test():
        subject, html, text, _count = pending_digest()
        try:
            mailer.send(cfg.digest_to, "[PROVA] " + subject, "[PROVA]\n\n" + text, html)
        except Exception:
            log.exception("test digest send failed")
            return RedirectResponse("/settings?prova=errore", status_code=303)
        return RedirectResponse("/settings?prova=ok", status_code=303)

    @app.get("/health")
    def health():
        snap = store.latest_ok_snapshot()
        return {"ok": True,
                "last_ok_snapshot": snap.taken_at.isoformat() if snap else None}

    return app


def build_app() -> FastAPI:
    return create_app(load_config())
