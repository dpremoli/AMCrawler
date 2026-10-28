"""Shared test factories."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from amcrawler.mailer import MailerNotConfigured
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
    """Records sent mail as (to, subject, text, html); fail=True makes send raise.

    Like SmtpMailer it is bound to one recipient; to="" means not configured.
    """

    def __init__(self, fail: bool = False, to: str = "owner@example.com"):
        self.fail = fail
        self.to = to
        self.sent: list[tuple] = []

    def send(self, subject, text, html=None):
        if not self.to:
            raise MailerNotConfigured("DIGEST_TO non impostato", recipient_missing=True)
        if self.fail:
            raise RuntimeError("smtp down")
        self.sent.append((self.to, subject, text, html))


NOW = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def cfg_dev(tmp_path):
    from amcrawler.config import load_config

    return load_config({
        "AMC_PUBLIC_URL": "https://viaggi.example.com",
        "DIGEST_FROM": "maintainer@example.com",
        "DIGEST_TO": "owner@example.com",
        "AMC_DB_PATH": str(tmp_path / "web.db"),
        "AMC_DEV_NO_AUTH": "1",
    })


@pytest.fixture
def seeded_store(store):
    """Baseline holds d1 (Adventure, TH) and d2 (Discovery, JP); d3 is new since."""
    store.save_catalogue([
        itin(id="i1", code="0001", areas=("Sud Est Asiatico",), countries=("TH",)),
        itin(id="i2", code="0002", formula="Discovery", areas=("Estremo Oriente",),
             countries=("JP",)),
        itin(id="i3", code="0003", formula="Adventure", areas=("Africa",),
             countries=("KE",)),
    ], NOW)
    d1 = dep(id="d1", itinerary_id="i1", name="Thailandia Classica", participants=8,
             notes="Note del viaggio")
    d2 = dep(id="d2", code="2026-0002", itinerary_id="i2", itinerary_code="0002",
             name="Giappone Discovery", formula="Discovery", countries=("JP",),
             areas=("Estremo Oriente",), start_date=TODAY + timedelta(days=40))
    d3 = dep(id="d3", code="2026-0003", itinerary_id="i3", itinerary_code="0003",
             name="Kenya Safari", countries=("KE",), areas=("Africa",),
             start_date=TODAY + timedelta(days=50))
    sid = store.record_snapshot(NOW - timedelta(days=1), [d1, d2])
    store.record_digest_run(NOW - timedelta(days=1), sid, "baseline", [])
    store.record_snapshot(NOW - timedelta(hours=2), [d1, d2, d3])
    return store


@pytest.fixture
def client(cfg_dev, seeded_store):
    from fastapi.testclient import TestClient

    from amcrawler.web import create_app

    app = create_app(cfg_dev, store=seeded_store, mailer=FakeMailer(),
                     start_scheduler=False, now=lambda: NOW)
    return TestClient(app)
