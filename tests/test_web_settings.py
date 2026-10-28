import re

import pytest
from fastapi.testclient import TestClient

from amcrawler.models import Filter
from amcrawler.web import create_app
from tests.conftest import NOW, FakeMailer, TODAY  # noqa: F401


@pytest.fixture
def mailer():
    return FakeMailer()


@pytest.fixture
def hook_calls():
    return []


@pytest.fixture
def client(cfg_dev, seeded_store, mailer, hook_calls):
    app = create_app(cfg_dev, store=seeded_store, mailer=mailer,
                     start_scheduler=False, now=lambda: NOW)
    app.state.on_send_time_change = hook_calls.append
    return TestClient(app, follow_redirects=False)


def test_settings_page_shows_filter_and_runs(client, seeded_store):
    seeded_store.set_digest_filter(Filter(formulas=("Discovery",), min_participants=4))
    r = client.get("/settings")
    assert r.status_code == 200
    assert 'name="send_time"' in r.text and 'type="time"' in r.text
    assert 'value="07:00"' in r.text
    assert "owner@example.com" in r.text
    assert "Anteprima" in r.text and "Invia prova" in r.text
    assert "Riferimento iniziale" in r.text  # Italian label for the baseline run
    assert 'value="4"' in r.text  # pmin
    assert 'value="Discovery" checked' in r.text


def test_settings_save_filter_and_time(client, seeded_store, hook_calls):
    r = client.post("/settings", data={
        "area": "Africa", "formula": "Adventure", "dfrom": "10", "dmin": "7",
        "pmin": "3", "send_time": "06:30"})
    assert r.status_code == 303
    assert r.headers["location"] == "/settings"
    assert seeded_store.send_time() == "06:30"
    assert seeded_store.digest_filter() == Filter(
        areas=("Africa",), formulas=("Adventure",), days_from=10, duration_min=7,
        min_participants=3)
    assert hook_calls == ["06:30"]


@pytest.mark.parametrize("bad", ["7", "25:00", "", "6:30", "07:60", "０６:３０"])
def test_settings_rejects_bad_send_time(client, seeded_store, hook_calls, bad):
    r = client.post("/settings", data={"formula": "Discovery", "send_time": bad})
    assert r.status_code == 400
    assert "Orario non valido" in r.text
    assert seeded_store.send_time() == "07:00"
    assert seeded_store.digest_filter() == Filter()
    assert hook_calls == []


def test_settings_from_search(client, seeded_store):
    r = client.post("/settings/from-search", data={"query": "formula=Discovery&pmin=4"})
    assert r.status_code == 303
    assert r.headers["location"] == "/settings"
    assert seeded_store.digest_filter() == Filter(formulas=("Discovery",),
                                                  min_participants=4)


def test_index_offers_use_for_digest(client):
    r = client.get("/?formula=Discovery")
    assert 'action="/settings/from-search"' in r.text
    assert 'name="query" value="formula=Discovery"' in r.text


def test_digest_preview_renders(client):
    r = client.get("/digest/preview")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Kenya Safari" in r.text  # d3 is a New Entry


def test_digest_preview_empty(client, seeded_store):
    seeded_store.set_digest_filter(Filter(countries=("IT",)))
    r = client.get("/digest/preview")
    assert r.status_code == 200
    assert "Nessuna nuova partenza" in r.text


def test_digest_test_send(client, seeded_store, mailer):
    runs_before = seeded_store.recent_digest_runs(50)
    r = client.post("/digest/test")
    assert r.status_code == 303
    assert len(mailer.sent) == 1
    to, subject, text, html = mailer.sent[0]
    assert to == "owner@example.com"
    assert subject.startswith("[PROVA] ")
    assert "Kenya Safari" in html
    assert seeded_store.recent_digest_runs(50) == runs_before


def test_digest_test_send_failure_is_reported(cfg_dev, seeded_store):
    app = create_app(cfg_dev, store=seeded_store, mailer=FakeMailer(fail=True),
                     start_scheduler=False, now=lambda: NOW)
    c = TestClient(app, follow_redirects=True)
    r = c.post("/digest/test")
    assert r.status_code == 200
    assert "Invio non riuscito" in r.text


def test_manifest(client):
    r = client.get("/manifest.webmanifest")
    assert r.status_code == 200
    assert "manifest+json" in r.headers["content-type"]
    m = r.json()
    assert m["display"] == "standalone"
    assert m["name"] == "AMCrawler"
    sizes = {i["sizes"] for i in m["icons"] if i["type"] == "image/png"}
    assert sizes == {"192x192", "512x512"}
    for i in m["icons"]:
        assert client.get(i["src"]).status_code == 200


def test_manifest_linked_with_credentials(client):
    r = client.get("/")
    assert 'rel="manifest"' in r.text
    assert 'crossorigin="use-credentials"' in r.text


def test_manifest_route_registered_once(client):
    paths = [r.path for r in client.app.routes if r.path == "/manifest.webmanifest"]
    assert paths == ["/manifest.webmanifest"]


def test_vendor_and_map_assets_served(client):
    for p in ("map.js", "vendor/d3.v7.9.0.min.js", "vendor/topojson-client.3.1.0.min.js",
              "vendor/countries-50m.json"):
        assert client.get("/static/" + p).status_code == 200


def test_digest_test_render_error_redirects_not_500(client, monkeypatch):
    def boom(*a, **k):
        raise ValueError("render broke")

    monkeypatch.setattr("amcrawler.web.render_digest", boom)
    r = client.post("/digest/test")
    assert r.status_code == 303
    assert r.headers["location"] == "/settings?prova=errore"


def test_settings_keeps_full_country_list_collapsed(client, seeded_store):
    from tests.conftest import itin

    seeded_store.save_catalogue([itin(id="i9", code="0009", countries=("NO",))], NOW)
    html = client.get("/settings").text
    assert 'value="NO"' in html  # no current Departure there, still selectable
    assert "<details" in html and "<details open" not in html


def test_settings_tools_come_before_filter_form(client):
    html = client.get("/settings").text
    for marker in ('name="send_time"', "Anteprima", "Invia prova"):
        assert html.index(marker) < html.index("Filtro del Digest")


def test_digest_test_without_recipient_explains(cfg_dev, seeded_store):
    import dataclasses

    mailer = FakeMailer(to="")
    cfg = dataclasses.replace(cfg_dev, digest_to="")
    c = TestClient(create_app(cfg, store=seeded_store, mailer=mailer,
                              start_scheduler=False, now=lambda: NOW),
                   follow_redirects=True)
    r = c.post("/digest/test")
    assert r.status_code == 200 and "DIGEST_TO non impostato" in r.text
    assert mailer.sent == []


def test_settings_keeps_saved_values_missing_from_catalogue(client, seeded_store):
    seeded_store.set_digest_filter(Filter(countries=("NO",), areas=("Nord Europa",),
                                          formulas=("Nuova",)))
    html = client.get("/settings").text
    assert re.search(r'name="country" value="NO" checked', html)
    assert re.search(r'name="area" value="Nord Europa" checked', html)
    assert re.search(r'name="formula" value="Nuova" checked', html)


def test_digest_preview_render_error_shows_italian_message(client, monkeypatch):
    def boom(*a, **k):
        raise ValueError("render broke")

    monkeypatch.setattr("amcrawler.web.render_digest", boom)
    r = client.get("/digest/preview")
    assert r.status_code == 200
    assert "Impossibile generare l'anteprima" in r.text


def test_digest_test_smtp_not_configured_is_reported(client, mailer, monkeypatch):
    from amcrawler.mailer import MailerNotConfigured

    def not_configured(*a, **k):
        raise MailerNotConfigured("SMTP non configurato")

    monkeypatch.setattr(mailer, "send", not_configured)
    r = client.post("/digest/test")
    assert r.status_code == 303
    assert r.headers["location"] == "/settings?prova=smtp"
