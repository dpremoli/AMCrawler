"""One-off helper: capture trimmed, anonymised test fixtures from the live site.

Run from the repo root:  .venv/Scripts/python scripts/capture_fixtures.py
Coordinator personal data (assignment.tourleader) is removed everywhere.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from amcrawler.source import BASE_URL, make_client  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
PARAMS = {"fromTotalPax": 1, "isFull": 0, "hasAssignment": 0}
# keys the parser needs plus a few harmless extras, to keep the sample small
BASE_KEYS = ("id", "code", "name", "tourleaderMode", "category", "countries", "geographicAreas")


def scrub(result: dict) -> dict:
    result = dict(result)
    if result.get("assignment"):
        result["assignment"] = {"id": "x", "status": "active"}
    return result


def fetch_page(client, page: int, with_filter: bool) -> dict:
    params = dict(PARAMS) if with_filter else {k: v for k, v in PARAMS.items() if k != "hasAssignment"}
    params["spg"] = page
    r = client.get("/api/operative/tours", params=params)
    r.raise_for_status()
    return r.json()


def trim_base(base: dict) -> dict:
    out = {k: base[k] for k in BASE_KEYS if k in base}
    out["countries"] = [
        {"id": c["id"], "isoCode2": c["isoCode2"], "name": c["name"]} for c in base.get("countries", [])
    ]
    out["geographicAreas"] = [
        {k: a[k] for k in ("id", "name", "ordinal", "slug") if k in a} for a in base.get("geographicAreas", [])
    ]
    return out


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    with make_client() as client:
        page1 = fetch_page(client, 1, True)
        page2 = fetch_page(client, 2, True)
        if len(page2["data"]["results"]) < 5:
            page2 = fetch_page(client, 2, False)
        for page, limit in ((page1, 30), (page2, 5)):
            page["data"]["results"] = [scrub(r) for r in page["data"]["results"][:limit]]
            page["data"]["results"] = [
                {**r, "base": {k: v for k, v in r["base"].items()}} for r in page["data"]["results"]
            ]
        (FIXTURES / "tours_page1.json").write_text(json.dumps(page1, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        (FIXTURES / "tours_page2.json").write_text(json.dumps(page2, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")

        wanted = {r["base"]["id"] for p in (page1, page2) for r in p["data"]["results"]}
        js = client.get("/data/bases.js")
        js.raise_for_status()
        text = js.text
    start = text.index("[", text.index("bases:"))
    bases, end = json.JSONDecoder().raw_decode(text, start)
    keep = [b for b in bases if b["id"] in wanted]
    kept_ids = {b["id"] for b in keep}
    extra_ng = next(b for b in bases if b["tourleaderMode"] == "no-tourleader" and b["id"] not in kept_ids)
    keep.append(extra_ng)
    kept_ids.add(extra_ng["id"])
    px = next((b for b in bases if any(c["isoCode2"] == "PX" for c in b.get("countries", [])) and b["id"] not in kept_ids), None)
    if px:
        keep.append(px)
    sample = [trim_base(b) for b in keep]
    out = '"use strict";var BASES={bases:' + json.dumps(sample, ensure_ascii=False) + ',search:{}};\n'
    (FIXTURES / "bases_sample.js").write_text(out, encoding="utf-8", newline="\n")
    print(f"pages: {len(page1['data']['results'])}+{len(page2['data']['results'])}, bases: {len(sample)}, from {BASE_URL}")


if __name__ == "__main__":
    main()
