"""FastAPI app: main view, hide/unhide, health, auth wiring."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import digest, filters
from .auth import AccessVerifier, install_auth
from .config import Config, load_config
from .geo import country_name_it, iso_numeric
from .mailer import SmtpMailer, date_range, truncate_notes
from .models import Departure, Filter
from .source import itinerary_url
from .store import Store

_PKG = Path(__file__).parent
_BAD_DIGEST = ("failed", "stale")

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
        runs = store.recent_digest_runs(1)
        digest_bad = bool(runs) and runs[0].status in _BAD_DIGEST
        bad = (latest is not None and not latest.ok) or digest_bad
        if latest is None:
            return {"text": "Nessun aggiornamento ancora", "bad": bad}
        at = latest.taken_at.astimezone(ZoneInfo(cfg.tz)).strftime("%H:%M")
        text = (f"Aggiornato alle {at} · {_plural(shown, 'partenza', 'partenze')}"
                f" · {_plural(new, 'nuova', 'nuove')}")
        if not latest.ok:
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
            "departures": [{"id": d.id, "countries": list(d.countries),
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
            "icons": [{"src": "/static/icon.svg", "sizes": "any",
                       "type": "image/svg+xml", "purpose": "any"}],
        }, media_type="application/manifest+json")

    @app.get("/health")
    def health():
        snap = store.latest_ok_snapshot()
        return {"ok": True,
                "last_ok_snapshot": snap.taken_at.isoformat() if snap else None}

    return app


def build_app() -> FastAPI:
    return create_app(load_config())
