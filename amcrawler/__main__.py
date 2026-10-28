"""Run one job now: python -m amcrawler crawl|digest."""
from __future__ import annotations

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
    cfg = load_config()
    store = Store(cfg.db_path)
    store.init()
    now = datetime.now(timezone.utc)
    if argv[0] == "crawl":
        with source.make_client() as client:
            snap = crawl.run_crawl(store, client, now)
        print(f"crawl ok: snapshot {snap.id}")
    else:
        mailer = SmtpMailer(cfg.smtp_host, cfg.smtp_port, cfg.smtp_user,
                            cfg.smtp_password, cfg.digest_from)
        print(f"digest: {digest.run_digest(store, mailer, cfg, now)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
