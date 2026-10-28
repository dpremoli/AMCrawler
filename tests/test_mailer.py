from datetime import date

import pytest

from amcrawler.mailer import (
    SmtpMailer,
    date_range,
    describe_filter,
    giorni,
    render_digest,
    render_stale,
    subject_for,
    truncate_notes,
)
from amcrawler.models import Filter
from tests.conftest import dep


def test_subject_plural_singular():
    assert subject_for(3, date(2026, 10, 29)) == "3 nuove partenze senza coordinatore · 29 ott"
    assert subject_for(1, date(2026, 1, 5)) == "1 nuova partenza senza coordinatore · 5 gen"


def test_date_range():
    assert date_range(date(2026, 11, 8), date(2026, 11, 23)) == "8 nov – 23 nov"


def test_truncate_notes():
    assert truncate_notes("breve") == "breve"
    out = truncate_notes("parola " * 40)
    assert len(out) <= 141 and out.endswith("…")


def test_describe_filter_empty():
    assert describe_filter(Filter()) == "Tutte le partenze senza coordinatore"


def test_describe_filter_full():
    f = Filter(
        areas=("Sud Est Asiatico",), countries=("JP", "TH"), formulas=("Discovery",),
        days_from=14, days_to=120, duration_min=8, duration_max=17, min_participants=4,
    )
    assert describe_filter(f) == (
        "Aree: Sud Est Asiatico · Paesi: Giappone, Thailandia · Formula: Discovery · "
        "Partenza tra 14 e 120 giorni · Durata 8–17 giorni · Almeno 4 iscritti"
    )


def test_describe_filter_one_sided_bounds():
    assert describe_filter(Filter(days_from=14)) == "Partenza tra almeno 14 giorni"
    assert describe_filter(Filter(days_to=120)) == "Partenza entro 120 giorni"
    assert describe_filter(Filter(duration_min=8)) == "Durata minima 8 giorni"
    assert describe_filter(Filter(duration_max=17)) == "Durata massima 17 giorni"


def test_render_digest_contents():
    subj, html, text = render_digest(
        [dep(name="Giappone Discovery", itinerary_code="9650", participants=8,
             max_participants=15, countries=("JP",))],
        Filter(), "https://viaggi.example.com", date(2026, 10, 29),
    )
    assert subj == "1 nuova partenza senza coordinatore · 29 ott"
    assert "Giappone Discovery" in html and "8/15 iscritti" in html and "Giappone" in html
    assert "https://www.viaggiavventurenelmondo.it/viaggi/9650" in html
    assert "https://viaggi.example.com" in html
    assert '<html lang="it">' in html and "Vedi viaggio" in html
    assert "<style" not in html
    assert "Giappone Discovery" in text and "https://viaggi.example.com" in text


def test_digest_escapes_and_truncates_notes():
    _, html, _ = render_digest(
        [dep(notes="<b>x</b> " + "lungo " * 60)], Filter(),
        "https://viaggi.example.com", date(2026, 10, 29),
    )
    assert "<b>x</b>" not in html and "&lt;b&gt;" in html and "…" in html


def test_render_stale():
    subj, text = render_stale(30, "boom")
    assert subj == "AMCrawler: nessun aggiornamento da 30 ore"
    assert "boom" in text and "30" in text
    _, text2 = render_stale(30, None)
    assert "None" not in text2


def test_smtp_mailer_sends_multipart(monkeypatch):
    calls = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            calls["addr"] = (host, port)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, context=None):
            calls["tls"] = True

        def login(self, user, password):
            calls["login"] = (user, password)

        def send_message(self, msg):
            calls["msg"] = msg

    monkeypatch.setattr("amcrawler.mailer.smtplib.SMTP", FakeSMTP)
    SmtpMailer("smtp.example.com", 587, "u@example.com", "pw", "maintainer@example.com").send(
        "owner@example.com", "Oggetto", "testo", "<p>html</p>"
    )
    msg = calls["msg"]
    assert calls["addr"] == ("smtp.example.com", 587) and calls["tls"]
    assert calls["login"] == ("u@example.com", "pw")
    assert msg["To"] == "owner@example.com" and msg["From"] == "maintainer@example.com"
    assert msg["Subject"] == "Oggetto"
    assert msg.get_body(("plain",)).get_content().strip() == "testo"
    assert msg.get_body(("html",)).get_content().strip() == "<p>html</p>"


def test_giorni_singular_plural():
    assert giorni(1) == "1 giorno"
    assert giorni(12) == "12 giorni"


def test_singular_day_in_filter_and_card():
    f = Filter(days_from=1, days_to=1, duration_min=1, duration_max=1)
    text = describe_filter(f)
    assert "Partenza tra 1 e 1 giorno" in text and "Durata 1–1 giorno" in text
    assert describe_filter(Filter(days_to=1)) == "Partenza entro 1 giorno"
    _, html, plain = render_digest([dep(duration=1)], Filter(), "https://viaggi.example.com",
                                   date(2026, 10, 28))
    assert "1 giorno" in plain and "1 giorni" not in plain


def test_smtp_not_configured_raises_before_connecting(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("smtplib.SMTP must not be constructed")

    monkeypatch.setattr("amcrawler.mailer.smtplib.SMTP", boom)
    for user, pw, sender in [("", "pw", "m@example.com"), ("u", "", "m@example.com"),
                             ("u", "pw", "")]:
        with pytest.raises(RuntimeError) as exc:
            SmtpMailer("smtp.example.com", 587, user, pw, sender).send("o@example.com", "s", "t")
        assert str(exc.value) == (
            "SMTP non configurato: impostare SMTP_USER, SMTP_PASSWORD e DIGEST_FROM")
