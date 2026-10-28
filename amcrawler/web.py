"""FastAPI app: main view, hide/unhide, health, auth wiring."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qsl, urlencode

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import digest, filters
from .auth import AccessVerifier, install_auth
from .config import Config, load_config, parse_hhmm
from .geo import country_name_it, iso_numeric
from .labels import filter_parts, plural_it
from .mailer import MailerNotConfigured, SmtpMailer, render_current, render_digest
from .models import Filter
from .store import SnapshotInfo, Store
from .templating import env

_PKG = Path(__file__).parent
log = logging.getLogger(__name__)
CATALOGUE_STALE_AFTER = timedelta(hours=72)
WEEKDAYS_IT = ("lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica")

def _href(pairs: list[tuple[str, str]], hidden_view: bool) -> str:
    if hidden_view:
        pairs = pairs + [("nascoste", "1")]
    return "/?" + urlencode(pairs) if pairs else "/"


def _chips(f: Filter, hidden_view: bool, q: str = "") -> list[dict]:
    pairs = f.to_query()
    text = [("q", q)] if q else []
    chips = [
        {"label": label, "href": _href(pairs[:i] + pairs[i + 1:] + text, hidden_view)}
        for i, (_key, _value, label) in enumerate(filter_parts(f))
    ]
    if q:
        chips.append({"label": f"“{q}”", "href": _href(pairs, hidden_view)})
    return chips


def _int_or_zero(raw: str | None) -> int:
    return int(raw) if raw and raw.isascii() and raw.isdigit() else 0


def _filter_options(areas, countries, formulas, selected: Filter) -> dict:
    """Options for the three checkbox groups; selected values are always listed."""
    return {
        "areas": [(v, v) for v in sorted(set(areas) | set(selected.areas))],
        "countries": sorted(((c, country_name_it(c))
                             for c in set(countries) | set(selected.countries)),
                            key=lambda t: t[1]),
        "formulas": [(v, v) for v in sorted(set(formulas) | set(selected.formulas))],
    }


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
        mailer = SmtpMailer.from_config(cfg)

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
    app.state.on_send_time_change = lambda hhmm: None  # replaced when the scheduler starts

    verifier = None
    if cfg.cf_team_domain and cfg.cf_aud:
        verifier = AccessVerifier(cfg.cf_team_domain, cfg.cf_aud)
    install_auth(app, verifier, cfg.dev_no_auth)

    templates = Jinja2Templates(env=env)

    app.mount("/static", StaticFiles(directory=str(_PKG / "static")), name="static")

    def status_strip(last_ok: SnapshotInfo | None, shown: int, new: int) -> dict:
        latest = store.latest_snapshot()
        runs = store.recent_digest_runs(1)
        digest_bad = bool(runs) and runs[0].status in digest.ERROR_STATUSES
        crawl_bad = latest is not None and not latest.ok
        catalogue_at = store.catalogue_fetched_at()
        catalogue_bad = (last_ok is not None and catalogue_at is not None
                         and now() - catalogue_at > CATALOGUE_STALE_AFTER)
        bad = crawl_bad or digest_bad or catalogue_bad
        if last_ok is None:
            parts = ["Nessun aggiornamento riuscito"]
        else:
            taken = digest.to_local(last_ok.taken_at, cfg.tz)
            when = taken.strftime("alle %H:%M")
            if taken.date() != digest.today_in(cfg.tz, now()):
                when = taken.strftime("il %d/%m alle %H:%M")
            parts = [f"Aggiornato {when}", plural_it(shown, "partenza", "partenze"),
                     plural_it(new, "nuova", "nuove")]
        if crawl_bad:
            parts.append("ultimo aggiornamento non riuscito")
        elif digest_bad:
            parts.append("ultimo Digest non riuscito")
        if catalogue_bad:
            parts.append("catalogo non aggiornato")
        text = " · ".join(parts)
        return {"text": text, "bad": bad}

    @app.api_route("/", methods=["GET", "HEAD"])
    def index(request: Request):
        today = digest.today_in(cfg.tz, now())
        search = Filter.from_query(request.query_params.multi_items())
        q = request.query_params.get("q", "").strip()
        hidden_view = request.query_params.get("nascoste") == "1"
        hidden = store.hidden_ids()
        last_ok = store.latest_ok_snapshot()
        current = store.snapshot_departures(last_ok.id) if last_ok else []
        pool = [d for d in current if (d.id in hidden) == hidden_view]
        results = sorted(
            (d for d in filters.apply(pool, search, today)
             if filters.text_matches(d, q, country_name_it)),
            key=lambda d: (d.start_date, d.name, d.id))
        badged = digest.badged_ids(store, today, current)
        new_count = sum(1 for d in results if d.id in badged)

        map_codes = sorted({c for d in results for c in d.countries})
        map_data = {
            "departures": [{"id": d.id, "name": d.name, "countries": list(d.countries),
                            "new": d.id in badged} for d in results],
            "names": {c: country_name_it(c) for c in map_codes},
            "numeric": {c: iso_numeric(c) for c in map_codes},
        }
        text_pair = [("q", q)] if q else []
        ctx = {
            "results": results,
            "badged": badged,
            "hidden_view": hidden_view,
            "search": search,
            "q": q,
            "today": today,
            "chips": _chips(search, hidden_view, q),
            "status": status_strip(last_ok, len(results), new_count),
            **_filter_options({a for d in current for a in d.areas},
                              {c for d in current for c in d.countries},
                              store.formulas(), search),
            "map_data": map_data,
            "list_href": _href(search.to_query() + text_pair, False),
            "hidden_href": _href(search.to_query() + text_pair, True),
            "search_query": urlencode(search.to_query()),
        }
        return templates.TemplateResponse(request, "index.html", ctx)

    @app.post("/hide/{departure_id}")
    def hide(departure_id: str):
        match = next((d for d in store.latest_ok_departures() if d.id == departure_id), None)
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
                      error: str | None = None, status_code: int = 200,
                      enabled: bool | None = None, weekdays: list[int] | None = None,
                      quiet: bool | None = None):
        itineraries = list(store.itineraries().values())
        runs = [{"at": digest.to_local(r.run_at, cfg.tz).strftime("%d/%m/%Y %H:%M"),
                 "label": digest.STATUS_LABELS_IT.get(r.status, r.status),
                 "count": r.entry_count, "bad": r.status in digest.ERROR_STATUSES}
                for r in store.recent_digest_runs(10)]
        ctx = {
            "search": flt,
            "send_time": send_time,
            "error": error,
            "test_result": request.query_params.get("prova"),
            "list_result": request.query_params.get("elenco"),
            "list_count": _int_or_zero(request.query_params.get("n")),
            "digest_enabled": store.digest_enabled() if enabled is None else enabled,
            "digest_weekdays": store.digest_weekdays() if weekdays is None else weekdays,
            "digest_quiet_mail": store.digest_quiet_mail() if quiet is None else quiet,
            "weekdays_it": WEEKDAYS_IT,
            "recipient": cfg.digest_to,
            "runs": runs,
            **_filter_options({a for i in itineraries for a in i.areas},
                              {c for i in itineraries for c in i.countries},
                              store.formulas(), flt),
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
        enabled = form.get("digest_enabled") is not None
        quiet = form.get("digest_quiet_mail") is not None
        weekdays = sorted({int(v) for v in form.getlist("weekday")
                           if v.isascii() and v.isdigit() and int(v) < 7})
        chosen = dict(enabled=enabled, weekdays=weekdays, quiet=quiet)
        try:
            parse_hhmm(send_time)
        except ValueError:
            return settings_page(request, flt, send_time,
                                 error="Orario non valido: usa il formato HH:MM.",
                                 status_code=400, **chosen)
        if enabled and not weekdays:
            return settings_page(request, flt, send_time,
                                 error="Scegli almeno un giorno di invio.",
                                 status_code=400, **chosen)
        store.set_digest_filter(flt)
        store.set_send_time(send_time)
        store.set_digest_enabled(enabled)
        store.set_digest_weekdays(weekdays)
        store.set_digest_quiet_mail(quiet)
        app.state.on_send_time_change(send_time)
        return RedirectResponse("/settings", status_code=303)

    @app.post("/settings/from-search")
    async def settings_from_search(request: Request):
        form = await request.form()
        pairs = parse_qsl(str(form.get("query", "")))
        store.set_digest_filter(Filter.from_query(pairs))
        return RedirectResponse("/settings", status_code=303)

    def render_pending() -> tuple[str, str, str, int]:
        today = digest.today_in(cfg.tz, now())
        entries = digest.compute_new_entries(store, today)
        subject, html, text = render_digest(entries, store.digest_filter(),
                                            cfg.public_url, today)
        return subject, html, text, len(entries)

    @app.get("/digest/preview")
    def digest_preview(request: Request):
        try:
            _subject, html, _text, count = render_pending()
        except Exception:
            log.exception("digest preview failed")
            return templates.TemplateResponse(request, "preview_error.html", {})
        if count == 0:
            return templates.TemplateResponse(request, "preview_empty.html", {})
        return HTMLResponse(html)

    @app.post("/digest/test")
    def digest_test():
        try:
            subject, html, text, _count = render_pending()
            mailer.send("[PROVA] " + subject, "[PROVA]\n\n" + text, html)
        except MailerNotConfigured as exc:
            return RedirectResponse(
                "/settings?prova=" + ("destinatario" if exc.recipient_missing else "smtp"),
                status_code=303)
        except Exception:
            log.exception("test digest failed")
            return RedirectResponse("/settings?prova=errore", status_code=303)
        return RedirectResponse("/settings?prova=ok", status_code=303)

    @app.post("/digest/current")
    def digest_current():
        today = digest.today_in(cfg.tz, now())
        hidden = store.hidden_ids()
        entries = sorted(
            (d for d in filters.apply(store.latest_ok_departures(), store.digest_filter(),
                                      today) if d.id not in hidden),
            key=lambda d: (d.start_date, d.name, d.id))
        if not entries:
            return RedirectResponse("/settings?elenco=vuoto", status_code=303)
        try:
            subject, html, text = render_current(entries, store.digest_filter(),
                                                 cfg.public_url)
            mailer.send(subject, text, html)
        except MailerNotConfigured as exc:
            return RedirectResponse(
                "/settings?elenco=" + ("destinatario" if exc.recipient_missing else "smtp"),
                status_code=303)
        except Exception:
            log.exception("current list mail failed")
            return RedirectResponse("/settings?elenco=errore", status_code=303)
        return RedirectResponse(f"/settings?elenco=ok&n={len(entries)}", status_code=303)

    @app.api_route("/health", methods=["GET", "HEAD"])
    def health():
        snap = store.latest_ok_snapshot()
        return {"ok": True,
                "last_ok_snapshot": snap.taken_at.isoformat() if snap else None}

    return app


def build_app() -> FastAPI:
    # uvicorn only configures its own loggers: make crawl/digest INFO lines reach the logs
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return create_app(load_config())
