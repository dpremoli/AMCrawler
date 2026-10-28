# AMCrawler

AMCrawler watches the Avventure nel Mondo catalogue for Departures that still
need a Coordinator (Uncovered), stores a daily snapshot, and emails the Owner a
digest of New Entries. A phone-first Italian web page (with a world map) lets
the Owner search, hide Departures and edit the Digest Filter. It runs as a
single FastAPI container behind swag and Cloudflare Access.

- Design spec: [docs/superpowers/specs/2026-10-28-amcrawler-design.md](docs/superpowers/specs/2026-10-28-amcrawler-design.md)
- Glossary and domain language: [CONTEXT.md](CONTEXT.md)
- Deployment on Unraid: [deploy/README.md](deploy/README.md)

## Development

Create the environment (Python 3.12 or newer):

```bash
python -m venv .venv
```

```bash
.venv/Scripts/python -m pip install -r requirements-dev.txt
```

Run the tests (no network; the live test is opt-in with `-m live`):

```bash
.venv/Scripts/python -m pytest
```

Run the app locally without Cloudflare Access:

```bash
AMC_DEV_NO_AUTH=1 AMC_DB_PATH=data/dev.db .venv/Scripts/python -m uvicorn --factory amcrawler.web:build_app --port 8000
```

Run a crawl or a digest once (exit 0 on success, 1 on failure):

```bash
.venv/Scripts/python -m amcrawler crawl
```

```bash
.venv/Scripts/python -m amcrawler digest
```
