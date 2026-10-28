"""Italian wording shared by the web UI and the Digest email."""
from __future__ import annotations

from datetime import date

from .geo import country_name_it
from .models import Filter


MONTHS_IT = ("gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic")


def short_date(d: date) -> str:
    return f"{d.day} {MONTHS_IT[d.month - 1]}"


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
