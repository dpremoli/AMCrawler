from datetime import date

import pytest

from amcrawler.mailer import (
    MailerNotConfigured,
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
from amcrawler.source import itinerary_url
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


def test_render_stale_singular_hour():
    subj, text = render_stale(1, None)
    assert subj == "AMCrawler: nessun aggiornamento da 1 ora"
    assert "da 1 ora." in text


def test_render_stale_without_any_successful_snapshot():
    subj, text = render_stale(None, "boom")
    assert subj == "AMCrawler: nessun aggiornamento riuscito finora"
    assert "None" not in text and "boom" in text


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
    SmtpMailer("smtp.example.com", 587, "u@example.com", "pw", "maintainer@example.com",
               "owner@example.com").send("Oggetto", "testo", "<p>html</p>")
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
    creds = "SMTP non configurato: impostare SMTP_USER, SMTP_PASSWORD e DIGEST_FROM"
    for user, pw, sender, to, message in [
            ("", "pw", "m@example.com", "o@example.com", creds),
            ("u", "", "m@example.com", "o@example.com", creds),
            ("u", "pw", "", "o@example.com", creds),
            ("u", "pw", "m@example.com", "", "DIGEST_TO non impostato"),
            ("", "", "", "", "DIGEST_TO non impostato; " + creds)]:
        with pytest.raises(MailerNotConfigured) as exc:
            SmtpMailer("smtp.example.com", 587, user, pw, sender, to).send("s", "t")
        assert str(exc.value) == message
        assert exc.value.recipient_missing is (to == "")


def test_render_quiet_links_to_the_app():
    from amcrawler.mailer import render_quiet

    subject, html, text = render_quiet(Filter(), "https://viaggi.example.com")
    assert subject == "AMCrawler: nessuna novità"
    for body in (html, text):
        assert "nessuna nuova partenza" in body.lower()
        assert "https://viaggi.example.com" in body


def test_render_current_subject_and_lines():
    from amcrawler.mailer import render_current

    subject, html, text = render_current(
        [dep(name="Giappone Discovery", countries=("JP",))], Filter(),
        "https://viaggi.example.com")
    assert subject == "AMCrawler: partenze attuali (1)"
    assert "Giappone Discovery" in html and "Giappone Discovery" in text
    assert "Vedi viaggio" in html


def _alert_mail_data(**overrides):
    values = dict(name="Patagonia Trek", itinerary_code="0042", start_date=date(2027, 1, 3),
                  end_date=date(2027, 1, 21), participants=13, max_participants=14)
    values.update(overrides)
    return dep(**values)


def test_render_alert_reached():
    from amcrawler.mailer import render_alert_reached

    subject, html, text = render_alert_reached(_alert_mail_data(), "https://viaggi.example.com")
    assert subject == "AMCrawler: Patagonia Trek ha 13 iscritti"
    for body in (html, text):
        assert "Patagonia Trek ha raggiunto 13 iscritti" in body
        assert "dom 3 gen → gio 21 gen" in body and "13/14 iscritti" in body
        assert "ancora senza Coordinatore" in body
        assert "L'avviso è stato spento. Per riceverne un altro, tocca la campanella del viaggio." in body
        assert "https://viaggi.example.com" in body
        assert itinerary_url("0042") in body


def test_render_alert_reached_text_dates_line():
    from amcrawler.mailer import render_alert_reached

    _subject, _html, text = render_alert_reached(_alert_mail_data(), "")
    assert ("dom 3 gen → gio 21 gen · 19 giorni · 13/14 iscritti · "
            "ancora senza Coordinatore") in text


def test_render_alert_gone_quotes_the_last_seen_data():
    from amcrawler.mailer import render_alert_gone

    subject, html, text = render_alert_gone(_alert_mail_data(participants=9),
                                            "https://viaggi.example.com")
    assert subject == "AMCrawler: Patagonia Trek non è più nell'elenco"
    for body in (html, text):
        assert "Patagonia Trek non è più tra le partenze senza Coordinatore" in body
        assert ("Ha trovato un Coordinatore, è al completo oppure è stata annullata. "
                "Ultimo dato: 9/14 iscritti.") in body
        assert itinerary_url("0042") in body


def test_render_alert_gone_omits_the_seat_count_when_unknown():
    from amcrawler.mailer import render_alert_gone

    _subject, html, text = render_alert_gone(_alert_mail_data(participants=9,
                                                              max_participants=0),
                                             "https://viaggi.example.com")
    for body in (html, text):
        assert "Ultimo dato: 9 iscritti." in body


def test_alert_mail_escapes_the_name():
    from amcrawler.mailer import render_alert_reached

    _subject, html, _text = render_alert_reached(_alert_mail_data(name="A <b>&</b>"), "")
    assert "<b>&</b>" not in html and "A &lt;b&gt;&amp;&lt;/b&gt;" in html
