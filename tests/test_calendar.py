import re
import json
import shutil
import subprocess
from pathlib import Path

import pytest


STATIC = Path(__file__).parent.parent / "amcrawler" / "static"


def test_view_switcher_has_list_map_and_calendar(client):
    html = client.get("/").text
    switcher = html.split('aria-label="Vista"', 1)[1].split("</div>", 1)[0]
    for view, label in (("list", "Lista"), ("map", "Mappa"), ("cal", "Calendario")):
        assert f'data-view="{view}"' in switcher and f">{label}</button>" in switcher
    assert switcher.index("Lista") < switcher.index("Mappa") < switcher.index("Calendario")


def test_calendar_pane_markup(client):
    html = client.get("/").text
    assert 'data-today="2026-10-28"' in html
    pane = html.split('id="calendario"', 1)[1].split("</section>", 1)[0]
    assert 'aria-label="Mese precedente"' in pane and 'aria-label="Mese successivo"' in pane
    assert "Nessuna partenza in questo mese." in pane
    assert re.search(r'<script src="/static/calendar\.js\?v=[0-9a-f]{10}" defer></script>', html)


def test_calendar_script_is_served(client):
    r = client.get("/static/calendar.js")
    assert r.status_code == 200


def test_calendar_empty_message_follows_the_results(client):
    assert "Nessuna partenza corrisponde alla ricerca." in client.get("/?q=zzzz").text
    pane = client.get("/?q=zzzz").text.split('id="calendario"', 1)[1].split("</section>", 1)[0]
    assert "Nessuna partenza corrisponde alla ricerca." in pane


# The calendar's rules live in pure functions that node can run without a browser.
node = shutil.which("node")


def _js(expression):
    script = (f"const c = require({json.dumps(str(STATIC / 'calendar.js'))});"
              f"console.log(JSON.stringify({expression}));")
    out = subprocess.run([node, "-e", script], capture_output=True, encoding="utf-8", check=True)
    return json.loads(out.stdout)


@pytest.mark.skipif(node is None, reason="node is not installed")
class TestCalendarRules:
    def test_opens_on_the_current_month_when_it_has_a_departure(self):
        assert _js('c.openingMonth("2026-10-28", ["2026-10-30", "2026-12-01"])') == "2026-10"

    def test_opens_on_the_first_later_month_with_a_departure(self):
        assert _js('c.openingMonth("2026-10-28", ["2027-01-05", "2026-12-24"])') == "2026-12"

    def test_opens_on_the_current_month_when_nothing_is_later(self):
        assert _js('c.openingMonth("2026-10-28", ["2026-09-30"])') == "2026-10"
        assert _js('c.openingMonth("2026-10-28", [])') == "2026-10"

    def test_navigation_runs_from_this_month_to_the_last_departure_month(self):
        assert _js('c.monthRange("2026-10-28", ["2026-12-24", "2027-02-01"])') == [
            "2026-10", "2027-02"]
        assert _js('c.monthRange("2026-10-28", ["2026-09-01"])') == ["2026-10", "2026-10"]

    def test_first_day_with_departures_in_a_month(self):
        dates = ["2026-12-24", "2026-11-20", "2026-11-13", "2026-11-13"]
        assert _js('c.firstDayIn("2026-11", ' + json.dumps(dates) + ")") == "2026-11-13"
        assert _js('c.firstDayIn("2027-03", ' + json.dumps(dates) + ")") is None

    def test_grid_is_monday_first_with_blank_edges(self):
        weeks = _js("c.monthWeeks(2026, 11)")  # November 2026 starts on a Sunday
        assert weeks[0] == [0, 0, 0, 0, 0, 0, 1]
        assert weeks[1] == [2, 3, 4, 5, 6, 7, 8]
        assert weeks[-1] == [30, 0, 0, 0, 0, 0, 0]
        assert all(len(w) == 7 for w in weeks)
        feb = _js("c.monthWeeks(2027, 2)")  # February 2027 starts on a Monday
        assert feb[0] == [1, 2, 3, 4, 5, 6, 7] and feb[-1] == [22, 23, 24, 25, 26, 27, 28]

    def test_month_shift_wraps_the_year(self):
        assert _js('c.shiftMonth("2026-12", 1)') == "2027-01"
        assert _js('c.shiftMonth("2027-01", -1)') == "2026-12"

    def test_labels_are_italian(self):
        assert _js('c.monthTitle("2026-11")') == "Novembre 2026"
        assert _js('c.dayLabel("2026-11-13", 2)') == "13 novembre, 2 partenze"
        assert _js('c.dayLabel("2026-11-13", 1)') == "13 novembre, 1 partenza"
        assert _js('c.selectionLine("2026-11-13", 2)') == "Venerdì 13 novembre · 2 partenze"


@pytest.mark.skipif(node is None, reason="node is not installed")
def test_selection_stays_on_a_day_that_lost_its_last_departure():
    dates = json.dumps(["2026-11-20"])
    assert _js('c.keepSelection("2026-11", "2026-11-13", ' + dates + ")") == "2026-11-13"
    assert _js('c.keepSelection("2026-11", null, ' + dates + ")") == "2026-11-20"
    assert _js('c.keepSelection("2026-12", "2026-11-13", ' + dates + ")") is None


def test_list_empty_message_is_hidden_in_calendar_view():
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert '.layout[data-view="cal"] .list .empty { display: none; }' in css
