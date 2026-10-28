"""New Entry detection: today's Digest Filter applied to both snapshots."""
from __future__ import annotations

from datetime import date

from amcrawler.filters import apply
from amcrawler.models import Departure, Filter


def new_entries(
    current: list[Departure],
    baseline: list[Departure],
    f: Filter,
    hidden: set[str],
    today: date,
) -> list[Departure]:
    """Departures matching `f` now that did not match `f` in the baseline snapshot."""
    previously = {d.id for d in apply(baseline, f, today)}
    fresh = [d for d in apply(current, f, today) if d.id not in previously and d.id not in hidden]
    return sorted(fresh, key=lambda d: (d.start_date, d.name))
