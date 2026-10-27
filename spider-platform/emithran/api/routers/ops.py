"""Overview, sources, jobs, settings."""
from __future__ import annotations

import datetime as dt
import os
import threading
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from ...config import get_settings
from ...crawler.parsers import PARSERS
from ...db import get_session, session_scope
from ...jobs.service import JobError, cancel_job, create_job
from ...models import (
    AlertEvent, AppSetting, Category, CrawlJob, CrawlLog, CrawlUrl, Dataset, DatasetRow, Document, Machine,
    Material, Observation, QualityIssue, Source, Standard,
)
from ...registry.service import SourceConfig, freshness, register_source, update_source
from ...auth import Principal, enabled as auth_enabled, principal, user
from ..deps import iso, require_admin, require_editor

router = APIRouter(prefix="/api", dependencies=[Depends(user)])
public = APIRouter(prefix="/api")           # no sign-in: the host's health check


def source_dict(src: Source, extra: dict | None = None) -> dict[str, Any]:
    d = {c.name: getattr(src, c.name) for c in Source.__table__.columns}
    for k in ("last_run_at", "last_success_at", "created_at", "updated_at", "verified_at"):
        d[k] = iso(d[k])
    d["freshness"] = freshness(src)
    d["credential_present"] = get_settings().credential_present(src.auth_env_var) if src.auth_env_var else None
    d.update(extra or {})
    return d


def job_dict(j: CrawlJob, src: Source | None = None) -> dict[str, Any]:
    src = src or j.source
    dur = None
    if j.started_at:
        end = j.finished_at or dt.datetime.now(dt.timezone.utc)
        st = j.started_at if j.started_at.tzinfo else j.started_at.replace(tzinfo=dt.timezone.utc)
        end = end if end.tzinfo else end.replace(tzinfo=dt.timezone.utc)
        dur = round((end - st).total_seconds(), 1)
    return {"id": j.id, "source": src.slug, "source_name": src.name, "status": j.status, "trigger": j.trigger,
            "params": j.params, "cancel_requested": j.cancel_requested, "created_at": iso(j.created_at),
            "started_at": iso(j.started_at), "finished_at": iso(j.finished_at), "heartbeat_at": iso(j.heartbeat_at),
            "duration_s": dur, "stats": j.stats or {}, "error": j.error, "worker": j.worker}


# ---------------------------------------------------------------- overview
@router.get("/me")
def me(p: Principal = Depends(principal)):
    """The signed-in user's identity and what they may do; the dashboard uses it to hide admin actions."""
    from ...auth import ADMIN, WRITE

    return {"sub": p.sub, "admin": ADMIN in p.permissions, "editor": bool(p.permissions & {WRITE, ADMIN}), "auth": auth_enabled()}


@public.get("/health")
def health(s: Session = Depends(get_session)):
    s.execute(select(1))
    return {"status": "ok", "time": dt.datetime.now(dt.timezone.utc).isoformat()}


@router.get("/overview")
def overview(s: Session = Depends(get_session)):
    now = dt.datetime.now(dt.timezone.utc)
    sources = s.execute(select(Source)).scalars().all()
    fresh: dict[str, int] = {}
    ver: dict[str, int] = {}
    for src in sources:
        fresh[freshness(src)] = fresh.get(freshness(src), 0) + 1
        ver[src.verification_status] = ver.get(src.verification_status, 0) + 1
    count = lambda m, *w: s.execute(select(func.count()).select_from(m).where(*w)).scalar_one()  # noqa: E731
    jobs_by_status = dict(s.execute(select(CrawlJob.status, func.count()).group_by(CrawlJob.status)).all())
    day_ago = now - dt.timedelta(days=1)
    obs_origin = {
        "crawled_live": count(Observation, Observation.source_id.is_not(None),
                              ~cast(Observation.quality_flags, String).like('%"replayed"%')),
        "crawled_replayed": count(Observation, Observation.source_id.is_not(None),
                                  cast(Observation.quality_flags, String).like('%"replayed"%')),
        "internal_taxonomy": count(Observation, Observation.source_id.is_(None)),
    }
    status_counts = dict(s.execute(select(Observation.status, func.count()).group_by(Observation.status)).all())
    kinds = dict(s.execute(select(Observation.kind, func.count()).group_by(Observation.kind)).all())
    # material-market coverage: families with ≥1 USD/kg price, by origin
    cov = s.execute(select(Material.family, func.count(func.distinct(Material.id)),
                           func.count(func.distinct(Observation.material_id)))
                    .outerjoin(Observation, (Observation.material_id == Material.id) & (Observation.kind == "price")
                               & (Observation.unit_normalized == "USD/kg") & Observation.status.in_(["verified", "unverified"]))
                    .group_by(Material.family)).all()
    recent = s.execute(select(CrawlJob).order_by(CrawlJob.created_at.desc()).limit(8)).scalars().all()
    last_ok = max((x.last_success_at for x in sources if x.last_success_at and x.source_type != "internal"), default=None)
    return {
        "generated_at": now.isoformat(),
        "sources": {"total": len(sources), "active": sum(1 for x in sources if x.status == "active"),
                    "crawlable": sum(1 for x in sources if x.source_type != "internal"),
                    "by_verification": ver, "freshness": fresh},
        "spiders": {"running": jobs_by_status.get("running", 0) + jobs_by_status.get("starting", 0),
                    "queued": jobs_by_status.get("queued", 0), "by_status": jobs_by_status,
                    "failed_24h": count(CrawlJob, CrawlJob.status == "failed", CrawlJob.created_at >= day_ago),
                    "failed_total": jobs_by_status.get("failed", 0)},
        "urls": {"discovered": count(CrawlUrl), "failed": count(CrawlUrl, CrawlUrl.status == "failed"),
                 "fetched": count(CrawlUrl, CrawlUrl.status.in_(["fetched", "unchanged", "not_modified"]))},
        "records": {"observations": sum(status_counts.values()), "by_status": status_counts, "by_kind": kinds,
                    "by_origin": obs_origin, "documents": count(Document), "standards": count(Standard)},
        "taxonomy": {"categories": count(Category), "datasets": count(Dataset), "rows": count(DatasetRow),
                     "materials": count(Material), "machines": count(Machine)},
        "coverage": [{"family": f, "materials": n, "priced": p} for f, n, p in sorted(cov, key=lambda r: -r[1])],
        "quality": {"open_issues": count(QualityIssue), "errors": count(QualityIssue, QualityIssue.severity == "error")},
        "alerts": {"open": count(AlertEvent, AlertEvent.acknowledged.is_(False))},
        "last_successful_refresh": iso(last_ok),
        "recent_jobs": [job_dict(j) for j in recent],
    }


# ---------------------------------------------------------------- sources
@router.get("/parsers")
def parsers():
    return {"parsers": sorted(PARSERS)}


@router.get("/sources")
def list_sources(q: str | None = None, category: str | None = None, country: str | None = None,
                 source_type: str | None = None, kind: str | None = None, verification: str | None = None,
                 status: str | None = None, material: str | None = None, s: Session = Depends(get_session)):
    rows = s.execute(select(Source).order_by(Source.status, Source.name)).scalars().all()
    obs_counts = dict(s.execute(select(Observation.source_id, func.count()).group_by(Observation.source_id)).all())
    doc_counts = dict(s.execute(select(Document.source_id, func.count()).group_by(Document.source_id)).all())
    url_counts = dict(s.execute(select(CrawlUrl.source_id, func.count()).group_by(CrawlUrl.source_id)).all())

    def match(src: Source) -> bool:
        if q and q.lower() not in f"{src.name} {src.slug} {src.base_url} {src.notes or ''}".lower():
            return False
        if category and not ({"*", category} & set(src.categories or [])):
            return False
        if country and country not in (src.countries or []):
            return False
        if source_type and src.source_type != source_type:
            return False
        if kind and kind not in (src.data_kinds or []):
            return False
        if verification and src.verification_status != verification:
            return False
        if status and src.status != status:
            return False
        if material and not any(material.lower() in m.lower() for m in src.materials or []):
            return False
        return True

    items = [source_dict(x, {"records": obs_counts.get(x.id, 0) + doc_counts.get(x.id, 0),
                             "urls": url_counts.get(x.id, 0)}) for x in rows if match(x)]
    return {"total": len(items), "items": items}


@router.get("/sources/{slug}")
def get_source(slug: str, s: Session = Depends(get_session)):
    src = s.execute(select(Source).where(Source.slug == slug)).scalar_one_or_none()
    if src is None:
        raise HTTPException(404, "source not found")
    jobs = s.execute(select(CrawlJob).where(CrawlJob.source_id == src.id).order_by(CrawlJob.created_at.desc()).limit(20)).scalars()
    url_status = dict(s.execute(select(CrawlUrl.status, func.count()).where(CrawlUrl.source_id == src.id)
                                .group_by(CrawlUrl.status)).all())
    return {**source_dict(src), "jobs": [job_dict(j, src) for j in jobs], "url_status": url_status,
            "records": s.execute(select(func.count()).select_from(Observation).where(Observation.source_id == src.id)).scalar_one()
            + s.execute(select(func.count()).select_from(Document).where(Document.source_id == src.id)).scalar_one()}


@router.get("/sources/{slug}/urls")
def source_urls(slug: str, status: str | None = None, limit: int = Query(100, le=1000), offset: int = 0,
                s: Session = Depends(get_session)):
    src = s.execute(select(Source).where(Source.slug == slug)).scalar_one_or_none()
    if src is None:
        raise HTTPException(404, "source not found")
    q = select(CrawlUrl).where(CrawlUrl.source_id == src.id)
    if status:
        q = q.where(CrawlUrl.status == status)
    rows = s.execute(q.order_by(CrawlUrl.last_fetched.desc().nulls_last()).limit(limit).offset(offset)).scalars()
    return {"items": [{"url": u.url, "status": u.status, "http_status": u.http_status, "depth": u.depth,
                       "etag": u.etag, "last_modified": u.last_modified, "content_hash": (u.content_hash or "")[:12],
                       "first_seen": iso(u.first_seen), "last_fetched": iso(u.last_fetched), "fetch_count": u.fetch_count,
                       "items_extracted": u.items_extracted, "error": u.error} for u in rows]}


@router.post("/sources", dependencies=[Depends(require_editor)])
def create_source(cfg: SourceConfig, s: Session = Depends(get_session)):
    try:
        src = register_source(s, cfg)
        s.commit()
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return source_dict(src)


@router.patch("/sources/{slug}", dependencies=[Depends(require_editor)])
def patch_source(slug: str, patch: dict[str, Any], s: Session = Depends(get_session)):
    src = s.execute(select(Source).where(Source.slug == slug)).scalar_one_or_none()
    if src is None:
        raise HTTPException(404, "source not found")
    if src.source_type == "internal":
        raise HTTPException(400, "internal source is managed by the importer")
    patch.pop("slug", None)
    try:
        update_source(s, src, patch)
        s.commit()
    except Exception as exc:
        s.rollback()
        raise HTTPException(400, str(exc))
    return source_dict(src)


# ---------------------------------------------------------------- jobs
class JobIn(BaseModel):
    source: str
    params: dict[str, Any] = {}


@router.get("/jobs")
def list_jobs(source: str | None = None, status: str | None = None, limit: int = Query(50, le=500),
              s: Session = Depends(get_session)):
    q = select(CrawlJob, Source).join(Source, Source.id == CrawlJob.source_id)
    if source:
        q = q.where(Source.slug == source)
    if status:
        q = q.where(CrawlJob.status == status)
    rows = s.execute(q.order_by(CrawlJob.created_at.desc()).limit(limit)).all()
    return {"items": [job_dict(j, src) for j, src in rows]}


@router.post("/jobs", dependencies=[Depends(require_editor)])
def start_job(body: JobIn, s: Session = Depends(get_session)):
    try:
        job = create_job(s, body.source, trigger="manual", params=body.params)
        s.commit()
    except (JobError, ValueError, KeyError) as exc:
        s.rollback()
        raise HTTPException(400, str(exc))
    return job_dict(job)


@router.post("/jobs/{job_id}/cancel", dependencies=[Depends(require_editor)])
def stop_job(job_id: int, s: Session = Depends(get_session)):
    try:
        job = cancel_job(s, job_id)
        s.commit()
    except JobError as exc:
        raise HTTPException(400, str(exc))
    return job_dict(job)


@router.get("/jobs/{job_id}")
def get_job(job_id: int, s: Session = Depends(get_session)):
    j = s.get(CrawlJob, job_id)
    if j is None:
        raise HTTPException(404, "job not found")
    return job_dict(j)


@router.get("/jobs/{job_id}/logs")
def job_logs(job_id: int, after: int = 0, limit: int = Query(200, le=2000), s: Session = Depends(get_session)):
    rows = s.execute(select(CrawlLog).where(CrawlLog.job_id == job_id, CrawlLog.id > after)
                     .order_by(CrawlLog.id).limit(limit)).scalars()
    return {"items": [{"id": r.id, "ts": iso(r.ts), "level": r.level, "message": r.message} for r in rows]}


# ---------------------------------------------------------------- settings
SETTING_KEYS = {"max_parallel_jobs": int, "global_max_pages_per_job": int, "default_download_delay": float,
                "default_concurrency": int, "auto_pause_after_failures": int, "job_timeout_minutes": int}


@router.get("/settings")
def get_settings_api(s: Session = Depends(get_session)):
    cfg = get_settings()
    overrides = {r.key: r.value for r in s.execute(select(AppSetting)).scalars()}
    creds = sorted({x.auth_env_var for x in s.execute(select(Source)).scalars() if x.auth_env_var} | {"EMITHRAN_LLM_API_KEY"})
    redis_ok = None
    if cfg.redis_url:
        try:
            import redis
            redis_ok = bool(redis.Redis.from_url(cfg.redis_url, socket_timeout=1).ping())
        except Exception:
            redis_ok = False
    last_hb = s.execute(select(func.max(CrawlJob.heartbeat_at))).scalar_one()
    db_url = cfg.database_url
    if "@" in db_url:
        db_url = db_url.split("://")[0] + "://***@" + db_url.split("@", 1)[1]
    return {
        "service": {"database": db_url, "redis_url": bool(cfg.redis_url), "redis_connected": redis_ok,
                    "data_root": str(cfg.data_root.resolve()), "data_root_exists": cfg.data_root.exists(),
                    "replay_mode": bool(cfg.replay_dir), "embedded_worker": cfg.embedded_worker,
                    "user_agent": cfg.user_agent, "contact_email": cfg.contact_email,
                    "admin_token_required": bool(cfg.admin_token), "last_job_heartbeat": iso(last_hb)},
        "crawl_policy": {k: overrides.get(k, getattr(cfg, k)) for k in SETTING_KEYS},
        "credentials": [{"env_var": c, "present": cfg.credential_present(c)} for c in creds],
        "policies": [
            "robots.txt obeyed by default (per-source override requires a documented API justification)",
            "No CAPTCHA solving, authentication bypass, paywall or anti-bot circumvention",
            "Sources marked blocked_by_terms or planned can never be crawled",
            "Bounded retries with exponential backoff; Retry-After honoured",
            "Per-source concurrency, delay, depth and page budgets; global page cap per job",
        ],
    }


@router.put("/settings", dependencies=[Depends(require_admin)])
def put_settings(body: dict[str, Any], s: Session = Depends(get_session)):
    out = {}
    for k, v in body.items():
        if k not in SETTING_KEYS:
            raise HTTPException(400, f"unknown setting {k}")
        try:
            val = SETTING_KEYS[k](v)
        except (TypeError, ValueError):
            raise HTTPException(400, f"invalid value for {k}")
        if val < 0:
            raise HTTPException(400, f"{k} must be ≥ 0")
        row = s.get(AppSetting, k) or AppSetting(key=k)
        row.value = val
        s.add(row)
        out[k] = val
    s.commit()
    return {"updated": out}


_import_state: dict[str, Any] = {"running": False, "last": None}


@router.post("/admin/import-taxonomy", dependencies=[Depends(require_admin)])
def import_taxonomy(force: bool = False):
    if _import_state["running"]:
        raise HTTPException(409, "import already running")

    def work():
        from ...taxonomy.importer import TaxonomyImporter
        _import_state["running"] = True
        started = dt.datetime.now(dt.timezone.utc)
        try:
            with session_scope() as s:
                stats = TaxonomyImporter(s, get_settings().data_root, force=force).run()
                src = s.execute(select(Source).where(Source.slug == "emithran-taxonomy")).scalar_one_or_none()
                if src:
                    src.last_run_at = src.last_success_at = dt.datetime.now(dt.timezone.utc)
            _import_state["last"] = {"started": started.isoformat(), "ok": True, "stats": stats}
        except Exception as exc:
            _import_state["last"] = {"started": started.isoformat(), "ok": False, "error": str(exc)}
        finally:
            _import_state["running"] = False

    threading.Thread(target=work, daemon=True).start()
    return {"started": True}


@router.get("/admin/import-taxonomy")
def import_status():
    return _import_state
