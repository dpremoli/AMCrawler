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


def _send(mailer, subject: str, html: str, text: str) -> bool:
    """Send one Alert mail; False (logged) when it did not go out, so the Alert stays."""
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
    Digest pause or weekdays say; a failed mail keeps its Alert for the next crawl."""
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
        else:
            subject, html, text = render_alert_gone(alert, cfg.public_url)
        if _send(mailer, subject, html, text):
            store.delete_alert(alert.departure_id)
