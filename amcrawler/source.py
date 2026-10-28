"""Source: fetch and parse Uncovered Departures and the Itinerary catalogue."""
from __future__ import annotations

import json
import time
from datetime import date

import httpx

from .geo import fold_countries
from .models import Departure, Itinerary

BASE_URL = "https://www.viaggiavventurenelmondo.it"
MAX_PAGES = 50


class SourceError(Exception):
    """The site could not be fetched or its payload was not understood."""


def make_client() -> httpx.Client:
    return httpx.Client(
        base_url=BASE_URL,
        headers={"User-Agent": "AMCrawler/1.0 (personal use)"},
        timeout=20.0,
    )


def itinerary_url(itinerary_code: str) -> str:
    return f"{BASE_URL}/viaggi/{itinerary_code}"


def _str(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("expected non-empty string")
    return value


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("expected integer")
    return value


def _parse_departure(result: dict) -> Departure | None:
    """Return the Departure, or None when it is assigned or not Coordinator-regular."""
    base = result["base"]
    if not isinstance(base, dict):
        raise ValueError("base must be an object")
    if result["assignment"] is not None or base["tourleaderMode"] != "regular":
        return None
    category = base["category"]
    return Departure(
        id=str(_str(result["id"])),
        code=_str(result["code"]),
        itinerary_id=str(_str(base["id"])),
        itinerary_code=_str(base["code"]),
        name=_str(base["name"]),
        formula=_str(category["name"]),
        start_date=date.fromisoformat(_str(result["startDate"])[:10]),
        end_date=date.fromisoformat(_str(result["endDate"])[:10]),
        duration=_int(result["duration"]),
        participants=_int(result["pax"]),
        max_participants=_int(result["paxMax"]),
        notes=result["notes"] if isinstance(result["notes"], str) else "",
    )


def parse_tours_page(payload: dict) -> tuple[list[Departure], int]:
    """Parse one tours page into Uncovered Departures and the page size."""
    try:
        if payload["success"] is not True:
            raise ValueError("success is not true")
        data = payload["data"]
        size = _int(data["maxResultsPerPage"])
        results = data["results"]
        if not isinstance(results, list):
            raise ValueError("results must be a list")
        departures = []
        for result in results:
            dep = _parse_departure(result)
            if dep is not None:
                departures.append(dep)
    except (KeyError, TypeError, ValueError) as exc:
        raise SourceError(f"unexpected tours payload: {exc!r}") from exc
    return departures, size


def fetch_uncovered(client: httpx.Client, pause: float = 1.0) -> list[Departure]:
    """Fetch every Uncovered Departure, page by page."""
    found: list[Departure] = []
    for page in range(1, MAX_PAGES + 1):
        if page > 1:
            time.sleep(pause)
        params = {"fromTotalPax": 1, "isFull": 0, "hasAssignment": 0, "spg": page}
        try:
            response = client.get("/api/operative/tours", params=params)
            if response.status_code != 200:
                raise SourceError(f"tours page {page}: HTTP {response.status_code}")
            payload = response.json()
        except httpx.HTTPError as exc:
            raise SourceError(f"tours page {page}: {exc!r}") from exc
        except ValueError as exc:
            raise SourceError(f"tours page {page}: invalid JSON") from exc
        deps, size = parse_tours_page(payload)
        if size < 1:
            raise SourceError(f"tours page {page}: maxResultsPerPage {size}")
        found.extend(deps)
        # last page is decided on the RAW result count, not the post-drop count
        if len(payload["data"]["results"]) < size:
            break
    else:
        raise SourceError("troppe pagine")
    return found


def parse_catalogue(js_text: str) -> list[Itinerary]:
    """Parse the `BASES` script into Itineraries."""
    try:
        marker = js_text.index("bases:")
        start = js_text.index("[", marker)
        bases, _ = json.JSONDecoder().raw_decode(js_text, start)
        itineraries = []
        for b in bases:
            areas = sorted(b["geographicAreas"], key=lambda a: a["ordinal"])
            itineraries.append(
                Itinerary(
                    id=str(b["id"]),
                    code=_str(b["code"]),
                    name=_str(b["name"]),
                    formula=_str(b["category"]["name"]),
                    areas=tuple(a["name"] for a in areas),
                    countries=fold_countries(c["isoCode2"] for c in b["countries"]),
                    coordinator_free=b["tourleaderMode"] != "regular",
                )
            )
    except (KeyError, TypeError, ValueError) as exc:
        raise SourceError(f"unexpected catalogue: {exc!r}") from exc
    return itineraries


def fetch_catalogue(client: httpx.Client) -> list[Itinerary]:
    try:
        response = client.get("/data/bases.js")
    except httpx.HTTPError as exc:
        raise SourceError(f"catalogue: {exc!r}") from exc
    if response.status_code != 200:
        raise SourceError(f"catalogue: HTTP {response.status_code}")
    return parse_catalogue(response.text)
