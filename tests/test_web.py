import json
import re
from datetime import timedelta

from amcrawler.source import itinerary_url
from tests.conftest import NOW, dep, itin


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


def test_status_strip_without_any_ok_snapshot(app_client, store):
    store.record_snapshot(NOW, None, "boom")
    c = app_client(store=store)
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


def test_health_without_snapshot(app_client, store):
    c = app_client(store=store)
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


def test_auth_enforced_when_not_dev(app_client, cfg_auth):
    c = app_client(cfg_auth)
    assert c.get("/").status_code == 403
    assert c.post("/hide/d1").status_code == 403
    assert c.get("/health").status_code == 200


def test_hostile_text_is_escaped(app_client, store):
    evil = "</script><img src=x onerror=alert(1)>"
    store.save_catalogue([itin()], NOW)
    store.record_snapshot(NOW, [dep(id="x1", name=evil, notes=evil)])
    c = app_client(store=store)
    html = c.get("/").text
    assert evil not in html
    assert "&lt;/script&gt;&lt;img src=x onerror=alert(1)&gt;" in html.split(
        'data-id="x1"', 1)[1].split("</article>", 1)[0]
    data = _map_data(html)
    assert data["departures"][0]["name"] == evil


def test_search_sheet_lists_only_current_countries(client, seeded_store):
    seeded_store.save_catalogue([
        itin(id="i1", code="0001", countries=("TH",)),
        itin(id="i2", code="0002", formula="Discovery", areas=("Estremo Oriente",),
             countries=("JP",)),
        itin(id="i3", code="0003", areas=("Africa",), countries=("KE",)),
        itin(id="i9", code="0009", areas=("Nord Europa",), countries=("NO",)),
    ], NOW)
    html = client.get("/").text
    assert 'name="country" value="JP"' in html
    assert 'name="country" value="NO"' not in html
    assert 'value="Nord Europa"' not in html


def test_filter_groups_are_details_open_when_selected(client):
    html = client.get("/?country=JP").text
    assert "<details" in html
    assert re.search(r'<details class="group" open>\s*<summary>Paese · 1 selezionato', html)
    assert re.search(r'<details class="group">\s*<summary>Area</summary>', html)


def test_head_health_and_index(app_client, cfg_auth):
    c = app_client(cfg_auth)
    assert c.head("/health").status_code == 200
    assert c.head("/").status_code == 403


def test_head_index_ok_in_dev(client):
    assert client.head("/").status_code == 200


def test_favicon_link(client):
    assert '<link rel="icon" href="/static/icon.svg" type="image/svg+xml">' in client.get("/").text
    assert client.get("/static/icon.svg").status_code == 200


def test_one_day_departure_card_and_chip_use_singular(app_client, store):
    store.save_catalogue([itin()], NOW)
    store.record_snapshot(NOW, [dep(id="x1", duration=1)])
    c = app_client(store=store)
    html = c.get("/?dmax=1").text
    card = html.split('data-id="x1"', 1)[1].split("</article>", 1)[0]
    assert "1 giorno ·" in card and "1 giorni" not in card
    assert "Durata massima 1 giorno" in html and "1 giorni" not in html


def test_status_strip_red_when_catalogue_older_than_72h(client, seeded_store):
    seeded_store.save_catalogue([itin()], NOW - timedelta(hours=73))
    text = client.get("/").text
    assert "status-bad" in text
    assert "catalogo non aggiornato" in text


def test_status_strip_ok_when_catalogue_within_72h(client, seeded_store):
    seeded_store.save_catalogue([itin()], NOW - timedelta(hours=71))
    text = client.get("/").text
    assert "status-bad" not in text
    assert "catalogo non aggiornato" not in text


def test_status_strip_shows_date_when_last_ok_is_not_today(app_client, store):
    store.save_catalogue([itin()], NOW)
    store.record_snapshot(NOW - timedelta(hours=26), [dep()])
    text = app_client(store=store).get("/").text
    assert "Aggiornato il 27/10 alle 10:00" in text
