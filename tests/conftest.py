"""Shared test factories."""
from __future__ import annotations

from datetime import date, timedelta

from amcrawler.models import Departure, Itinerary

TODAY = date(2026, 10, 28)


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
