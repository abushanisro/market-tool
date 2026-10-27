"""Auth0 sign-in: token verification, roles, machine secret and production guard. No network: local RSA key, stubbed JWKS."""
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

DOMAIN, AUD, SECRET = "emithran-test.us.auth0.com", "https://api.emithran.test", "machine-secret"


def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


KEY, OTHER_KEY = _key(), _key()


def _pem(k):
    return k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def token(perms=("read:data",), *, aud=AUD, iss=f"https://{DOMAIN}/", exp=3600, key=KEY, sub="auth0|u1"):
    claims = {"sub": sub, "aud": aud, "iss": iss, "iat": int(time.time()) - 5, "exp": int(time.time()) + exp, "permissions": list(perms)}
    return jwt.encode(claims, _pem(key), algorithm="RS256", headers={"kid": "t"})


def bearer(**kw):
    return {"Authorization": f"Bearer {token(**kw)}"}


@pytest.fixture()
def client(seeded, monkeypatch):
    from emithran import auth, config
    from emithran.api.main import app

    class StubJwks:                                    # what PyJWKClient would return after fetching Auth0's key set
        def get_signing_key_from_jwt(self, _token):
            return type("K", (), {"key": KEY.public_key()})()

    monkeypatch.setattr(auth, "_jwks", lambda domain: StubJwks())
    monkeypatch.setenv("EMITHRAN_AUTH0_DOMAIN", DOMAIN)
    monkeypatch.setenv("EMITHRAN_AUTH0_AUDIENCE", AUD)
    monkeypatch.setenv("EMITHRAN_ADMIN_TOKEN", SECRET)
    config.get_settings.cache_clear()
    with TestClient(app) as c:
        yield c
    config.get_settings.cache_clear()


def test_signed_in_users_can_read_and_everyone_else_cannot(client):
    assert client.get("/api/overview").status_code == 401                                         # no token
    assert client.get("/api/overview", headers={"Authorization": "Basic abc"}).status_code == 401
    assert client.get("/api/overview", headers=bearer()).status_code == 200                       # viewer
    assert client.get("/api/overview", headers=bearer(perms=())).status_code == 403               # valid token, no permission
    for bad in (bearer(aud="https://other"), bearer(iss="https://evil.example/"), bearer(exp=-60), bearer(key=OTHER_KEY)):
        r = client.get("/api/overview", headers=bad)
        assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"                 # wrong audience / issuer / expired / bad signature


def test_only_admins_can_write(client):
    body = {"source": "does-not-exist"}                    # reaches the handler only if auth passed: it answers 400 for an unknown source
    assert client.post("/api/jobs", json=body).status_code == 401
    assert client.post("/api/jobs", json=body, headers=bearer()).status_code == 403              # viewer
    assert client.post("/api/jobs", json=body, headers=bearer(perms=("read:data", "admin:platform"))).status_code == 400
    assert client.post("/api/jobs", json=body, headers={"X-Admin-Token": SECRET}).status_code == 401   # the machine secret is not a user login


def test_machine_secret_works_only_on_the_fetcher_api(client):
    assert client.get("/api/fetcher/jobs/999/plan").status_code == 401
    assert client.get("/api/fetcher/jobs/999/plan", headers={"X-Admin-Token": "wrong"}).status_code == 401
    assert client.get("/api/fetcher/jobs/999/plan", headers={"X-Admin-Token": SECRET}).status_code == 404     # authorized, job unknown
    assert client.get("/api/fetcher/jobs/999/plan", headers=bearer(perms=("read:data", "admin:platform"))).status_code == 401   # a user token is not a worker


def test_health_is_open_and_me_reports_the_role(client):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/me", headers=bearer()).json() == {"sub": "auth0|u1", "admin": False, "auth": True}
    assert client.get("/api/me", headers=bearer(perms=("read:data", "admin:platform"))).json()["admin"] is True


def test_event_stream_requires_sign_in(client):
    assert client.get("/api/stream?once=true").status_code == 401
    with client.stream("GET", "/api/stream?once=true", headers=bearer()) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")


def test_developer_console_admin_api_accepts_only_auth0_admins(client):
    url = "/api/v2/admin/clients"
    assert client.get(url).status_code == 401
    assert client.get(url, headers=bearer()).status_code == 403                                   # viewer
    assert client.get(url, headers=bearer(perms=("read:data", "admin:platform"))).status_code == 200
    assert client.get(url, headers={"X-Admin-Token": SECRET}).status_code == 401                  # the shared secret no longer opens it once Auth0 is on
    assert client.get(url, headers=bearer(exp=-60)).status_code == 401


def test_development_mode_stays_open_when_auth0_is_not_configured(seeded):
    from emithran.api.main import app

    with TestClient(app) as c:
        assert c.get("/api/overview").status_code == 200
        assert c.get("/api/me").json() == {"sub": "dev", "admin": True, "auth": False}


def test_production_refuses_to_start_without_auth0_and_the_machine_secret(seeded, monkeypatch):
    from emithran import auth, config

    monkeypatch.setenv("EMITHRAN_ENV", "production")
    config.get_settings.cache_clear()
    with pytest.raises(RuntimeError) as e:
        auth.check_production()
    assert all(v in str(e.value) for v in ("EMITHRAN_AUTH0_DOMAIN", "EMITHRAN_AUTH0_AUDIENCE", "EMITHRAN_ADMIN_TOKEN"))
    for k, v in (("EMITHRAN_AUTH0_DOMAIN", DOMAIN), ("EMITHRAN_AUTH0_AUDIENCE", AUD), ("EMITHRAN_ADMIN_TOKEN", SECRET)):
        monkeypatch.setenv(k, v)
    config.get_settings.cache_clear()
    auth.check_production()                                                                       # fully configured: starts
    config.get_settings.cache_clear()


def test_hosted_postgres_urls_are_accepted_as_given(monkeypatch):
    from emithran import config

    for given in ("postgres://u:p@h:5432/d", "postgresql://u:p@h:5432/d", "postgresql+psycopg://u:p@h:5432/d"):
        monkeypatch.setenv("EMITHRAN_DATABASE_URL", given)
        config.get_settings.cache_clear()
        assert config.get_settings().database_url == "postgresql+psycopg://u:p@h:5432/d"
    monkeypatch.setenv("EMITHRAN_DATABASE_URL", "sqlite:////tmp/x.db")
    config.get_settings.cache_clear()
    assert config.get_settings().database_url == "sqlite:////tmp/x.db"          # other URLs untouched
    config.get_settings.cache_clear()
