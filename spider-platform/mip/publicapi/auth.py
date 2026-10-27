"""Authentication for the public API.

* OAuth2 client-credentials: ``POST /api/v2/oauth2/token`` → signed bearer token ``emt_…`` (HMAC-SHA256,
  stateless; revocation is enforced by checking the client on every request).
* API keys: ``emk_<prefix>_<secret>`` — only prefix + SHA-256 hash are stored.
* The service admin token (``MIP_ADMIN_TOKEN``, header ``X-Admin-Token``) acts as a superuser for the
  developer console's server-side calls.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import secrets
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field

from fastapi import Depends, Request, Response
from sqlalchemy import select

from ..config import get_settings
from ..db import session_scope
from ..models import ApiClient, ApiKey, ApiRequestLog, AppSetting
from .errors import ApiError

SCOPES: dict[str, str] = {
    "api:ontologies-read": "Read ontology metadata and objects (materials, machines, processes, categories, observations …)",
    "api:datasets-read": "Read imported datasets: metadata, schema, rows and original files",
    "api:market-read": "Read market and material intelligence (FX, price series, benchmarks, alternatives)",
    "api:orchestration-read": "Read sources and builds (crawl jobs)",
    "api:orchestration-write": "Start and cancel builds (crawl jobs)",
    "api:admin": "Manage API clients, API keys and view usage",
}
READ_SCOPES = [s for s in SCOPES if s.endswith("-read")]


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


_secret_cache: bytes | None = None


def signing_secret() -> bytes:
    """MIP_SECRET_KEY if set, otherwise a random key generated once and persisted in app_settings."""
    global _secret_cache
    if _secret_cache:
        return _secret_cache
    import os
    env = os.environ.get("MIP_SECRET_KEY")
    if env:
        _secret_cache = env.encode()
        return _secret_cache
    with session_scope() as s:
        row = s.get(AppSetting, "token_signing_secret")
        if row is None:
            row = AppSetting(key="token_signing_secret", value=secrets.token_hex(32))
            s.add(row)
        _secret_cache = str(row.value).encode()
    return _secret_cache


def issue_token(client_id: str, scopes: list[str], ttl: int) -> tuple[str, int]:
    now = int(time.time())
    payload = _b64(json.dumps({"cid": client_id, "sc": scopes, "iat": now, "exp": now + ttl,
                               "jti": secrets.token_hex(8)}, separators=(",", ":")).encode())
    sig = _b64(hmac.new(signing_secret(), payload.encode(), hashlib.sha256).digest())
    return f"emt_{payload}.{sig}", ttl


def new_client_secret() -> str:
    return "ems_" + secrets.token_urlsafe(32)


def new_api_key() -> tuple[str, str]:
    prefix = secrets.token_hex(4)
    return prefix, f"emk_{prefix}_{secrets.token_urlsafe(32)}"


@dataclass
class Principal:
    kind: str  # client | key | admin
    id: str
    scopes: set[str] = field(default_factory=set)
    rate_limit: int = 600

    def require(self, *needed: str) -> None:
        if self.kind == "admin":
            return
        missing = [s for s in needed if s not in self.scopes]
        if missing:
            raise ApiError("PERMISSION_DENIED", "MissingScope", {"requiredScopes": list(needed), "missingScopes": missing})


def _verify_token(tok: str) -> Principal:
    try:
        payload, sig = tok[4:].split(".", 1)
        good = _b64(hmac.new(signing_secret(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(good, sig):
            raise ValueError("signature")
        data = json.loads(_unb64(payload))
    except Exception:
        raise ApiError("UNAUTHORIZED", "InvalidToken", {"reason": "malformed or bad signature"})
    if data["exp"] < time.time():
        raise ApiError("UNAUTHORIZED", "InvalidToken", {"reason": "expired"})
    with session_scope() as s:
        c = s.execute(select(ApiClient).where(ApiClient.client_id == data["cid"])).scalar_one_or_none()
        if c is None or c.revoked_at is not None:
            raise ApiError("UNAUTHORIZED", "InvalidToken", {"reason": "client revoked"})
        if c.secret_rotated_at and data["iat"] < c.secret_rotated_at.timestamp() - 1:
            raise ApiError("UNAUTHORIZED", "InvalidToken", {"reason": "issued before secret rotation"})
        return Principal("client", c.client_id, set(data["sc"]) & set(c.scopes), c.rate_limit_per_minute)


def _verify_key(key: str) -> Principal:
    parts = key.split("_", 2)
    if len(parts) != 3:
        raise ApiError("UNAUTHORIZED", "InvalidToken", {"reason": "malformed api key"})
    with session_scope() as s:
        k = s.execute(select(ApiKey).where(ApiKey.prefix == parts[1])).scalar_one_or_none()
        if k is None or not hmac.compare_digest(k.key_hash, sha256(key)):
            raise ApiError("UNAUTHORIZED", "InvalidToken", {"reason": "unknown api key"})
        now = dt.datetime.now(dt.timezone.utc)
        exp = k.expires_at.replace(tzinfo=dt.timezone.utc) if k.expires_at and k.expires_at.tzinfo is None else k.expires_at
        if k.revoked_at is not None or (exp and exp < now):
            raise ApiError("UNAUTHORIZED", "InvalidToken", {"reason": "api key revoked or expired"})
        last = k.last_used_at.replace(tzinfo=dt.timezone.utc) if k.last_used_at and k.last_used_at.tzinfo is None else k.last_used_at
        if last is None or (now - last).total_seconds() > 60:
            k.last_used_at = now
        return Principal("key", k.prefix, set(k.scopes), k.rate_limit_per_minute)


def authenticate(request: Request) -> Principal:
    admin = get_settings().admin_token
    if admin and hmac.compare_digest(request.headers.get("x-admin-token", ""), admin):
        return Principal("admin", "admin", set(SCOPES), 100_000)
    auth = request.headers.get("authorization", "")
    if not auth:
        raise ApiError("UNAUTHORIZED", "MissingCredentials", {}, {"WWW-Authenticate": "Bearer"})
    scheme, _, tok = auth.partition(" ")
    if scheme.lower() != "bearer" or not tok:
        raise ApiError("UNAUTHORIZED", "InvalidToken", {"reason": "expected 'Bearer <token>'"}, {"WWW-Authenticate": "Bearer"})
    tok = tok.strip()
    if tok.startswith("emt_"):
        return _verify_token(tok)
    if tok.startswith("emk_"):
        return _verify_key(tok)
    raise ApiError("UNAUTHORIZED", "InvalidToken", {"reason": "unrecognised token type"})


# ------------------------------------------------------------------ rate limiting (fixed window / minute)
class RateLimiter:
    def __init__(self):
        self.mem: dict[tuple[str, int], int] = defaultdict(int)
        self.lock = threading.Lock()
        self.redis = None
        url = get_settings().redis_url
        if url:
            try:
                import redis
                self.redis = redis.Redis.from_url(url, socket_timeout=0.5)
                self.redis.ping()
            except Exception:
                self.redis = None

    def hit(self, key: str, limit: int) -> tuple[int, int, int]:
        window = int(time.time() // 60)
        reset = (window + 1) * 60
        if self.redis is not None:
            try:
                rk = f"mip:rl:{key}:{window}"
                n = self.redis.incr(rk)
                if n == 1:
                    self.redis.expire(rk, 70)
                return n, limit, reset
            except Exception:
                pass
        with self.lock:
            self.mem[(key, window)] += 1
            n = self.mem[(key, window)]
            if len(self.mem) > 10_000:
                for k in [k for k in self.mem if k[1] < window]:
                    self.mem.pop(k, None)
        return n, limit, reset


_limiter: RateLimiter | None = None


def limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter()
    return _limiter


def principal(request: Request, response: Response) -> Principal:
    p = authenticate(request)
    request.state.principal = p
    n, limit, reset = limiter().hit(f"{p.kind}:{p.id}", p.rate_limit)
    headers = {"X-RateLimit-Limit": str(limit), "X-RateLimit-Remaining": str(max(0, limit - n)), "X-RateLimit-Reset": str(reset)}
    if n > limit:
        raise ApiError("TOO_MANY_REQUESTS", "RateLimitExceeded", {"limitPerMinute": limit, "resetAt": reset},
                       {**headers, "Retry-After": str(max(1, int(reset - time.time())))})
    for k, v in headers.items():
        response.headers[k] = v
    return p


def scoped(*scopes: str):
    def dep(p: Principal = Depends(principal)) -> Principal:
        p.require(*scopes)
        return p
    dep.__name__ = "requires_" + "_".join(s.replace(":", "_").replace("-", "_") for s in scopes)
    return dep


# ------------------------------------------------------------------ usage log (batched writer)
class UsageWriter:
    def __init__(self):
        self.buf: list[dict] = []
        self.lock = threading.Lock()
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def add(self, rec: dict) -> None:
        with self.lock:
            self.buf.append(rec)

    def flush(self) -> None:
        with self.lock:
            items, self.buf = self.buf, []
        if items:
            try:
                with session_scope() as s:
                    s.bulk_insert_mappings(ApiRequestLog, items)
            except Exception:
                pass

    def _loop(self):
        while True:
            time.sleep(2)
            self.flush()


_usage: UsageWriter | None = None


def usage() -> UsageWriter:
    global _usage
    if _usage is None:
        _usage = UsageWriter()
    return _usage


async def usage_middleware(request: Request, call_next):
    if not request.url.path.startswith("/api/v2"):
        return await call_next(request)
    t0 = time.perf_counter()
    response = await call_next(request)
    route = request.scope.get("route")
    p = getattr(request.state, "principal", None)
    usage().add({"ts": dt.datetime.now(dt.timezone.utc), "principal_type": p.kind if p else "anonymous",
                 "principal_id": p.id if p else None, "method": request.method,
                 "route": getattr(route, "path", request.url.path)[:200], "status": response.status_code,
                 "duration_ms": round((time.perf_counter() - t0) * 1000, 2),
                 "error_name": getattr(request.state, "error_name", None),
                 "error_instance_id": getattr(request.state, "error_instance_id", None)})
    response.headers["X-Emithran-Api-Version"] = "2.0"
    return response
