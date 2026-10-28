"""Digest email rendering (Italian) and SMTP delivery."""
from __future__ import annotations

import smtplib
import ssl
from datetime import date
from email.message import EmailMessage

from jinja2 import Environment, PackageLoader, select_autoescape

from .geo import country_name_it
from .models import Departure, Filter
from .source import itinerary_url

MONTHS_IT = ("gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic")

_env = Environment(
    loader=PackageLoader("amcrawler", "templates"),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def giorni(n: int) -> str:
    return f"{n} giorno" if n == 1 else f"{n} giorni"


def short_date(d: date) -> str:
    return f"{d.day} {MONTHS_IT[d.month - 1]}"


def date_range(start: date, end: date) -> str:
    return f"{short_date(start)} – {short_date(end)}"


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


def describe_filter(f: Filter) -> str:
    parts: list[str] = []
    if f.areas:
        parts.append("Aree: " + ", ".join(f.areas))
    if f.countries:
        parts.append("Paesi: " + ", ".join(country_name_it(c) for c in f.countries))
    if f.formulas:
        label = "Formula" if len(f.formulas) == 1 else "Formule"
        parts.append(f"{label}: " + ", ".join(f.formulas))
    if f.days_from is not None and f.days_to is not None:
        parts.append(f"Partenza tra {f.days_from} e {giorni(f.days_to)}")
    elif f.days_from is not None:
        parts.append(f"Partenza tra almeno {giorni(f.days_from)}")
    elif f.days_to is not None:
        parts.append(f"Partenza entro {giorni(f.days_to)}")
    if f.duration_min is not None and f.duration_max is not None:
        parts.append(f"Durata {f.duration_min}–{giorni(f.duration_max)}")
    elif f.duration_min is not None:
        parts.append(f"Durata minima {giorni(f.duration_min)}")
    elif f.duration_max is not None:
        parts.append(f"Durata massima {giorni(f.duration_max)}")
    if f.min_participants > 1:
        parts.append(f"Almeno {f.min_participants} iscritti")
    return " · ".join(parts) if parts else "Tutte le partenze senza coordinatore"


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
    html = _env.get_template("email/digest.html").render(**ctx)
    text = _env.get_template("email/digest.txt").render(**ctx)
    return subject, html, text


def render_stale(hours: int, last_error: str | None) -> tuple[str, str]:
    subject = f"AMCrawler: nessun aggiornamento da {hours} ore"
    text = _env.get_template("email/stale.txt").render(hours=hours, last_error=last_error)
    return subject, text


class SmtpMailer:
    def __init__(self, host: str, port: int, user: str, password: str, sender: str):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.sender = sender

    def send(self, to: str, subject: str, text: str, html: str | None = None) -> None:
        if not (self.user and self.password and self.sender):
            raise RuntimeError(
                "SMTP non configurato: impostare SMTP_USER, SMTP_PASSWORD e DIGEST_FROM")
        msg = EmailMessage()
        msg["From"] = self.sender
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(text)
        if html is not None:
            msg.add_alternative(html, subtype="html")
        with smtplib.SMTP(self.host, self.port, timeout=30) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(self.user, self.password)
            smtp.send_message(msg)
