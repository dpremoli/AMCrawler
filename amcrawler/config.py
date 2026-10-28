"""Runtime configuration read from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class Config:
    public_url: str
    cf_team_domain: str
    cf_aud: str
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    digest_from: str
    digest_to: str
    tz: str
    db_path: str
    dev_no_auth: bool


def _port(value: str | None, default: int) -> int:
    try:
        return int(value) if value else default
    except ValueError:
        return default


def load_config(env: Mapping[str, str] = os.environ) -> Config:
    """Build a Config from env vars; never raises (bad values fall back)."""
    return Config(
        public_url=env.get("AMC_PUBLIC_URL", ""),
        cf_team_domain=env.get("CF_ACCESS_TEAM_DOMAIN", ""),
        cf_aud=env.get("CF_ACCESS_AUD", ""),
        smtp_host=env.get("SMTP_HOST") or "smtp.gmail.com",
        smtp_port=_port(env.get("SMTP_PORT"), 587),
        smtp_user=env.get("SMTP_USER", ""),
        smtp_password=env.get("SMTP_PASSWORD", ""),
        digest_from=env.get("DIGEST_FROM", ""),
        digest_to=env.get("DIGEST_TO", ""),
        tz=env.get("TZ") or "Europe/London",
        db_path=env.get("AMC_DB_PATH") or "/data/amcrawler.db",
        dev_no_auth=env.get("AMC_DEV_NO_AUTH") == "1",
    )
