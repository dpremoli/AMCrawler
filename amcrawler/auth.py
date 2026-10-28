"""Cloudflare Access JWT verification (ADR 0001). The app has no login of its own."""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

import httpx
import jwt
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)

LEEWAY_SECONDS = 30  # tolerated clock skew when checking exp
_HEADER = "Cf-Access-Jwt-Assertion"
_COOKIE = "CF_Authorization"


class AuthError(Exception):
    """Token rejected. The message is a reason category and never contains token data."""


def _default_fetcher(team_domain: str) -> Callable[[], dict]:
    def fetch() -> dict:
        resp = httpx.get(f"https://{team_domain}/cdn-cgi/access/certs", timeout=10.0)
        resp.raise_for_status()
        return resp.json()

    return fetch


class AccessVerifier:
    def __init__(self, team_domain: str, aud: str,
                 jwks_fetcher: Callable[[], dict] | None = None,
                 cache_seconds: int = 3600) -> None:
        self.team_domain = team_domain
        self.aud = aud
        self._fetcher = jwks_fetcher or _default_fetcher(team_domain)
        self._cache_seconds = cache_seconds
        self._keys: dict[str, object] = {}
        self._fetched_at = 0.0
        self._lock = threading.Lock()

    def _refresh(self) -> None:
        try:
            jwks = self._fetcher()
            keys = {}
            for jwk in jwks["keys"]:
                if jwk.get("kty") == "RSA" and jwk.get("kid"):
                    keys[jwk["kid"]] = jwt.PyJWK(jwk).key
        except Exception as exc:  # network, JSON or shape errors: all mean "cannot verify"
            raise AuthError("jwks unavailable") from exc
        self._keys = keys
        self._fetched_at = time.monotonic()

    def _key_for(self, kid: str):
        with self._lock:
            if time.monotonic() - self._fetched_at > self._cache_seconds:
                self._refresh()
            key = self._keys.get(kid)
            if key is None:  # unknown kid: key rotation, refetch once
                self._refresh()
                key = self._keys.get(kid)
            if key is None:
                raise AuthError("unknown key id")
            return key

    def verify(self, token: str) -> dict:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AuthError("malformed token") from exc
        if header.get("alg") != "RS256":
            raise AuthError("unexpected algorithm")
        kid = header.get("kid")
        if not isinstance(kid, str):
            raise AuthError("missing key id")
        key = self._key_for(kid)
        try:
            return jwt.decode(
                token, key, algorithms=["RS256"], audience=self.aud,
                issuer=f"https://{self.team_domain}",
                options={"require": ["exp"]}, leeway=LEEWAY_SECONDS,
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthError("expired") from exc
        except jwt.InvalidAudienceError as exc:
            raise AuthError("audience mismatch") from exc
        except jwt.InvalidIssuerError as exc:
            raise AuthError("issuer mismatch") from exc
        except jwt.InvalidSignatureError as exc:
            raise AuthError("bad signature") from exc
        except jwt.PyJWTError as exc:
            raise AuthError("invalid token") from exc


def install_auth(app: FastAPI, verifier: AccessVerifier | None, dev_no_auth: bool) -> None:
    def deny() -> PlainTextResponse:
        return PlainTextResponse("Accesso negato", status_code=403)

    @app.middleware("http")
    async def access_middleware(request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)
        if dev_no_auth:
            logger.warning("AMC_DEV_NO_AUTH attivo: autenticazione disattivata")
            return await call_next(request)
        if verifier is None:
            logger.warning("auth denied: verifier not configured")
            return deny()
        token = request.headers.get(_HEADER) or request.cookies.get(_COOKIE)
        if not token:
            logger.info("auth denied: missing token")
            return deny()
        try:
            await run_in_threadpool(verifier.verify, token)
        except AuthError as exc:
            logger.info("auth denied: %s", exc)
            return deny()
        return await call_next(request)
