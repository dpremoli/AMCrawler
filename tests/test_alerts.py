import logging
from datetime import timedelta

from amcrawler.alerts import run_alerts
from amcrawler.config import load_config
from tests.conftest import NOW, TODAY, FakeMailer, dep

CFG = load_config({"AMC_PUBLIC_URL": "https://viaggi.example.com", "TZ": "Europe/London",
                   "DIGEST_TO": "owner@example.com"})


def _run(store, mailer, departures, now=NOW):
    run_alerts(store, mailer, CFG, now, departures)


def _alert(store, threshold=8, **overrides):
    d = dep(id="d1", name="Patagonia Trek", participants=5, max_participants=14, **overrides)
    store.set_alert(d, threshold, NOW)
    return d


def test_reached_sends_one_mail_and_ends_the_alert(store):
    _alert(store, threshold=8)
    mailer = FakeMailer()
    _run(store, mailer, [dep(id="d1", name="Patagonia Trek", participants=8,
                             max_participants=14)])
    assert [m[1] for m in mailer.sent] == ["AMCrawler: Patagonia Trek ha 8 iscritti"]
    assert store.alerts() == []
    _run(store, mailer, [dep(id="d1", participants=9)])
    assert len(mailer.sent) == 1  # the Alert is over


def test_a_departure_past_the_threshold_still_counts_as_reached(store):
    _alert(store, threshold=8)
    mailer = FakeMailer()
    _run(store, mailer, [dep(id="d1", name="Patagonia Trek", participants=11)])
    assert mailer.sent[0][1] == "AMCrawler: Patagonia Trek ha 11 iscritti"
    assert store.alerts() == []


def test_reached_mail_failure_keeps_the_alert_and_logs(store, caplog):
    _alert(store, threshold=8)
    with caplog.at_level(logging.ERROR, logger="amcrawler"):
        _run(store, FakeMailer(fail=True), [dep(id="d1", participants=8)])
    assert len(store.alerts()) == 1
    assert any(r.exc_info for r in caplog.records)
    mailer = FakeMailer()  # retried after the next accepted crawl
    _run(store, mailer, [dep(id="d1", participants=8)])
    assert len(mailer.sent) == 1 and store.alerts() == []


def test_unconfigured_mailer_keeps_the_alert_with_a_warning(store, caplog):
    _alert(store, threshold=8)
    with caplog.at_level(logging.WARNING, logger="amcrawler"):
        _run(store, FakeMailer(to=""), [dep(id="d1", participants=8)])
    assert len(store.alerts()) == 1
    assert any(r.levelno == logging.WARNING and not r.exc_info for r in caplog.records)


def test_below_threshold_refreshes_the_last_seen_data(store):
    _alert(store, threshold=8)
    mailer = FakeMailer()
    _run(store, mailer, [dep(id="d1", name="Patagonia Trek", participants=6,
                             max_participants=12)])
    assert mailer.sent == []
    a = store.alerts()[0]
    assert (a.threshold, a.participants, a.max_participants) == (8, 6, 12)


def test_gone_sends_the_last_seen_data_and_ends_the_alert(store):
    _alert(store, threshold=8)
    mailer = FakeMailer()
    _run(store, mailer, [dep(id="other")])
    assert [m[1] for m in mailer.sent] == [
        "AMCrawler: Patagonia Trek non è più nell'elenco"]
    assert "5/14 iscritti" in mailer.sent[0][2]
    assert store.alerts() == []


def test_gone_mail_failure_keeps_the_alert(store, caplog):
    _alert(store, threshold=8)
    with caplog.at_level(logging.ERROR, logger="amcrawler"):
        _run(store, FakeMailer(fail=True), [dep(id="other")])
    assert len(store.alerts()) == 1
    assert any(r.exc_info for r in caplog.records)


def test_empty_snapshot_does_nothing(store):
    _alert(store, threshold=8)
    mailer = FakeMailer()
    _run(store, mailer, [])
    assert mailer.sent == [] and len(store.alerts()) == 1


def test_departed_alert_is_dropped_silently(store):
    store.set_alert(dep(id="d1", start_date=TODAY - timedelta(days=3)), 8, NOW)
    store.set_alert(dep(id="d2", start_date=TODAY), 8, NOW)  # leaves today
    mailer = FakeMailer()
    _run(store, mailer, [dep(id="other")])
    assert mailer.sent == [] and store.alerts() == []


def test_departure_leaving_tomorrow_is_still_reported_gone(store):
    store.set_alert(dep(id="d1", name="Domani", start_date=TODAY + timedelta(days=1)), 8, NOW)
    mailer = FakeMailer()
    _run(store, mailer, [dep(id="other")])
    assert len(mailer.sent) == 1 and store.alerts() == []


def test_today_is_the_local_day(store):
    # 23:30 UTC on 28 Oct is 00:30 on 29 Oct in a UTC+1 zone
    cfg = load_config({"TZ": "Europe/Rome", "DIGEST_TO": "owner@example.com"})
    store.set_alert(dep(id="d1", start_date=TODAY + timedelta(days=1)), 8, NOW)
    mailer = FakeMailer()
    late = NOW.replace(hour=23, minute=30)
    run_alerts(store, mailer, cfg, late, [dep(id="other")])
    assert mailer.sent == [] and store.alerts() == []


def test_alerts_ignore_the_digest_pause_and_weekdays(store):
    store.set_digest_enabled(False)
    store.set_digest_weekdays([])
    _alert(store, threshold=8)
    mailer = FakeMailer()
    _run(store, mailer, [dep(id="d1", participants=8)])
    assert len(mailer.sent) == 1


def test_alerts_are_independent(store):
    _alert(store, threshold=8)
    store.set_alert(dep(id="d2", name="Altro", participants=2), 4, NOW)
    mailer = FakeMailer()
    _run(store, mailer, [dep(id="d1", participants=9), dep(id="d2", participants=2)])
    assert len(mailer.sent) == 1
    assert [a.departure_id for a in store.alerts()] == ["d2"]


def test_past_pins_are_dropped_after_a_crawl(store):
    store.pin("old", TODAY - timedelta(days=1), NOW)
    store.pin("today", TODAY, NOW)
    store.pin("later", TODAY + timedelta(days=9), NOW)
    _run(store, FakeMailer(), [dep(id="x")])
    assert store.pinned_ids() == {"today", "later"}


def test_the_mail_is_html_and_text_with_the_links(store):
    _alert(store, threshold=8, itinerary_code="0042")
    mailer = FakeMailer()
    _run(store, mailer, [dep(id="d1", itinerary_code="0042", participants=8)])
    _to, _subject, text, html = mailer.sent[0]
    assert "https://viaggi.example.com" in text and "https://viaggi.example.com" in html
    assert "0042" in text and html is not None
