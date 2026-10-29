"""The one Jinja environment shared by the web UI and the Digest email."""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from .geo import country_name_it
from .labels import (
    card_date, countdown, date_range, giorni, plural_it, seat_blocks, seat_percent,
    seats_label,
    weekday_abbr)
from .source import itinerary_url

_STATIC = Path(__file__).parent / "static"


@lru_cache(maxsize=None)
def static_url(path: str) -> str:
    """/static URL with a content hash, so a deploy never serves a stale cached file."""
    digest = hashlib.sha256((_STATIC / path).read_bytes()).hexdigest()[:10]
    return f"/static/{path}?v={digest}"


env = Environment(
    loader=PackageLoader("amcrawler", "templates"),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)
env.globals.update(
    date_range=date_range, giorni=giorni, plural_it=plural_it, itinerary_url=itinerary_url,
    card_date=card_date, countdown=countdown, seat_blocks=seat_blocks, seat_percent=seat_percent,
    seats_label=seats_label,
    weekday_abbr=weekday_abbr, static_url=static_url)
env.filters["country_name_it"] = country_name_it
