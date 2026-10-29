import json
import re
from datetime import timedelta

from amcrawler.source import itinerary_url
from tests.conftest import NOW, TODAY, dep, itin


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


def test_static_urls_carry_content_hash(client):
    # a deploy must not be hidden behind a cached old stylesheet or script
    html = client.get("/").text
    for name in ("app.css", "app.js", "map.js", "calendar.js"):
        m = re.search(r'/static/' + re.escape(name) + r'\?v=([0-9a-f]{10})"', html)
        assert m, name
        assert client.get(f"/static/{name}?v={m.group(1)}").status_code == 200


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
    assert re.search(r'<link rel="icon" href="/static/icon\.svg\?v=[0-9a-f]{10}" type="image/svg\+xml">', client.get("/").text)
    assert client.get("/static/icon.svg").status_code == 200


def test_one_day_departure_card_and_chip_use_singular(app_client, store):
    store.save_catalogue([itin()], NOW)
    store.record_snapshot(NOW, [dep(id="x1", duration=1)])
    c = app_client(store=store)
    html = c.get("/?dmax=1").text
    card = html.split('data-id="x1"', 1)[1].split("</article>", 1)[0]
    assert ">1 giorno<" in card and "1 giorni" not in card
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


def test_ui_uses_one_term_for_the_digest(client):
    text = client.get("/").text
    assert "Usa per il Digest" in text
    assert "riepilogo" not in text.lower()


def test_build_app_configures_logging(monkeypatch):
    import logging

    from amcrawler import web

    calls = []
    monkeypatch.setattr(logging, "basicConfig", lambda **kw: calls.append(kw))
    monkeypatch.setattr(web, "load_config", lambda: "cfg")
    monkeypatch.setattr(web, "create_app", lambda cfg: ("app", cfg))
    assert web.build_app() == ("app", "cfg")
    assert calls and calls[0]["level"] == logging.INFO


# --- search box ---------------------------------------------------------------------

def test_search_matches_name_accents_and_case(client):
    text = client.get("/?q=KENYA").text
    assert "Kenya Safari" in text and "Thailandia Classica" not in text
    text = client.get("/?q=thailandia+classica").text
    assert "Thailandia Classica" in text and "Giappone Discovery" not in text


def test_search_matches_country_area_formula_and_code(client):
    assert "Giappone Discovery" in client.get("/?q=giappone").text
    assert "Kenya Safari" in client.get("/?q=africa").text
    assert "Thailandia Classica" not in client.get("/?q=africa").text
    assert "Giappone Discovery" in client.get("/?q=discovery").text
    assert "Giappone Discovery" not in client.get("/?q=adventure").text
    assert "Kenya Safari" in client.get("/?q=2026-0003").text
    assert "Kenya Safari" not in client.get("/?q=2026-0002").text


def test_search_matches_the_italian_country_name(client):
    text = client.get("/?q=Kenya").text
    assert "Kenya Safari" in text
    assert "Giappone Discovery" in client.get("/?q=giappone").text


def test_search_terms_are_anded_and_combine_with_filters(client):
    assert "Kenya Safari" in client.get("/?q=kenya+africa").text
    assert "Kenya Safari" not in client.get("/?q=kenya+asia").text
    text = client.get("/?q=safari&formula=Discovery").text
    assert "Kenya Safari" not in text


def test_search_applies_to_counts_map_and_status(client):
    r = client.get("/?q=kenya")
    assert "1 partenza · 1 nuova" in r.text
    assert [d["name"] for d in _map_data(r.text)["departures"]] == ["Kenya Safari"]


def test_search_applies_in_the_hidden_view(client, seeded_store):
    seeded_store.hide("d1", "2026-0001", NOW)
    text = client.get("/?nascoste=1&q=thailandia").text
    assert "Thailandia Classica" in text
    assert "Thailandia Classica" not in client.get("/?nascoste=1&q=kenya").text


def test_search_form_keeps_filters_and_hidden_view(client):
    html = client.get("/?q=kenya&formula=Adventure&nascoste=1").text
    form = html.split('role="search"', 1)[1].split("</form>", 1)[0]
    assert 'action="/"' in form and 'method="get"' in form
    assert 'type="search"' in form and 'name="q"' in form and 'value="kenya"' in form
    assert 'placeholder="Cerca viaggio, paese, codice…"' in form
    assert 'aria-label="Cerca"' in form
    assert 'name="formula" value="Adventure"' in form
    assert 'name="nascoste" value="1"' in form
    assert html.index('role="search"') < html.index('role="status"')


def test_search_form_is_prefilled_and_escaped(client):
    html = client.get('/?q="><b>').text
    assert 'value="&#34;&gt;&lt;b&gt;"' in html
    assert '"><b>' not in html


def test_search_text_chip_is_removable_and_keeps_other_filters(client):
    html = client.get("/?q=kenya&formula=Adventure").text
    chips = html.split('id="chips"', 1)[1].split("</ul>", 1)[0]
    assert "“kenya”" in chips
    removing_q = re.search(r'<a class="chip" href="([^"]*)"[^>]*>Formula', chips).group(1)
    assert "q=kenya" in removing_q and "formula" not in removing_q
    removing_formula = re.search(r'href="([^"]*)"[^>]*>“kenya”', chips).group(1)
    assert removing_formula == "/?formula=Adventure"


def test_search_text_never_reaches_the_digest(client):
    html = client.get("/?q=kenya&formula=Adventure").text
    assert 'name="query" value="formula=Adventure"' in html
    sheet = html.split('id="filters"', 1)[1]
    assert 'name="q" value="kenya"' in sheet  # the sheet keeps the text for Cerca
    assert "Azzera" in sheet


def test_tabs_keep_the_search_text(client):
    html = client.get("/?q=kenya").text
    assert 'href="/?q=kenya&amp;nascoste=1"' in html


# --- card --------------------------------------------------------------------------

def _card(html, dep_id):
    return html.split(f'data-id="{dep_id}"', 1)[1].split("</article>", 1)[0]


def test_card_has_start_date_hook_and_date_strip(client):
    html = client.get("/").text
    card = _card(html, "d1")
    # TODAY (28 Oct) + 30 days = 27 Nov, a Friday; 12 days
    assert 'data-start="2026-11-27"' in html
    assert ">VEN<" in card and ">27 nov<" in card
    assert ">12 giorni<" in card and ">MAR<" in card and ">8 dic<" in card


def test_card_where_line_and_stats(client):
    card = _card(client.get("/").text, "d1")
    assert "Thailandia · Adventure" in card
    assert "8/15 iscritti" in card
    assert "tra 30 giorni" in card and "Parte tra" not in card


def test_card_countdown_soon_pill_has_text(client, seeded_store):
    seeded_store.record_snapshot(NOW, [dep(id="d9", start_date=TODAY + timedelta(days=3))])
    card = _card(client.get("/").text, "d9")
    assert 'class="soon"' in card and "Parte tra 3 giorni" in card


def test_card_seat_blocks_and_colours(client, seeded_store):
    seeded_store.record_snapshot(NOW, [
        dep(id="d8", participants=6, max_participants=15),
        dep(id="d9", participants=1, max_participants=14)])
    html = client.get("/").text
    many = _card(html, "d8")
    assert many.count('<i class="on">') == 6 and many.count("<i") == 15
    assert 'role="img" aria-label="6 iscritti su 15 posti"' in many
    assert "ticks one" not in many
    one = _card(html, "d9")
    assert 'class="ticks one"' in one and "1 iscritto" in one
    assert 'aria-label="1 iscritto su 14 posti"' in one


def test_card_without_known_capacity_shows_a_bar(client, seeded_store):
    seeded_store.record_snapshot(NOW, [
        dep(id="d8", participants=4, max_participants=0),
        dep(id="d9", participants=4, max_participants=40)])
    html = client.get("/").text
    for dep_id in ("d8", "d9"):
        card = _card(html, dep_id)
        assert "<i" not in card and 'class="meter' in card


def test_card_has_no_flags_and_keeps_action_hooks(client):
    card = _card(client.get("/").text, "d1")
    assert not re.search("[\U0001F1E6-\U0001F1FF]", card)
    assert 'data-action="hide" data-id="d1"' in card
    assert "2026-0001" in card and ">Vedi<" in card
    assert 'role="button"' in card  # the notes


def test_hidden_view_card_offers_restore(client, seeded_store):
    seeded_store.hide("d1", "2026-0001", NOW)
    card = _card(client.get("/?nascoste=1").text, "d1")
    assert 'data-action="unhide" data-id="d1"' in card and "Ripristina" in card


def test_card_without_known_capacity_omits_the_slash(client, seeded_store):
    seeded_store.record_snapshot(NOW, [
        dep(id="d8", participants=3, max_participants=0),
        dep(id="d9", participants=1, max_participants=0)])
    html = client.get("/").text
    many, one = _card(html, "d8"), _card(html, "d9")
    assert ">3 iscritti<" in many and "3/0" not in many
    assert ">1 iscritto<" in one and "1/0" not in one
    assert 'aria-label="3 iscritti"' in many
