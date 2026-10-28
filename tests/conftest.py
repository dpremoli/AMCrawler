"""Shared test factories."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from amcrawler.models import Departure, Itinerary

TODAY = date(2026, 10, 28)
FIXTURES = Path(__file__).parent / "fixtures"


def load_json(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def read_text(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def dep(**overrides) -> Departure:
    start = overrides.get("start_date", TODAY + timedelta(days=30))
    values = dict(
        id="d1",
        code="2026-0001",
        itinerary_id="i1",
        itinerary_code="0001",
        name="Test",
        formula="Adventure",
        start_date=start,
        end_date=start + timedelta(days=11),
        duration=12,
        participants=5,
        max_participants=15,
        notes="",
        areas=("Sud Est Asiatico",),
        countries=("TH",),
    )
    values.update(overrides)
    return Departure(**values)


def itin(**overrides) -> Itinerary:
    values = dict(
        id="i1",
        code="0001",
        name="Test",
        formula="Adventure",
        areas=("Sud Est Asiatico",),
        countries=("TH",),
        coordinator_free=False,
    )
    values.update(overrides)
    return Itinerary(**values)


@pytest.fixture
def store(tmp_path):
    from amcrawler.store import Store

    s = Store(str(tmp_path / "t.db"))
    s.init()
    return s


class FakeMailer:
    """Records sent mail as (to, subject, text, html); fail=True makes send raise."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.sent: list[tuple] = []

    def send(self, to, subject, text, html=None):
        if self.fail:
            raise RuntimeError("smtp down")
        self.sent.append((to, subject, text, html))
