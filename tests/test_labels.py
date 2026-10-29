from datetime import date

import pytest

from amcrawler.labels import (
    card_date,
    countdown,
    seat_blocks,
    seat_percent,
    seats_label,
    weekday_abbr,
)

TODAY = date(2026, 10, 28)


def test_weekday_abbr():
    assert [weekday_abbr(date(2026, 11, 9 + i)) for i in range(7)] == [
        "LUN", "MAR", "MER", "GIO", "VEN", "SAB", "DOM"]
    assert weekday_abbr(date(2026, 11, 13)) == "VEN"


def test_card_date_adds_the_year_only_when_it_is_not_this_year():
    assert card_date(date(2026, 11, 13), TODAY) == "13 nov"
    assert card_date(date(2026, 12, 31), TODAY) == "31 dic"
    assert card_date(date(2027, 1, 6), TODAY) == "6 gen 2027"
    assert card_date(date(2025, 12, 30), TODAY) == "30 dic 2025"


@pytest.mark.parametrize("days, text, soon", [
    (0, "Parte oggi", True),
    (1, "Parte domani", True),
    (2, "Parte tra 2 giorni", True),
    (21, "Parte tra 21 giorni", True),
    (22, "tra 22 giorni", False),
    (61, "tra 61 giorni", False),
    (-1, "In corso", False),
])
def test_countdown_boundaries(days, text, soon):
    start = date.fromordinal(TODAY.toordinal() + days)
    assert countdown(start, TODAY) == (text, soon)


def test_seat_blocks_fill_the_first_participants():
    assert seat_blocks(6, 15) == [True] * 6 + [False] * 9
    assert seat_blocks(0, 3) == [False] * 3
    assert seat_blocks(1, 14) == [True] + [False] * 13


def test_seat_blocks_fill_all_when_over_capacity():
    assert seat_blocks(9, 8) == [True] * 8


@pytest.mark.parametrize("participants, maximum", [(3, 0), (3, -1), (5, 31), (5, 99)])
def test_seat_blocks_none_when_unknown_or_too_many(participants, maximum):
    assert seat_blocks(participants, maximum) is None


def test_seat_blocks_allows_thirty():
    assert len(seat_blocks(2, 30)) == 30


def test_seats_label():
    assert seats_label(6, 15) == "6 iscritti su 15 posti"
    assert seats_label(1, 14) == "1 iscritto su 14 posti"
    assert seats_label(3, 0) == "3 iscritti"


@pytest.mark.parametrize("participants, maximum, percent", [
    (6, 15, 40), (0, 15, 0), (15, 15, 100), (20, 15, 100), (4, 40, 10), (4, 0, 100),
    (0, 0, 0)])
def test_seat_percent(participants, maximum, percent):
    assert seat_percent(participants, maximum) == percent


def test_weekday_date_and_arrow_range():
    from amcrawler.labels import arrow_range, weekday_date

    assert weekday_date(date(2026, 1, 3)) == "sab 3 gen"
    assert arrow_range(date(2027, 1, 3), date(2027, 1, 21)) == "dom 3 gen → gio 21 gen"
