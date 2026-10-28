"""Country folding and ISO helpers."""
from __future__ import annotations

import gettext
from functools import lru_cache
from typing import Iterable

import pycountry

# Catalogue pseudo-codes for sub-national places (spec section 2.3).
PSEUDO_CODES: dict[str, tuple[str, ...]] = {
    "CT": ("IT",),
    "IA": ("IT",),
    "RD": ("IT",),
    "ND": ("IT",),
    "ZE": ("PT",),
    "MI": ("PT",),
    "LE": ("ES",),
    "ZI": ("GB",),
    "TB": ("CN",),
    "AK": ("US",),
    "PX": ("AR", "CL"),
    "IC": ("ES",),
    "SU": ("IT",),
    "AN": ("CW", "BQ"),
}

# Real-world codes pycountry does not know.
NAME_OVERRIDES_IT: dict[str, str] = {"XK": "Kosovo"}


def fold_countries(codes: Iterable[str]) -> tuple[str, ...]:
    """Fold pseudo-codes to real countries, order-preserving and deduplicated."""
    out: list[str] = []
    for code in codes:
        for real in PSEUDO_CODES.get(code, (code,)):
            if real not in out:
                out.append(real)
    return tuple(out)


def iso_numeric(alpha2: str) -> str | None:
    """3-digit zero-padded ISO numeric id (matches world-atlas), or None."""
    country = pycountry.countries.get(alpha_2=alpha2)
    return country.numeric if country else None


@lru_cache(maxsize=1)
def _italian():
    try:
        return gettext.translation(
            "iso3166-1", pycountry.LOCALES_DIR, languages=["it"]
        )
    except OSError:
        return None


def country_name_it(alpha2: str) -> str:
    """Italian country name; falls back to English name, then the code."""
    if alpha2 in NAME_OVERRIDES_IT:
        return NAME_OVERRIDES_IT[alpha2]
    country = pycountry.countries.get(alpha_2=alpha2)
    if not country:
        return alpha2
    tr = _italian()
    return tr.gettext(country.name) if tr else country.name
