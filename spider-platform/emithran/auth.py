"""Sign-in with Auth0.

Humans reach this API through the Next.js front door, which sends their Auth0 access token (a JWT) as ``Authorization: Bearer``.
Machine callers (the Go fetch worker) use the shared secret in ``X-Admin-Token`` on /api/fetcher only.
With Auth0 unconfigured the API is open (development only); ``check_production`` refuses to start that way in production.
"""
from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import Depends, Header, HTTPException

from .config import get_settings

READ, ADMIN = "read:data", "admin:platform"          # permissions granted to Auth0 roles `viewer` and `admin`
log = logging.getLogger("emithran.auth")


@dataclass(frozen=True)
class Principal:
    sub: str
    permissions: frozenset[str]


DEV = Principal("dev", frozenset({READ, ADMIN}))


def enabled() -> bool:
    return bool(get_settings().auth0_domain)


@lru_cache
def _jwks(domain: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"https://{domain}/.well-known/jwks.json")     # caches signing keys; refetches on an unknown key id


def verify(token: str) -> Principal:
    s = get_settings()
    try:
        key = _jwks(s.auth0_domain).get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256"], audience=s.auth0_audience, issuer=f"https://{s.auth0_domain}/", leeway=10)
    except jwt.PyJWTError as exc:
        raise HTTPException(401, "Invalid or expired token", headers={"WWW-Authenticate": "Bearer"}) from exc
    return Principal(claims["sub"], frozenset(claims.get("permissions", [])))


def principal(authorization: str | None = Header(default=None)) -> Principal:
    if not enabled():
        return DEV
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "Sign in required", headers={"WWW-Authenticate": "Bearer"})
    return verify(token.strip())


def _need(p: Principal, permission: str) -> Principal:
    if permission not in p.permissions:
        raise HTTPException(403, f"Requires permission {permission}")
    return p


def user(p: Principal = Depends(principal)) -> Principal:
    """Any signed-in user with read access (role viewer or admin)."""
    return _need(p, READ)


def admin(p: Principal = Depends(principal), x_admin_token: str | None = Header(default=None)) -> Principal:
    """Write access. In development (no Auth0) the optional shared secret still guards writes."""
    if enabled():
        return _need(p, ADMIN)
    secret = get_settings().admin_token
    if secret and not hmac.compare_digest(x_admin_token or "", secret):
        raise HTTPException(401, "X-Admin-Token required")
    return p


def machine(x_admin_token: str | None = Header(default=None)) -> None:
    """Machine-to-machine (the Go fetch worker): the shared secret, never a user token."""
    secret = get_settings().admin_token
    if secret and not hmac.compare_digest(x_admin_token or "", secret):
        raise HTTPException(401, "X-Admin-Token required")


def check_production() -> None:
    s = get_settings()
    if s.env != "production":
        if not enabled():
            log.warning("Auth0 is not configured: the API is OPEN (development only)")
        return
    missing = [n for n, v in (("EMITHRAN_AUTH0_DOMAIN", s.auth0_domain), ("EMITHRAN_AUTH0_AUDIENCE", s.auth0_audience),
                              ("EMITHRAN_ADMIN_TOKEN", s.admin_token)) if not v]
    if missing:
        raise RuntimeError("EMITHRAN_ENV=production requires " + ", ".join(missing))
