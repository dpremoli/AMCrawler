"""Digest email rendering (Italian) and SMTP delivery."""
from __future__ import annotations

import smtplib
import ssl
from datetime import date
from email.message import EmailMessage

from .config import Config
from .geo import country_name_it
from .labels import date_range, describe_filter, giorni, plural_it, short_date
from .models import Departure, Filter
from .source import itinerary_url
from .templating import env


def subject_for(count: int, day: date) -> str:
    noun = "nuova partenza" if count == 1 else "nuove partenze"
    return f"{count} {noun} senza coordinatore · {short_date(day)}"


def truncate_notes(text: str, limit: int = 140) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit + 1].rfind(" ")
    cut = limit if cut <= 0 else cut
    return text[:cut].rstrip() + "…"


def _card(d: Departure) -> dict:
    return {
        "name": d.name,
        "code": d.code,
        "dates": date_range(d.start_date, d.end_date),
        "duration": giorni(d.duration),
        "participants": f"{d.participants}/{d.max_participants} iscritti",
        "formula": d.formula,
        "countries": ", ".join(country_name_it(c) for c in d.countries),
        "notes": truncate_notes(d.notes),
        "url": itinerary_url(d.itinerary_code),
    }


def render_digest(
    entries: list[Departure], f: Filter, public_url: str, day: date
) -> tuple[str, str, str]:
    subject = subject_for(len(entries), day)
    ctx = {
        "subject": subject,
        "cards": [_card(d) for d in entries],
        "public_url": public_url,
        "filter_text": describe_filter(f),
    }
    html = env.get_template("email/digest.html").render(**ctx)
    text = env.get_template("email/digest.txt").render(**ctx)
    return subject, html, text


def render_stale(hours: int | None, last_error: str | None) -> tuple[str, str]:
    """Stale warning; `hours` is None when no snapshot ever succeeded."""
    if hours is None:
        subject = "AMCrawler: nessun aggiornamento riuscito finora"
    else:
        subject = f"AMCrawler: nessun aggiornamento da {plural_it(hours, 'ora', 'ore')}"
    text = env.get_template("email/stale.txt").render(
        hours=hours, hours_label=None if hours is None else plural_it(hours, "ora", "ore"),
        last_error=last_error)
    return subject, text


class MailerNotConfigured(RuntimeError):
    """Raised by send() before any network use when recipient or credentials are missing."""

    def __init__(self, message: str, recipient_missing: bool = False):
        super().__init__(message)
        self.recipient_missing = recipient_missing


class SmtpMailer:
    """Sends mail to one fixed recipient (the Coordinator)."""

    def __init__(self, host: str, port: int, user: str, password: str, sender: str,
                 recipient: str):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.sender = sender
        self.recipient = recipient

    @classmethod
    def from_config(cls, cfg: Config) -> "SmtpMailer":
        return cls(cfg.smtp_host, cfg.smtp_port, cfg.smtp_user, cfg.smtp_password,
                   cfg.digest_from, cfg.digest_to)

    def send(self, subject: str, text: str, html: str | None = None) -> None:
        missing = []
        if not self.recipient:
            missing.append("DIGEST_TO non impostato")
        if not (self.user and self.password and self.sender):
            missing.append(
                "SMTP non configurato: impostare SMTP_USER, SMTP_PASSWORD e DIGEST_FROM")
        if missing:
            raise MailerNotConfigured("; ".join(missing), not self.recipient)
        msg = EmailMessage()
        msg["From"] = self.sender
        msg["To"] = self.recipient
        msg["Subject"] = subject
        msg.set_content(text)
        if html is not None:
            msg.add_alternative(html, subtype="html")
        with smtplib.SMTP(self.host, self.port, timeout=30) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(self.user, self.password)
            smtp.send_message(msg)
