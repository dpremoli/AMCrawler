"""Domain models: Itinerary, Departure and the Digest/Search Filter."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Mapping


@dataclass(frozen=True)
class Itinerary:
    id: str
    code: str
    name: str
    formula: str
    areas: tuple[str, ...]
    countries: tuple[str, ...]  # already folded
    coordinator_free: bool


@dataclass(frozen=True)
class Departure:
    id: str
    code: str
    itinerary_id: str
    itinerary_code: str
    name: str
    formula: str
    start_date: date
    end_date: date
    duration: int
    participants: int
    max_participants: int
    notes: str
    areas: tuple[str, ...] = ()
    countries: tuple[str, ...] = ()


_INT_FIELDS = (
    ("dfrom", "days_from"),
    ("dto", "days_to"),
    ("dmin", "duration_min"),
    ("dmax", "duration_max"),
)
_TUPLE_FIELDS = (("area", "areas"), ("country", "countries"), ("formula", "formulas"))


def _parse_nonneg_int(value: object) -> int | None:
    try:
        n = int(str(value).strip())
    except ValueError:
        return None
    return n if n >= 0 else None


@dataclass(frozen=True)
class Filter:
    areas: tuple[str, ...] = ()
    countries: tuple[str, ...] = ()
    formulas: tuple[str, ...] = ()
    days_from: int | None = None
    days_to: int | None = None
    duration_min: int | None = None
    duration_max: int | None = None
    min_participants: int = 1

    def to_dict(self) -> dict:
        return {
            "areas": list(self.areas),
            "countries": list(self.countries),
            "formulas": list(self.formulas),
            "days_from": self.days_from,
            "days_to": self.days_to,
            "duration_min": self.duration_min,
            "duration_max": self.duration_max,
            "min_participants": self.min_participants,
        }

    @classmethod
    def from_dict(cls, d: Mapping) -> "Filter":
        kwargs: dict = {}
        for _, attr in _TUPLE_FIELDS:
            kwargs[attr] = tuple(str(x) for x in d.get(attr) or ())
        for _, attr in _INT_FIELDS:
            kwargs[attr] = d.get(attr)
        kwargs["min_participants"] = d.get("min_participants", 1)
        return cls(**kwargs)

    def to_query(self) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        for key, attr in _TUPLE_FIELDS:
            pairs.extend((key, v) for v in getattr(self, attr))
        for key, attr in _INT_FIELDS:
            v = getattr(self, attr)
            if v is not None:
                pairs.append((key, str(v)))
        if self.min_participants != 1:
            pairs.append(("pmin", str(self.min_participants)))
        return pairs

    @classmethod
    def from_query(cls, pairs: Iterable[tuple[str, str]]) -> "Filter":
        tuples: dict[str, list[str]] = {attr: [] for _, attr in _TUPLE_FIELDS}
        kwargs: dict = {}
        tuple_keys = dict(_TUPLE_FIELDS)
        int_keys = dict(_INT_FIELDS)
        for key, value in pairs:
            if key in tuple_keys:
                if value != "":
                    tuples[tuple_keys[key]].append(value)
            elif key in int_keys:
                n = _parse_nonneg_int(value)
                if n is not None:
                    kwargs[int_keys[key]] = n
            elif key == "pmin":
                n = _parse_nonneg_int(value)
                if n is not None:
                    kwargs["min_participants"] = max(n, 1)
        return cls(**{a: tuple(v) for a, v in tuples.items()}, **kwargs)

    def is_empty(self) -> bool:
        return self == Filter()
