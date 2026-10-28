import json
import re

from fastapi.testclient import TestClient

from amcrawler.source import itinerary_url
from amcrawler.web import create_app
from tests.conftest import NOW, FakeMailer


def _map_data(html):
    m = re.search(r'<script type="application/json" id="map-data">(.*?)</script>', html, re.S)
    return json.loads(m.group(1))


def test_index_renders_cards(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Thailandia Classica" in r.text
    assert "Giappone Discovery" in r.text
    assert "8/15 iscritti" in r.text
    assert f'href="{itinerary_url("0001")}"' in r.text
    assert "Vedi viaggio" in r.text
    assert '<html lang="it">' in r.text
    assert 'id="map"' in r.text


def test_index_search_filters(client):
    r = client.get("/?formula=Discovery")
    assert "Giappone Discovery" in r.text
    assert "Thailandia Classica" not in r.text


def test_index_ignores_bad_query_values(client):
    r = client.get("/?dfrom=abc&pmin=-3&formula=")
    assert r.status_code == 200
    for name in ("Thailandia Classica", "Giappone Discovery", "Kenya Safari"):
        assert name in r.text


def test_new_badge_shown(client):
    r = client.get("/")
    assert r.text.count("NUOVA") == 1
    card = r.text.split('data-id="d3"', 1)[1].split("</article>", 1)[0]
    assert "NUOVA" in card


def test_status_strip(client):
    r = client.get("/")
    assert "Aggiornato alle 10:00 · 3 partenze · 1 nuova" in r.text
    assert "status-bad" not in r.text


def test_status_strip_red_when_latest_snapshot_failed(client, seeded_store):
    seeded_store.record_snapshot(NOW, None, "boom")
    text = client.get("/").text
    assert "status-bad" in text
    assert "Aggiornato alle 10:00" in text  # last OK crawl, not the failed one at 12:00
    assert "12:00" not in text.split('class="status', 1)[1].split("</p>", 1)[0]
    assert "ultimo aggiornamento non riuscito" in text


def test_status_strip_without_any_ok_snapshot(cfg_dev, store):
    store.record_snapshot(NOW, None, "boom")
    c = TestClient(create_app(cfg_dev, store=store, mailer=FakeMailer(),
                              start_scheduler=False, now=lambda: NOW))
    text = c.get("/").text
    assert "Nessun aggiornamento riuscito" in text
    assert "status-bad" in text
    assert "Aggiornato alle" not in text


def test_hide_and_unhide(client):
    assert client.post("/hide/d1").json() == {"ok": True}
    assert "Thailandia Classica" not in client.get("/").text
    hidden = client.get("/?nascoste=1").text
    assert "Thailandia Classica" in hidden
    assert "Giappone Discovery" not in hidden
    assert "Ripristina" in hidden
    assert client.post("/unhide/d1").json() == {"ok": True}
    assert "Thailandia Classica" in client.get("/").text


def test_hide_unknown_404(client):
    r = client.post("/hide/nope")
    assert r.status_code == 404
    assert r.json()["ok"] is False


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["last_ok_snapshot"].startswith("2026-10-28T10:00")


def test_health_without_snapshot(cfg_dev, store):
    c = TestClient(create_app(cfg_dev, store=store, mailer=FakeMailer(),
                              start_scheduler=False, now=lambda: NOW))
    assert c.get("/health").json() == {"ok": True, "last_ok_snapshot": None}
    assert c.get("/").status_code == 200


def test_map_data_embedded(client):
    data = _map_data(client.get("/").text)
    assert data["numeric"]["JP"] == "392"
    assert data["names"]["JP"] == "Giappone"
    by_id = {d["id"]: d for d in data["departures"]}
    assert by_id["d2"] == {"id": "d2", "name": "Giappone Discovery", "countries": ["JP"],
                         "new": False}
    assert by_id["d3"]["new"] is True


def test_filter_options_and_chips(client):
    r = client.get("/?country=JP")
    assert 'value="Estremo Oriente"' in r.text
    assert 'value="Discovery"' in r.text
    assert 'value="KE"' in r.text
    assert "Paese: Giappone" in r.text
    assert 'href="/"' in r.text  # the chip removes itself


def test_static_and_manifest(client):
    assert client.get("/static/app.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/manifest.webmanifest").json()["name"] == "AMCrawler"


def test_auth_enforced_when_not_dev(cfg_dev, seeded_store):
    import dataclasses

    cfg = dataclasses.replace(cfg_dev, dev_no_auth=False)
    c = TestClient(create_app(cfg, store=seeded_store, mailer=FakeMailer(),
                              start_scheduler=False, now=lambda: NOW))
    assert c.get("/").status_code == 403
    assert c.post("/hide/d1").status_code == 403
    assert c.get("/health").status_code == 200


def test_hostile_text_is_escaped(cfg_dev, store):
    from tests.conftest import dep, itin

    evil = "</script><img src=x onerror=alert(1)>"
    store.save_catalogue([itin()], NOW)
    store.record_snapshot(NOW, [dep(id="x1", name=evil, notes=evil)])
    c = TestClient(create_app(cfg_dev, store=store, mailer=FakeMailer(),
                              start_scheduler=False, now=lambda: NOW))
    html = c.get("/").text
    assert evil not in html
    assert "&lt;/script&gt;&lt;img src=x onerror=alert(1)&gt;" in html.split(
        'data-id="x1"', 1)[1].split("</article>", 1)[0]
    data = _map_data(html)
    assert data["departures"][0]["name"] == evil
