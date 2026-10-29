"""Run one job now: python -m amcrawler crawl|digest."""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

from . import crawl, digest, source
from .config import load_config
from .mailer import SmtpMailer
from .store import Store

_USAGE = "usage: python -m amcrawler crawl|digest"


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in ("crawl", "digest"):
        print(_USAGE, file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO)
    cfg = load_config()
    store = Store(cfg.db_path)
    store.init()
    now = datetime.now(timezone.utc)
    if argv[0] == "crawl":
        with source.make_client() as client:
            snap = crawl.crawl_and_alert(store, client, SmtpMailer.from_config(cfg), cfg, now)
        if snap is None:
            print("crawl skipped: another is running", file=sys.stderr)
            return 1
        if not snap.ok:
            print(f"crawl failed: {snap.error}", file=sys.stderr)
            return 1
        print(f"crawl ok: snapshot {snap.id}")
    else:
        mailer = SmtpMailer.from_config(cfg)
        status = digest.run_digest(store, mailer, cfg, now)
        if status in digest.SKIP_REASONS:
            print(f"digest skipped: {digest.SKIP_REASONS[status]}")
            return 0
        print(f"digest: {status}")
        # "stale" means the crawler has been failing, so it is an error too.
        if status in digest.ERROR_STATUSES:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
