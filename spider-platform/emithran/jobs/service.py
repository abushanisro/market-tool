"""Job queue backed by the database (Postgres: SELECT … FOR UPDATE SKIP LOCKED)."""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..db import is_postgres
from ..models import CrawlJob, Source
from ..registry.service import RUNNABLE_VERIFICATION


class JobError(ValueError):
    pass


def get_source(s: Session, ref: str | int) -> Source:
    src = s.get(Source, int(ref)) if str(ref).isdigit() else s.execute(select(Source).where(Source.slug == ref)).scalar_one_or_none()
    if src is None:
        raise JobError(f"unknown source {ref!r}")
    return src


def create_job(s: Session, ref: str | int, *, trigger: str = "manual", params: dict[str, Any] | None = None) -> CrawlJob:
    src = get_source(s, ref)
    if src.source_type == "internal":
        raise JobError("internal sources are refreshed with `import-taxonomy`, not crawled")
    if src.verification_status not in RUNNABLE_VERIFICATION:
        raise JobError(f"source '{src.slug}' is {src.verification_status}; crawling is not permitted")
    if src.status == "disabled":
        raise JobError(f"source '{src.slug}' is disabled")
    if not (src.extraction or {}).get("parser") or src.extraction.get("parser") == "none":
        raise JobError(f"source '{src.slug}' has no parser configured")
    if src.auth_env_var and src.verification_status == "key_required":
        from ..config import get_settings
        if not get_settings().credential_present(src.auth_env_var):
            raise JobError(f"credential {src.auth_env_var} is not set")
    params = params or {}
    if params.get("backfill"):
        if not (src.backfill or {}).get("url_template"):
            raise JobError("this source has no backfill configuration")
        dt.date.fromisoformat(params["start_date"])
        trigger = "backfill"
    active = s.execute(select(CrawlJob).where(CrawlJob.source_id == src.id,
                                              CrawlJob.status.in_(["queued", "running"]))).scalars().first()
    if active is not None:
        raise JobError(f"job {active.id} for '{src.slug}' is already {active.status}")
    job = CrawlJob(source_id=src.id, trigger=trigger, params=params, status="queued", stats={})
    s.add(job)
    s.flush()
    return job


def claim_jobs(s: Session, limit: int, worker: str) -> list[CrawlJob]:
    if limit <= 0:
        return []
    q = select(CrawlJob).where(CrawlJob.status == "queued").order_by(CrawlJob.created_at).limit(limit)
    if is_postgres():
        q = q.with_for_update(skip_locked=True)
    jobs = list(s.execute(q).scalars())
    now = dt.datetime.now(dt.timezone.utc)
    for j in jobs:
        j.status, j.worker, j.started_at, j.heartbeat_at = "starting", worker, now, now
    s.flush()
    return jobs


def cancel_job(s: Session, job_id: int) -> CrawlJob:
    job = s.get(CrawlJob, job_id)
    if job is None:
        raise JobError("job not found")
    if job.status == "queued":
        job.status, job.finished_at = "cancelled", dt.datetime.now(dt.timezone.utc)
    elif job.status in ("running", "starting"):
        job.cancel_requested = True
    else:
        raise JobError(f"job is already {job.status}")
    s.flush()
    return job


def recover_stale(s: Session, stale_seconds: int) -> int:
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=stale_seconds)
    res = s.execute(update(CrawlJob).where(CrawlJob.status.in_(["running", "starting"]), CrawlJob.heartbeat_at < cutoff)
                    .values(status="failed", error="worker lost (no heartbeat)", finished_at=dt.datetime.now(dt.timezone.utc)))
    return res.rowcount or 0
