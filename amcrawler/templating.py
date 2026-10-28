"""The one Jinja environment shared by the web UI and the Digest email."""
from __future__ import annotations

from jinja2 import Environment, PackageLoader, select_autoescape

from .geo import country_name_it
from .labels import (
    card_date, countdown, date_range, giorni, plural_it, seat_blocks, seat_percent,
    seats_label,
    weekday_abbr)
from .source import itinerary_url

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
    weekday_abbr=weekday_abbr)
env.filters["country_name_it"] = country_name_it
