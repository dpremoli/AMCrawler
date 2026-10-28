import logging
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from amcrawler.auth import AccessVerifier, AuthError, install_auth

TEAM = "team.example.cloudflareaccess.com"
AUD = "aud-123"


def _make_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwks(key, kid):
    import json

    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": kid, "alg": "RS256", "use": "sig"})
    return {"keys": [jwk]}


@pytest.fixture(scope="module")
def key():
    return _make_key()


def _token(key, kid="k1", aud=AUD, iss=f"https://{TEAM}", exp_delta=300):
    now = int(time.time())
    claims = {"aud": [aud], "iss": iss, "exp": now + exp_delta, "iat": now,
              "email": "owner@example.com"}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


def _client(verifier, dev_no_auth=False):
    app = FastAPI()
    install_auth(app, verifier, dev_no_auth)

    @app.get("/x")
    def x():
        return {"ok": True}

    @app.get("/health")
    def health():
        return {"ok": True}

    return TestClient(app)


def _verifier(key, fetcher=None):
    return AccessVerifier(TEAM, AUD, jwks_fetcher=fetcher or (lambda: _jwks(key, "k1")))


def test_valid_header_token_allowed(key):
    c = _client(_verifier(key))
    r = c.get("/x", headers={"Cf-Access-Jwt-Assertion": _token(key)})
    assert r.status_code == 200


def test_valid_cookie_token_allowed(key):
    c = _client(_verifier(key))
    c.cookies.set("CF_Authorization", _token(key))
    assert c.get("/x").status_code == 200


def test_wrong_aud_rejected(key):
    c = _client(_verifier(key))
    r = c.get("/x", headers={"Cf-Access-Jwt-Assertion": _token(key, aud="other")})
    assert r.status_code == 403


def test_wrong_issuer_rejected(key):
    c = _client(_verifier(key))
    r = c.get("/x", headers={"Cf-Access-Jwt-Assertion": _token(key, iss="https://evil.example.com")})
    assert r.status_code == 403


def test_expired_rejected(key):
    c = _client(_verifier(key))
    r = c.get("/x", headers={"Cf-Access-Jwt-Assertion": _token(key, exp_delta=-3600)})
    assert r.status_code == 403


def test_wrong_signature_rejected(key):
    c = _client(_verifier(key))
    r = c.get("/x", headers={"Cf-Access-Jwt-Assertion": _token(_make_key())})
    assert r.status_code == 403


def test_hs256_token_rejected(key):
    c = _client(_verifier(key))
    bad = jwt.encode({"aud": AUD, "iss": f"https://{TEAM}", "exp": int(time.time()) + 300},
                     "secret" * 8, algorithm="HS256", headers={"kid": "k1"})
    assert c.get("/x", headers={"Cf-Access-Jwt-Assertion": bad}).status_code == 403


def test_missing_token_rejected(key):
    c = _client(_verifier(key))
    r = c.get("/x")
    assert r.status_code == 403
    assert r.text == "Accesso negato"
    assert r.headers["content-type"].startswith("text/plain")


def test_garbage_token_rejected(key):
    c = _client(_verifier(key))
    assert c.get("/x", headers={"Cf-Access-Jwt-Assertion": "not.a.jwt"}).status_code == 403


def test_health_open_without_token(key):
    c = _client(_verifier(key))
    assert c.get("/health").status_code == 200


def test_unknown_kid_refetches_once(key):
    calls = []

    def fetcher():
        calls.append(1)
        return {"keys": []} if len(calls) == 1 else _jwks(key, "k1")

    v = _verifier(key, fetcher)
    assert v.verify(_token(key))["iss"] == f"https://{TEAM}"
    assert len(calls) == 2


def test_unknown_kid_still_unknown_raises_after_one_refetch(key):
    calls = []

    def fetcher():
        calls.append(1)
        return {"keys": []}

    v = _verifier(key, fetcher)
    with pytest.raises(AuthError):
        v.verify(_token(key))
    assert len(calls) == 2


def test_jwks_cached(key):
    calls = []

    def fetcher():
        calls.append(1)
        return _jwks(key, "k1")

    v = _verifier(key, fetcher)
    v.verify(_token(key))
    v.verify(_token(key))
    assert len(calls) == 1


def test_fetch_failure_is_403_not_500(key):
    def fetcher():
        raise RuntimeError("network down")

    c = _client(_verifier(key, fetcher))
    r = c.get("/x", headers={"Cf-Access-Jwt-Assertion": _token(key)})
    assert r.status_code == 403
    assert r.text == "Accesso negato"


def test_dev_no_auth_allows_and_warns(caplog):
    c = _client(None, dev_no_auth=True)
    with caplog.at_level(logging.WARNING, logger="amcrawler.auth"):
        assert c.get("/x").status_code == 200
    assert "AMC_DEV_NO_AUTH attivo: autenticazione disattivata" in caplog.text


def test_no_verifier_rejects():
    c = _client(None)
    assert c.get("/x").status_code == 403


def test_failure_logs_reason_only(key, caplog):
    c = _client(_verifier(key))
    tok = _token(key, aud="other")
    with caplog.at_level(logging.DEBUG, logger="amcrawler.auth"):
        c.get("/x", headers={"Cf-Access-Jwt-Assertion": tok})
    assert tok not in caplog.text
    assert "owner@example.com" not in caplog.text
    assert "audience" in caplog.text
