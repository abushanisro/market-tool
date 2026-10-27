"""``/api/fetcher`` — endpoints used by the Go fetch worker for one job at a time.

    GET  /jobs/{id}/plan       URLs, politeness settings and per-URL checkpoints
    POST /jobs/{id}/pages      fetched pages → parse, validate, store; returns links to follow
    POST /jobs/{id}/heartbeat  fetch-side stats + log lines; returns {"cancel": bool}
    POST /jobs/{id}/finish     final status via the same ``finalize`` the Scrapy engine uses
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..api.deps import require_admin
from ..config import get_settings
from ..crawler.run import _override, finalize
from ..crawler.spiders.registry_spider import canonicalize, snapshot, start_urls_for
from ..db import session_scope
from ..models import CrawlJob, CrawlLog, CrawlUrl
from .ingest import FETCHER_STATS, PageIn, ingest_pages

router = APIRouter(prefix="/api/fetcher", tags=["fetcher"], dependencies=[Depends(require_admin)])

ENGINE_KEY = "fetch_engine"  # source.extraction["fetch_engine"] == "go" routes the job to the Go worker


def unsupported(src: dict[str, Any]) -> str | None:
    """Features that need the Scrapy engine."""
    if src.get("render_js"):
        return "render_js sources need the Scrapy/Playwright engine"
    if src.get("sitemap_urls"):
        return "sitemap discovery is only implemented in the Scrapy engine"
    if (src.get("extraction") or {}).get("extract_patterns"):
        return "link-discovery (extract_patterns) is only implemented in the Scrapy engine"
    return None


def _active_job(s, job_id: int) -> CrawlJob:
    job = s.get(CrawlJob, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if job.status not in ("starting", "running"):
        raise HTTPException(409, f"job is {job.status}")
    return job


@router.get("/jobs/{job_id}/plan")
def plan(job_id: int) -> dict[str, Any]:
    cfg = get_settings()
    with session_scope() as s:
        job = _active_job(s, job_id)
        src = snapshot(job.source)
        if (why := unsupported(src)):
            raise HTTPException(422, why)
        now = dt.datetime.now(dt.timezone.utc)
        job.status, job.started_at, job.heartbeat_at = "running", job.started_at or now, now
        params = dict(job.params or {})
        urls = list(dict.fromkeys(canonicalize(u) for u in start_urls_for(src, params)))
        checkpoints = {
            c.url: {"etag": c.etag, "last_modified": c.last_modified, "content_hash": c.content_hash}
            for c in s.execute(select(CrawlUrl).where(CrawlUrl.source_id == src["id"])).scalars()
        }
        return {
            "job_id": job_id, "source": src["slug"], "force": bool(params.get("force_refresh")),
            "start_urls": [{"url": u, "depth": 0, "page": 1} for u in urls],
            "checkpoints": checkpoints,
            "policy": {
                "user_agent": cfg.user_agent, "contact_email": cfg.contact_email or "",
                "concurrency": max(1, src["concurrency"]), "download_delay": max(src["download_delay"], 0.0),
                "respect_robots": bool(src["respect_robots"]), "allowed_domains": src["allowed_domains"] or [],
                "max_pages": min(int(src["max_pages"] or 50), _override("global_max_pages_per_job", cfg.global_max_pages_per_job)),
                "timeout_seconds": _override("job_timeout_minutes", cfg.job_timeout_minutes) * 60,
                "accept": "application/json" if src["source_type"] == "api" else "",
                "max_retries": 4,
            },
        }


class PagesIn(BaseModel):
    pages: list[PageIn]


@router.post("/jobs/{job_id}/pages")
def pages(job_id: int, body: PagesIn) -> dict[str, Any]:
    with session_scope() as s:
        _active_job(s, job_id)
    return ingest_pages(job_id, body.pages)


class LogIn(BaseModel):
    level: str = "info"
    message: str
    ts: dt.datetime | None = None


class HeartbeatIn(BaseModel):
    stats: dict[str, int] | None = None                       # tolerate null from older workers: an empty heartbeat is still a heartbeat
    logs: list[LogIn] | None = None


def _merge_fetcher_stats(job: CrawlJob, stats: dict[str, int]) -> None:
    merged = dict(job.stats or {})
    merged.update({k: int(v) for k, v in stats.items() if k in FETCHER_STATS})
    job.stats = merged


@router.post("/jobs/{job_id}/heartbeat")
def heartbeat(job_id: int, body: HeartbeatIn) -> dict[str, bool]:
    with session_scope() as s:
        job = s.get(CrawlJob, job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        _merge_fetcher_stats(job, body.stats or {})
        job.heartbeat_at = dt.datetime.now(dt.timezone.utc)
        for entry in body.logs or []:
            s.add(CrawlLog(job_id=job_id, ts=entry.ts or job.heartbeat_at, level=entry.level[:10], message=entry.message[:2000]))
        return {"cancel": bool(job.cancel_requested) or job.status not in ("starting", "running")}


class FinishIn(BaseModel):
    reason: str = "finished"  # finished | closespider_pagecount | cancelled | exception | timeout
    error: str | None = None
    stats: dict[str, int] = Field(default_factory=dict)


@router.post("/jobs/{job_id}/finish")
def finish(job_id: int, body: FinishIn) -> dict[str, str]:
    with session_scope() as s:
        job = s.get(CrawlJob, job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        _merge_fetcher_stats(job, body.stats)
    return {"status": finalize(job_id, body.reason, error=body.error)}
