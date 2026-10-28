"""Digest Filter / Search matching on Departures."""
from __future__ import annotations

import unicodedata
from datetime import date
from typing import Callable, Iterable

from amcrawler.models import Departure, Filter


def _any_in(wanted: tuple[str, ...], have: tuple[str, ...]) -> bool:
    return not wanted or any(w in have for w in wanted)


def matches(d: Departure, f: Filter, today: date) -> bool:
    """OR within each list, AND across criteria; ranges are inclusive."""
    if not _any_in(f.areas, d.areas):
        return False
    if not _any_in(f.countries, d.countries):
        return False
    if f.formulas and d.formula not in f.formulas:
        return False
    days = (d.start_date - today).days
    if f.days_from is not None and days < f.days_from:
        return False
    if f.days_to is not None and days > f.days_to:
        return False
    if f.duration_min is not None and d.duration < f.duration_min:
        return False
    if f.duration_max is not None and d.duration > f.duration_max:
        return False
    return d.participants >= f.min_participants


def apply(deps: Iterable[Departure], f: Filter, today: date) -> list[Departure]:
    return [d for d in deps if matches(d, f, today)]


def fold(text: str) -> str:
    """Casefold and strip accents, so "peru" finds "Perù"."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def text_matches(d: Departure, q: str, country_name: Callable[[str], str]) -> bool:
    """Search text: every whitespace-separated term must occur in the Departure's name,
    codes, Formula, Italian Country names or Area names. Not part of the Digest Filter."""
    terms = fold(q).split()
    if not terms:
        return True
    haystack = fold(" ".join([d.name, d.code, d.itinerary_code, d.formula,
                              *(country_name(c) for c in d.countries), *d.areas]))
    return all(t in haystack for t in terms)
