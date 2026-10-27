"""OAuth2 token · Orchestration (sources & builds) · Admin (clients, keys, usage) · Meta."""
from __future__ import annotations

import datetime as dt
import hmac
import secrets
from typing import Any

from fastapi import APIRouter, Body, Depends, Form, Path, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...db import get_session
from ...jobs.service import JobError, cancel_job, create_job
from ...models import ApiClient, ApiKey, ApiRequestLog, CrawlJob, Source
from ...registry.service import freshness
from ..auth import SCOPES, issue_token, new_api_key, new_client_secret, scoped, sha256
from ..common import jsonable, next_token, page_params, parse_rid, rid
from ..errors import ERROR_CATALOG, ApiError, invalid, not_found
from .ontologies import PAGE_SIZE, PAGE_TOKEN, ext

router = APIRouter(prefix="/api/v2")


# ------------------------------------------------------------------ OAuth2
@router.post("/oauth2/token", summary="Get Access Token", openapi_extra={"x-group": "General", "x-resource": "Authentication", "x-scopes": [], "x-auth": "client"},
             description="OAuth 2.0 client-credentials grant (RFC 6749 §4.4). Send `application/x-www-form-urlencoded` with "
                         "`grant_type=client_credentials`, `client_id`, `client_secret` (or HTTP Basic) and an optional space-separated "
                         "`scope`. Returns a bearer token valid for the client's TTL (default 3600 s).")
def token(request: Request, grant_type: str = Form(..., examples=["client_credentials"]),
          client_id: str | None = Form(None), client_secret: str | None = Form(None),
          scope: str | None = Form(None, description="Space-separated subset of the client's scopes."),
          s: Session = Depends(get_session)):
    if grant_type != "client_credentials":
        raise invalid("UnsupportedGrantType", grantType=grant_type)
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("basic "):
        import base64
        try:
            client_id, client_secret = base64.b64decode(auth[6:]).decode().split(":", 1)
        except Exception:
            raise ApiError("UNAUTHORIZED", "InvalidClientCredentials", {})
    c = s.execute(select(ApiClient).where(ApiClient.client_id == (client_id or ""))).scalar_one_or_none()
    if c is None or c.revoked_at or not client_secret or not hmac.compare_digest(c.secret_hash, sha256(client_secret)):
        raise ApiError("UNAUTHORIZED", "InvalidClientCredentials", {})
    requested = scope.split() if scope else list(c.scopes)
    bad = [x for x in requested if x not in c.scopes]
    if bad:
        raise invalid("InvalidScope", scopes=bad, granted=c.scopes)
    tok, ttl = issue_token(c.client_id, requested, c.token_ttl_seconds)
    c.last_token_at = dt.datetime.now(dt.timezone.utc)
    s.commit()
    return {"access_token": tok, "token_type": "bearer", "expires_in": ttl, "scope": " ".join(requested)}


# ------------------------------------------------------------------ meta
@router.get("/meta/scopes", summary="List Scopes", openapi_extra={"x-group": "General", "x-resource": "Authentication", "x-scopes": []},
            description="Lists OAuth scopes and what they grant. No authentication required.")
def scopes():
    return {"data": [{"scope": k, "description": v} for k, v in SCOPES.items()]}


@router.get("/meta/errors", summary="List Error Types", openapi_extra={"x-group": "General", "x-resource": "Errors", "x-scopes": []},
            description="Catalogue of `errorName` values the API can return, with their `errorCode`. No authentication required.")
def errors():
    return {"data": ERROR_CATALOG}


# ------------------------------------------------------------------ orchestration
O = "Orchestration"
RO, WO = scoped("api:orchestration-read"), scoped("api:orchestration-write")


def src_json(x: Source) -> dict:
    return {"rid": rid("source", x.slug), "slug": x.slug, "name": x.name, "baseUrl": x.base_url, "sourceType": x.source_type,
            "dataKinds": x.data_kinds, "license": x.license, "commercialUse": x.commercial_use,
            "verificationStatus": x.verification_status, "status": x.status, "freshness": freshness(x),
            "schedule": {"cron": x.schedule_cron, "intervalMinutes": x.schedule_interval_minutes},
            "lastSuccessAt": jsonable(x.last_success_at), "totalRecords": x.total_records}


def build_json(j: CrawlJob, src: Source) -> dict:
    return {"rid": rid("build", j.id), "source": src.slug, "status": j.status.upper(), "trigger": j.trigger, "params": j.params,
            "createdAt": jsonable(j.created_at), "startedAt": jsonable(j.started_at), "finishedAt": jsonable(j.finished_at),
            "cancelRequested": j.cancel_requested, "stats": j.stats or {}, "error": j.error}


@router.get("/orchestration/sources", summary="List Sources", openapi_extra=ext(O, "Sources", "api:orchestration-read"),
            description="Lists registered data sources with licence, verification status, schedule and freshness.")
def list_sources(pageSize: int | None = PAGE_SIZE, pageToken: str | None = PAGE_TOKEN, s: Session = Depends(get_session), _=Depends(RO)):
    size, off = page_params(pageSize, pageToken)
    rows = s.execute(select(Source).order_by(Source.slug)).scalars().all()
    page = rows[off:off + size]
    return {"nextPageToken": next_token(off, size, len(rows), len(page)), "data": [src_json(x) for x in page]}


@router.get("/orchestration/sources/{source}", summary="Get Source", openapi_extra=ext(O, "Sources", "api:orchestration-read"),
            description="Gets one source by slug or RID.")
def get_source(source: str = Path(..., examples=["frankfurter-fx"]), s: Session = Depends(get_session), _=Depends(RO)):
    slug = parse_rid(source, "source")
    x = s.execute(select(Source).where(Source.slug == slug)).scalar_one_or_none()
    if x is None:
        raise not_found("SourceNotFound", source=source)
    return src_json(x)


@router.get("/orchestration/builds", summary="List Builds", openapi_extra=ext(O, "Builds", "api:orchestration-read"),
            description="Lists builds (crawl jobs), newest first. Status: QUEUED, STARTING, RUNNING, SUCCEEDED, PARTIAL, FAILED, CANCELLED.")
def list_builds(source: str | None = Query(None), pageSize: int | None = PAGE_SIZE, pageToken: str | None = PAGE_TOKEN,
                s: Session = Depends(get_session), _=Depends(RO)):
    size, off = page_params(pageSize, pageToken)
    q = select(CrawlJob, Source).join(Source, Source.id == CrawlJob.source_id)
    if source:
        q = q.where(Source.slug == parse_rid(source, "source"))
    total = s.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = s.execute(q.order_by(CrawlJob.id.desc()).limit(size).offset(off)).all()
    return {"nextPageToken": next_token(off, size, total, len(rows)), "data": [build_json(j, x) for j, x in rows]}


@router.get("/orchestration/builds/{buildRid}", summary="Get Build", openapi_extra=ext(O, "Builds", "api:orchestration-read"),
            description="Gets a build with live statistics (URLs discovered, pages fetched, items stored, failures).")
def get_build(buildRid: str = Path(..., examples=["ri.emithran.main.build.12"]), s: Session = Depends(get_session), _=Depends(RO)):
    key = parse_rid(buildRid, "build")
    j = s.get(CrawlJob, int(key)) if key.isdigit() else None
    if j is None:
        raise not_found("BuildNotFound", buildRid=buildRid)
    return build_json(j, j.source)


class BuildIn(BaseModel):
    source: str = Field(..., description="Source slug or RID.")
    forceRefresh: bool = Field(False, description="Ignore content-hash / ETag checkpoints.")
    backfill: dict[str, str] | None = Field(None, description='`{"startDate": "2026-01-01", "endDate": "2026-10-27"}` for sources with a backfill template.')


@router.post("/orchestration/builds", summary="Create Build", openapi_extra=ext(O, "Builds", "api:orchestration-write"),
             description="Queues a crawl of a source. Refused for planned, blocked, disabled or already-running sources. "
                         "The worker picks it up within seconds; poll Get Build for progress.")
def create_build(body: BuildIn, s: Session = Depends(get_session), _=Depends(WO)):
    params: dict[str, Any] = {"force_refresh": True} if body.forceRefresh else {}
    if body.backfill:
        params.update({"backfill": True, "start_date": body.backfill.get("startDate"), "end_date": body.backfill.get("endDate")})
    try:
        j = create_job(s, parse_rid(body.source, "source"), params=params)
        s.commit()
    except (JobError, ValueError, TypeError) as exc:
        s.rollback()
        raise invalid("BuildNotPermitted", source=body.source, reason=str(exc))
    return build_json(j, j.source)


@router.post("/orchestration/builds/{buildRid}/cancel", summary="Cancel Build", openapi_extra=ext(O, "Builds", "api:orchestration-write"),
             description="Requests cancellation. Queued builds stop immediately; running builds close within a few seconds.")
def cancel_build(buildRid: str = Path(...), s: Session = Depends(get_session), _=Depends(WO)):
    key = parse_rid(buildRid, "build")
    try:
        j = cancel_job(s, int(key))
        s.commit()
    except (JobError, ValueError) as exc:
        raise invalid("BuildNotPermitted", buildRid=buildRid, reason=str(exc))
    return build_json(j, j.source)


# ------------------------------------------------------------------ admin
A = scoped("api:admin")
AG = "Admin"


def client_json(c: ApiClient) -> dict:
    return {"clientId": c.client_id, "rid": rid("client", c.client_id), "name": c.name, "description": c.description, "owner": c.owner,
            "scopes": c.scopes, "rateLimitPerMinute": c.rate_limit_per_minute, "tokenTtlSeconds": c.token_ttl_seconds,
            "secretHint": "…" + c.secret_hint, "createdAt": jsonable(c.created_at), "secretRotatedAt": jsonable(c.secret_rotated_at),
            "lastTokenAt": jsonable(c.last_token_at), "revokedAt": jsonable(c.revoked_at), "status": "REVOKED" if c.revoked_at else "ACTIVE"}


def key_json(k: ApiKey) -> dict:
    return {"rid": rid("api-key", k.prefix), "prefix": f"emk_{k.prefix}_…", "name": k.name, "owner": k.owner, "scopes": k.scopes,
            "rateLimitPerMinute": k.rate_limit_per_minute, "createdAt": jsonable(k.created_at), "expiresAt": jsonable(k.expires_at),
            "lastUsedAt": jsonable(k.last_used_at), "revokedAt": jsonable(k.revoked_at), "status": "REVOKED" if k.revoked_at else "ACTIVE"}


def _check_scopes(sc: list[str]) -> None:
    bad = [x for x in sc if x not in SCOPES]
    if bad or not sc:
        raise invalid("InvalidScope", scopes=bad or sc, available=list(SCOPES))


class ClientIn(BaseModel):
    name: str = Field(..., min_length=2, max_length=160)
    description: str | None = None
    owner: str | None = None
    scopes: list[str] = Field(..., description="Subset of List Scopes.")
    rateLimitPerMinute: int = Field(600, ge=1, le=100_000)
    tokenTtlSeconds: int = Field(3600, ge=300, le=86_400)


@router.get("/admin/clients", summary="List Clients", openapi_extra=ext(AG, "Clients", "api:admin"), description="Lists OAuth2 clients.")
def list_clients(s: Session = Depends(get_session), _=Depends(A)):
    return {"data": [client_json(c) for c in s.execute(select(ApiClient).order_by(ApiClient.created_at.desc())).scalars()]}


@router.post("/admin/clients", summary="Create Client", openapi_extra=ext(AG, "Clients", "api:admin"),
             description="Creates an OAuth2 client. **`clientSecret` is returned only once** — store it securely.")
def create_client(body: ClientIn, s: Session = Depends(get_session), _=Depends(A)):
    _check_scopes(body.scopes)
    secret = new_client_secret()
    c = ApiClient(client_id="emc_" + secrets.token_hex(8), name=body.name, description=body.description, owner=body.owner,
                  secret_hash=sha256(secret), secret_hint=secret[-4:], scopes=body.scopes,
                  rate_limit_per_minute=body.rateLimitPerMinute, token_ttl_seconds=body.tokenTtlSeconds)
    s.add(c)
    s.commit()
    return {**client_json(c), "clientSecret": secret}


@router.post("/admin/clients/{clientId}/rotateSecret", summary="Rotate Client Secret", openapi_extra=ext(AG, "Clients", "api:admin"),
             description="Issues a new secret and invalidates tokens issued before the rotation. The new secret is returned once.")
def rotate(clientId: str, s: Session = Depends(get_session), _=Depends(A)):
    c = s.execute(select(ApiClient).where(ApiClient.client_id == clientId)).scalar_one_or_none()
    if c is None or c.revoked_at:
        raise not_found("ClientNotFound", clientId=clientId)
    secret = new_client_secret()
    c.secret_hash, c.secret_hint, c.secret_rotated_at = sha256(secret), secret[-4:], dt.datetime.now(dt.timezone.utc)
    s.commit()
    return {**client_json(c), "clientSecret": secret}


@router.post("/admin/clients/{clientId}/revoke", summary="Revoke Client", openapi_extra=ext(AG, "Clients", "api:admin"),
             description="Revokes the client; all of its tokens stop working immediately.")
def revoke_client(clientId: str, s: Session = Depends(get_session), _=Depends(A)):
    c = s.execute(select(ApiClient).where(ApiClient.client_id == clientId)).scalar_one_or_none()
    if c is None:
        raise not_found("ClientNotFound", clientId=clientId)
    c.revoked_at = c.revoked_at or dt.datetime.now(dt.timezone.utc)
    s.commit()
    return client_json(c)


class KeyIn(BaseModel):
    name: str = Field(..., min_length=2, max_length=160)
    owner: str | None = None
    scopes: list[str]
    rateLimitPerMinute: int = Field(600, ge=1, le=100_000)
    expiresInDays: int | None = Field(90, ge=1, le=730, description="Null for no expiry.")


@router.get("/admin/apiKeys", summary="List API Keys", openapi_extra=ext(AG, "API Keys", "api:admin"), description="Lists API keys (never the secret).")
def list_keys(s: Session = Depends(get_session), _=Depends(A)):
    return {"data": [key_json(k) for k in s.execute(select(ApiKey).order_by(ApiKey.created_at.desc())).scalars()]}


@router.post("/admin/apiKeys", summary="Create API Key", openapi_extra=ext(AG, "API Keys", "api:admin"),
             description="Creates an API key. **`apiKey` is returned only once.** Use it as `Authorization: Bearer emk_…`.")
def create_key(body: KeyIn, s: Session = Depends(get_session), _=Depends(A)):
    _check_scopes(body.scopes)
    prefix, key = new_api_key()
    k = ApiKey(name=body.name, owner=body.owner, prefix=prefix, key_hash=sha256(key), scopes=body.scopes,
               rate_limit_per_minute=body.rateLimitPerMinute,
               expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=body.expiresInDays) if body.expiresInDays else None)
    s.add(k)
    s.commit()
    return {**key_json(k), "apiKey": key}


@router.post("/admin/apiKeys/{prefix}/revoke", summary="Revoke API Key", openapi_extra=ext(AG, "API Keys", "api:admin"),
             description="Revokes the key immediately.")
def revoke_key(prefix: str, s: Session = Depends(get_session), _=Depends(A)):
    k = s.execute(select(ApiKey).where(ApiKey.prefix == prefix.replace("emk_", "").strip("_…"))).scalar_one_or_none()
    if k is None:
        raise not_found("ApiKeyNotFound", prefix=prefix)
    k.revoked_at = k.revoked_at or dt.datetime.now(dt.timezone.utc)
    s.commit()
    return key_json(k)


@router.get("/admin/usage", summary="Get Usage", openapi_extra=ext(AG, "Usage", "api:admin"),
            description="Request counts, error rate and latency (avg, p95) for the last `days`, per day, per endpoint and per principal.")
def usage(days: int = Query(7, ge=1, le=90), s: Session = Depends(get_session), _=Depends(A)):
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    rows = s.execute(select(ApiRequestLog).where(ApiRequestLog.ts >= since)).scalars().all()
    def summarize(items):
        lat = sorted(r.duration_ms for r in items)
        n = len(items)
        return {"requests": n, "errors": sum(1 for r in items if r.status >= 400),
                "avgLatencyMs": round(sum(lat) / n, 1) if n else None, "p95LatencyMs": round(lat[int(0.95 * (n - 1))], 1) if n else None}
    by_day, by_route, by_principal, by_status = {}, {}, {}, {}
    for r in rows:
        ts = r.ts if r.ts.tzinfo else r.ts.replace(tzinfo=dt.timezone.utc)
        by_day.setdefault(ts.date().isoformat(), []).append(r)
        by_route.setdefault(f"{r.method} {r.route}", []).append(r)
        by_principal.setdefault(f"{r.principal_type}:{r.principal_id or '-'}", []).append(r)
        by_status[str(r.status)] = by_status.get(str(r.status), 0) + 1
    return {"since": since.isoformat(), "total": summarize(rows),
            "byDay": [{"day": k, **summarize(v)} for k, v in sorted(by_day.items())],
            "byEndpoint": sorted([{"endpoint": k, **summarize(v)} for k, v in by_route.items()], key=lambda x: -x["requests"])[:50],
            "byPrincipal": sorted([{"principal": k, **summarize(v)} for k, v in by_principal.items()], key=lambda x: -x["requests"]),
            "byStatus": by_status}


@router.get("/admin/usage/requests", summary="List Recent Requests", openapi_extra=ext(AG, "Usage", "api:admin"),
            description="Most recent API requests with status, latency and `errorInstanceId` for failed calls.")
def recent(limit: int = Query(100, ge=1, le=1000), errorsOnly: bool = False, s: Session = Depends(get_session), _=Depends(A)):
    q = select(ApiRequestLog).order_by(ApiRequestLog.id.desc()).limit(limit)
    if errorsOnly:
        q = q.where(ApiRequestLog.status >= 400)
    return {"data": [{"ts": jsonable(r.ts), "principal": f"{r.principal_type}:{r.principal_id or '-'}", "method": r.method,
                      "route": r.route, "status": r.status, "durationMs": r.duration_ms, "errorName": r.error_name,
                      "errorInstanceId": r.error_instance_id} for r in s.execute(q).scalars()]}
