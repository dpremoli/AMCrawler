import re
from datetime import timedelta

import pytest

from tests.conftest import NOW, TODAY, dep


def _pins_block(html):
    """The Fissati block, or None."""
    m = re.search(r'<details class="pins".*?</details>', html, re.S)
    return m.group(0) if m else None


def _main_card(html, dep_id):
    """The card of `dep_id` in the main list (not the copy inside Fissati)."""
    rest = html.split('id="lista"', 1)[1]
    rest = re.sub(r'<details class="pins".*?</details>', "", rest, flags=re.S)
    return rest.split(f'data-id="{dep_id}"', 1)[1].split("</article>", 1)[0]


# ---- pin routes ----

def test_pin_and_unpin(client, seeded_store):
    assert client.post("/pin/d1").json() == {"ok": True}
    assert seeded_store.pinned_ids() == {"d1"}
    assert client.post("/pin/d1").json() == {"ok": True}  # idempotent
    assert client.post("/unpin/d1").json() == {"ok": True}
    assert seeded_store.pinned_ids() == set()


def test_pin_remembers_the_start_date(client, seeded_store):
    client.post("/pin/d1")
    assert seeded_store.delete_pins_before(TODAY + timedelta(days=31)) == 1


@pytest.mark.parametrize("path", ["/pin/nope", "/unpin/nope", "/alert/nope",
                                  "/alert/nope/delete"])
def test_unknown_departure_is_404(client, path):
    r = client.post(path, data={"threshold": "9"})
    assert r.status_code == 404 and r.json()["ok"] is False


def test_pin_is_404_for_a_hidden_departure(client, seeded_store):
    client.post("/hide/d1")
    assert client.post("/pin/d1").status_code == 404
    assert client.post("/alert/d1", data={"threshold": "9"}).status_code == 404


def test_pin_routes_are_post_only(client):
    assert client.get("/pin/d1").status_code == 405
    assert client.get("/alert/d1/delete").status_code == 405


def test_pin_routes_need_auth(app_client, cfg_auth):
    c = app_client(cfg_auth)
    assert c.post("/pin/d1").status_code == 403
    assert c.post("/alert/d1", data={"threshold": "9"}).status_code == 403


# ---- alert routes ----

def test_set_alert(client, seeded_store):
    assert client.post("/alert/d1", data={"threshold": "12"}).json() == {"ok": True}
    assert seeded_store.alert_thresholds() == {"d1": 12}
    assert seeded_store.alerts()[0].participants == 8  # d1 has 8 of 15 now


def test_setting_an_alert_again_replaces_the_threshold(client, seeded_store):
    client.post("/alert/d1", data={"threshold": "12"})
    client.post("/alert/d1", data={"threshold": "10"})
    assert seeded_store.alert_thresholds() == {"d1": 10}


@pytest.mark.parametrize("threshold, ok", [
    ("9", True),    # participants + 1
    ("15", True),   # the seat count
    ("8", False),   # not above the 8 Participants
    ("3", False),
    ("16", False),  # past the 15 seats
])
def test_alert_threshold_bounds(client, seeded_store, threshold, ok):
    r = client.post("/alert/d1", data={"threshold": threshold})
    assert (r.status_code == 200) is ok
    if not ok:
        body = r.json()
        assert r.status_code == 400 and body["ok"] is False and body["error"]
        assert seeded_store.alerts() == []


def test_alert_errors_are_in_italian(client):
    low = client.post("/alert/d1", data={"threshold": "8"}).json()["error"]
    high = client.post("/alert/d1", data={"threshold": "16"}).json()["error"]
    assert low == "Scegli un numero maggiore di 8 iscritti."
    assert high == "Il viaggio ha solo 15 posti."


@pytest.mark.parametrize("raw", ["", "abc", "-4", "9.5", "٩", " "])
def test_alert_threshold_must_be_an_integer(client, raw):
    r = client.post("/alert/d1", data={"threshold": raw})
    assert r.status_code == 400 and r.json()["error"] == "Numero di iscritti non valido."


def test_a_huge_threshold_is_a_400_not_a_500(client):
    r = client.post("/alert/d1", data={"threshold": "9" * 5000})
    assert r.status_code == 400 and r.json()["error"] == "Il viaggio ha solo 15 posti."


def test_alert_threshold_missing_is_400(client):
    assert client.post("/alert/d1").status_code == 400


def test_alert_without_a_known_seat_count_goes_up_to_99(client, seeded_store):
    seeded_store.record_snapshot(NOW, [dep(id="d8", participants=4, max_participants=0)])
    assert client.post("/alert/d8", data={"threshold": "99"}).status_code == 200
    r = client.post("/alert/d8", data={"threshold": "100"})
    assert r.status_code == 400 and r.json()["error"] == "Massimo 99 iscritti."


def test_alert_is_capped_at_99_even_with_more_seats(client, seeded_store):
    seeded_store.record_snapshot(NOW, [dep(id="d8", participants=4, max_participants=120)])
    assert client.post("/alert/d8", data={"threshold": "100"}).status_code == 400


def test_delete_alert(client, seeded_store):
    client.post("/alert/d1", data={"threshold": "12"})
    assert client.post("/alert/d1/delete").json() == {"ok": True}
    assert seeded_store.alerts() == []
    assert client.post("/alert/d1/delete").json() == {"ok": True}


def test_hide_removes_pin_and_alert(client, seeded_store):
    client.post("/pin/d1")
    client.post("/pin/d2")
    client.post("/alert/d1", data={"threshold": "12"})
    assert client.post("/hide/d1").json() == {"ok": True}
    assert seeded_store.pinned_ids() == {"d2"} and seeded_store.alerts() == []
    client.post("/unhide/d1")
    assert seeded_store.pinned_ids() == {"d2"} and seeded_store.alerts() == []


# ---- rendering ----

def test_no_fissati_block_without_pins(client):
    assert _pins_block(client.get("/").text) is None


def test_fissati_block_lists_pinned_by_start_date(client, seeded_store):
    client.post("/pin/d2")
    client.post("/pin/d1")
    block = _pins_block(client.get("/").text)
    assert block.startswith('<details class="pins" open>') and "Fissati <span class=\"count\">(2)</span>" in block
    assert block.index("Thailandia Classica") < block.index("Giappone Discovery")
    assert "Kenya Safari" not in block
    row = block.split('class="prow"', 1)[1]
    assert "ven 27 nov → mar 8 dic" in row  # d1: 30 days from 28 Oct 2026, 12 days
    assert ">8/15<" in row


def test_fissati_row_is_a_collapsed_button_with_the_card(client):
    client.post("/pin/d1")
    block = _pins_block(client.get("/").text)
    assert re.search(r'<button type="button" class="prow-btn" aria-expanded="false"', block)
    assert re.search(r'class="open-card"[^>]*hidden', block)
    assert 'class="card pinned"' in block


def test_fissati_follows_the_search(client):
    client.post("/pin/d1")
    assert _pins_block(client.get("/?formula=Adventure").text) is not None
    assert _pins_block(client.get("/?formula=Discovery").text) is None
    assert _pins_block(client.get("/?q=zzzz").text) is None
    assert "Fissati <span class=\"count\">(1)</span>" in _pins_block(client.get("/?q=thailandia").text)


def test_fissati_is_absent_in_the_hidden_view(client, seeded_store):
    client.post("/pin/d1")
    client.post("/hide/d2")
    html = client.get("/?nascoste=1").text
    assert _pins_block(html) is None
    assert 'data-action="pin"' not in html and 'data-action="bell"' not in html


def test_pinned_card_stays_in_the_main_list_for_the_calendar(client):
    client.post("/pin/d1")
    html = client.get("/").text
    assert 'class="card pinned" data-id="d1"' in _main_card_open(html)
    assert _pins_block(html).count('data-id="d1"') >= 1


def _main_card_open(html):
    rest = html.split('id="lista"', 1)[1]
    rest = re.sub(r'<details class="pins".*?</details>', "", rest, flags=re.S)
    return rest


def test_divider_only_with_fissati(client):
    assert "Tutte le partenze" not in client.get("/").text
    client.post("/pin/d1")
    assert "Tutte le partenze" in client.get("/").text


def test_pin_and_bell_buttons_reflect_their_state(client):
    off = _main_card(client.get("/").text, "d1")
    assert 'data-action="pin" data-id="d1" aria-pressed="false" aria-label="Aggiungi a Fissati"' in off
    assert 'aria-label="Imposta avviso"' in off and 'aria-pressed="false"' in off
    assert '#i-pin"' in off and '#i-bell"' in off
    client.post("/pin/d1")
    client.post("/alert/d1", data={"threshold": "12"})
    on = _main_card(client.get("/").text, "d1")
    assert 'data-action="unpin" data-id="d1" aria-pressed="true" aria-label="Togli da Fissati"' in on
    assert 'aria-label="Avviso a 12 iscritti"' in on
    assert '#i-pin-on"' in on and '#i-bell-on"' in on
    assert '<span class="n">12</span>' in on


def test_bell_button_carries_the_sheet_data(client):
    card = _main_card(client.get("/").text, "d1")
    tag = re.search(r'<button[^>]*data-action="bell"[^>]*>', card).group(0)
    for attr in ('data-id="d1"', 'data-name="Thailandia Classica"', 'data-participants="8"',
                 'data-max="15"', 'data-threshold="0"'):
        assert attr in tag


def test_alert_card_outlines_the_goal_block_and_says_so(client):
    client.post("/alert/d1", data={"threshold": "12"})
    card = _main_card(client.get("/").text, "d1")
    ticks = re.search(r'<div class="ticks.*?</div>', card, re.S).group(0)
    items = re.findall(r"<i[^>]*>", ticks)
    assert [i for i, t in enumerate(items) if "goal" in t] == [11]
    assert "Ti scrivo quando arriva a 12 iscritti." in card
    assert "avviso a 12" in ticks


def test_card_without_an_alert_has_no_goal_or_line(client):
    card = _main_card(client.get("/").text, "d1")
    assert "goal" not in card and "Ti scrivo" not in card and 'class="n"' not in card


@pytest.mark.parametrize("seats", [0, 40])
def test_bar_fallback_skips_the_goal_outline(client, seeded_store, seats):
    seeded_store.record_snapshot(NOW, [dep(id="d8", participants=4, max_participants=seats)])
    client.post("/alert/d8", data={"threshold": "9"})
    card = _main_card(client.get("/").text, "d8")
    assert "goal" not in card and 'class="meter' in card
    assert "Ti scrivo quando arriva a 9 iscritti." in card


def test_fissati_row_shows_the_bell_threshold(client):
    client.post("/pin/d1")
    assert 'class="bell-mini"></span>' in _pins_block(client.get("/").text)
    client.post("/alert/d1", data={"threshold": "12"})
    mini = re.search(r'<span class="bell-mini"[^>]*>.*?</span>',
                     _pins_block(client.get("/").text), re.S).group(0)
    assert "12" in mini and "#i-bell-on" in mini
    assert 'role="img" aria-label="avviso a 12 iscritti"' in mini


def test_bell_sheet_is_in_the_page_once(client):
    html = client.get("/").text
    assert html.count('<dialog id="bell"') == 1
    sheet = html.split('<dialog id="bell"', 1)[1].split("</dialog>", 1)[0]
    for text in ("Attiva avviso", "Rimuovi avviso", "Annulla"):
        assert text in sheet
    assert 'class="sheet"' in html.split('<dialog id="bell"', 1)[1][:80]


def test_icon_sprite_has_the_four_symbols(client):
    html = client.get("/").text
    for symbol in ("i-pin", "i-pin-on", "i-bell", "i-bell-on", "i-chev"):
        assert f'<symbol id="{symbol}"' in html
