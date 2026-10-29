"""Alerts: after each accepted crawl, mail the Owner about Departures that reached their
Participant number or left the list, and tidy pins and Alerts of Departures that left."""
from __future__ import annotations

import logging
from datetime import datetime

from .config import Config
from .digest import today_in
from .mailer import MailerNotConfigured, render_alert_gone, render_alert_reached
from .models import Departure
from .store import Store

logger = logging.getLogger("amcrawler")

GONE_AFTER_MISSES = 2  # accepted crawls in a row without the Departure before "gone" is sent


def _send(mailer, subject: str, html: str, text: str) -> bool:
    """Send one Alert mail; False (logged) when it did not go out."""
    try:
        mailer.send(subject, text, html)
    except MailerNotConfigured as exc:
        logger.warning("alert mail not sent: %s", exc)
        return False
    except Exception:
        logger.exception("alert mail failed")
        return False
    return True


def run_alerts(store: Store, mailer, cfg: Config, now: datetime,
               departures: list[Departure]) -> None:
    """`departures` are those of the snapshot just accepted as OK. Alerts go out whatever the
    Digest pause or weekdays say. An Alert is claimed (deleted) before its mail is sent and
    put back when the mail fails, so it is retried after the next accepted crawl."""
    today = today_in(cfg.tz, now)
    store.delete_pins_before(today)
    current = {d.id: d for d in departures}
    for alert in store.alerts():
        d = current.get(alert.departure_id)
        if d is not None and d.participants >= alert.threshold:
            subject, html, text = render_alert_reached(d, cfg.public_url)
        elif d is not None:
            store.refresh_alert(d)
            continue
        elif not departures:
            continue  # an empty list is almost always the site being broken
        elif alert.start_date <= today:
            store.delete_alert(alert.departure_id)  # it has departed
            continue
        elif alert.misses + 1 < GONE_AFTER_MISSES:
            store.note_alert_miss(alert.departure_id)  # one crawl may just have skipped it
            continue
        else:
            subject, html, text = render_alert_gone(alert, cfg.public_url)
        if not store.claim_alert(alert.departure_id, alert.threshold):
            continue  # another crawl is sending it, or the Owner changed it
        if not _send(mailer, subject, html, text):
            store.restore_alert(alert, now)
