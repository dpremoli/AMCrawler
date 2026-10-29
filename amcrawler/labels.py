"""Italian wording shared by the web UI and the Digest email."""
from __future__ import annotations

from datetime import date

from .geo import country_name_it
from .models import Filter


MONTHS_IT = ("gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic")


def short_date(d: date) -> str:
    return f"{d.day} {MONTHS_IT[d.month - 1]}"


WEEKDAYS_ABBR_IT = ("LUN", "MAR", "MER", "GIO", "VEN", "SAB", "DOM")
SOON_DAYS = 21  # a Departure this close gets the amber countdown
MAX_SEAT_BLOCKS = 30  # above this the card shows a continuous bar instead of blocks


def weekday_abbr(d: date) -> str:
    return WEEKDAYS_ABBR_IT[d.weekday()]


def weekday_date(d: date) -> str:
    """'ven 13 nov'."""
    return f"{weekday_abbr(d).lower()} {short_date(d)}"


def arrow_range(start: date, end: date) -> str:
    """'ven 13 nov → dom 29 nov'."""
    return f"{weekday_date(start)} → {weekday_date(end)}"


def card_date(d: date, today: date) -> str:
    """'13 nov', or '6 gen 2027' when the year is not the current one."""
    return short_date(d) if d.year == today.year else f"{short_date(d)} {d.year}"


def countdown(start: date, today: date) -> tuple[str, bool]:
    """(label, soon): when a Departure leaves; `soon` is true within SOON_DAYS days."""
    days = (start - today).days
    if days < 0:
        return "In corso", False
    if days == 0:
        return "Parte oggi", True
    if days == 1:
        return "Parte domani", True
    if days <= SOON_DAYS:
        return f"Parte tra {days} giorni", True
    return f"tra {days} giorni", False


def seat_blocks(participants: int, max_participants: int) -> list[bool] | None:
    """One flag per seat, the first `participants` filled; None when there is no sensible
    seat count to draw (unknown or more than MAX_SEAT_BLOCKS)."""
    if not 0 < max_participants <= MAX_SEAT_BLOCKS:
        return None
    return [i < participants for i in range(max_participants)]


def seat_percent(participants: int, max_participants: int) -> int:
    """Fill of the continuous bar; full when the seat count is unknown but someone joined."""
    if max_participants <= 0:
        return 100 if participants > 0 else 0
    return min(100, participants * 100 // max_participants)


def seats_label(participants: int, max_participants: int) -> str:
    noun = "iscritto" if participants == 1 else "iscritti"
    if max_participants > 0:
        return f"{participants} {noun} su {max_participants} posti"
    return f"{participants} {noun}"


def date_range(start: date, end: date) -> str:
    return f"{short_date(start)} – {short_date(end)}"


def plural_it(n: int, one: str, many: str) -> str:
    """'1 partenza' / '3 partenze'."""
    return f"{n} {one if n == 1 else many}"


def giorni(n: int) -> str:
    return plural_it(n, "giorno", "giorni")


_INT_LABELS = {
    "dfrom": "Partenza tra almeno {}",
    "dto": "Partenza entro {}",
    "dmin": "Durata minima {}",
    "dmax": "Durata massima {}",
}


def filter_parts(f: Filter) -> list[tuple[str, str, str]]:
    """One (query key, value, Italian label) per active constraint value."""
    parts: list[tuple[str, str, str]] = []
    for key, value in f.to_query():
        if key == "area":
            label = f"Area: {value}"
        elif key == "country":
            label = f"Paese: {country_name_it(value)}"
        elif key == "formula":
            label = f"Formula: {value}"
        elif key == "pmin":
            label = f"Almeno {value} iscritti"
        else:
            label = _INT_LABELS[key].format(giorni(int(value)))
        parts.append((key, value, label))
    return parts


def describe_filter(f: Filter) -> str:
    """Single-sentence summary of a Filter for the Digest footer."""
    labels = {key: label for key, _, label in filter_parts(f)
              if key in _INT_LABELS or key == "pmin"}
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
    else:
        parts.extend(labels[k] for k in ("dfrom", "dto") if k in labels)
    if f.duration_min is not None and f.duration_max is not None:
        parts.append(f"Durata {f.duration_min}–{giorni(f.duration_max)}")
    else:
        parts.extend(labels[k] for k in ("dmin", "dmax") if k in labels)
    if "pmin" in labels:
        parts.append(labels["pmin"])
    return " · ".join(parts) if parts else "Tutte le partenze senza coordinatore"
