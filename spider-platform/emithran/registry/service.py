"""Source registry: validation, seeding, registration and freshness evaluation.

New sources are registered through ``SourceConfig`` (API: POST /api/sources). No pipeline code
changes are needed: a source is fully described by its type, scope, pagination and an
``extraction`` block naming a parser from ``emithran.crawler.parsers.PARSERS`` (or the generic
``html_css`` / ``html_table`` / ``article`` / ``rss`` / ``csv_table`` parsers configured by options).
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import CrawlJob, Observation, Source

SEED_FILE = Path(__file__).with_name("sources.yaml")

SourceType = Literal["api", "html", "sitemap", "rss", "csv", "xlsx", "pdf", "internal"]
Verification = Literal["verified", "url_verified", "documented", "key_required", "planned", "blocked_by_terms"]
RUNNABLE_VERIFICATION = {"verified", "url_verified", "documented", "key_required"}


class SourceConfig(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9\-]{1,78}$")
    name: str
    base_url: str
    website_category: str = "other"
    source_type: SourceType
    data_kinds: list[str] = []
    categories: list[str] = []
    materials: list[str] = []
    countries: list[str] = []
    processes: list[str] = []
    access_method: str = "HTTPS GET"
    auth_env_var: str | None = None
    license: str | None = None
    license_url: str | None = None
    commercial_use: Literal["yes", "no", "conditional", "unknown"] = "unknown"
    attribution: str | None = None
    verification_status: Verification = "planned"
    verified_at: dt.date | None = None
    update_frequency: str | None = None
    reliability: float = Field(default=0.5, ge=0, le=1)
    status: Literal["active", "paused", "disabled"] = "paused"
    schedule_interval_minutes: int | None = Field(default=None, ge=15)
    schedule_cron: str | None = None
    concurrency: int = Field(default=1, ge=1, le=16)
    download_delay: float = Field(default=1.0, ge=0.0, le=120)
    max_pages: int = Field(default=50, ge=1, le=100000)
    max_depth: int = Field(default=1, ge=0, le=10)
    respect_robots: bool = True
    render_js: bool = False
    resumable: bool = False
    allowed_domains: list[str] = []
    allow_patterns: list[str] = []
    deny_patterns: list[str] = []
    start_urls: list[str] = []
    sitemap_urls: list[str] = []
    pagination: dict[str, Any] = {}
    extraction: dict[str, Any] = {}
    validation: dict[str, Any] = {}
    backfill: dict[str, Any] = {}
    freshness_sla_hours: int = Field(default=72, ge=1)
    notes: str | None = None

    @field_validator("allow_patterns", "deny_patterns")
    @classmethod
    def _regex_ok(cls, v: list[str]) -> list[str]:
        for p in v:
            re.compile(p)
        return v

    @field_validator("schedule_cron")
    @classmethod
    def _cron_ok(cls, v: str | None) -> str | None:
        if v:
            from apscheduler.triggers.cron import CronTrigger

            CronTrigger.from_crontab(v, timezone="UTC")
        return v

    @model_validator(mode="after")
    def _consistency(self):
        if self.status == "active" and self.verification_status not in RUNNABLE_VERIFICATION:
            raise ValueError(f"source with verification_status={self.verification_status} cannot be active")
        generated = bool((self.extraction or {}).get("url_generator"))            # e.g. gdelt_topics builds its URLs at plan time
        if self.source_type != "internal" and self.status == "active" and not (self.start_urls or self.sitemap_urls or generated):
            raise ValueError("active sources need start_urls, sitemap_urls or an extraction.url_generator")
        if self.source_type != "internal" and not self.allowed_domains:
            from urllib.parse import urlparse

            hosts = {urlparse(u).hostname for u in self.start_urls + self.sitemap_urls + [self.base_url]}
            self.allowed_domains = sorted(h for h in hosts if h)
        if self.extraction.get("parser") not in (None, "none"):
            from ..crawler.parsers import PARSERS

            if self.extraction["parser"] not in PARSERS:
                raise ValueError(f"unknown parser {self.extraction['parser']!r}; available: {sorted(PARSERS)}")
        return self


def _apply(src: Source, cfg: SourceConfig) -> None:
    for k, v in cfg.model_dump().items():
        setattr(src, k, v)


def load_seed() -> list[SourceConfig]:
    doc = yaml.safe_load(SEED_FILE.read_text(encoding="utf-8"))
    defaults = doc.get("defaults", {})
    return [SourceConfig(**{**defaults, **s}) for s in doc["sources"]]


def seed_sources(s: Session, update_existing: bool = False) -> dict[str, int]:
    out = {"created": 0, "updated": 0, "unchanged": 0}
    for cfg in load_seed():
        src = s.execute(select(Source).where(Source.slug == cfg.slug)).scalar_one_or_none()
        if src is None:
            src = Source()
            _apply(src, cfg)
            s.add(src)
            out["created"] += 1
        elif update_existing:
            status = src.status  # keep operator decision about active/paused
            _apply(src, cfg)
            src.status = status if cfg.verification_status in RUNNABLE_VERIFICATION else cfg.status
            out["updated"] += 1
        else:
            out["unchanged"] += 1
    # internal pseudo-source for the imported taxonomy
    if s.execute(select(Source).where(Source.slug == "emithran-taxonomy")).scalar_one_or_none() is None:
        s.add(Source(slug="emithran-taxonomy", name="Emithran taxonomy (memory/ folder)", base_url="file://memory",
                     website_category="internal", source_type="internal", data_kinds=["price", "machine_rate", "property"],
                     categories=["*"], access_method="Local import (python -m emithran.cli import-taxonomy)",
                     license="Internal", commercial_use="yes", verification_status="verified",
                     reliability=0.8, status="active", freshness_sla_hours=24 * 365,
                     notes="Imported from the existing folder structure; prices are a snapshot (see period flags)."))
        out["created"] += 1
    s.flush()
    return out


def register_source(s: Session, cfg: SourceConfig) -> Source:
    if s.execute(select(Source.id).where(Source.slug == cfg.slug)).first():
        raise ValueError(f"source {cfg.slug!r} already exists")
    src = Source()
    _apply(src, cfg)
    s.add(src)
    s.flush()
    return src


def update_source(s: Session, src: Source, patch: dict[str, Any]) -> Source:
    data = {k: getattr(src, k) for k in SourceConfig.model_fields}
    data.update(patch)
    cfg = SourceConfig(**data)
    _apply(src, cfg)
    s.flush()
    return src


def freshness(src: Source, now: dt.datetime | None = None) -> str:
    """fresh | stale | never | failing | n/a"""
    now = now or dt.datetime.now(dt.timezone.utc)
    if src.verification_status not in RUNNABLE_VERIFICATION and src.source_type != "internal":
        return "n/a"
    if src.consecutive_failures >= 3:
        return "failing"
    last = src.last_success_at
    if last is None:
        return "never"
    if last.tzinfo is None:
        last = last.replace(tzinfo=dt.timezone.utc)
    return "fresh" if (now - last).total_seconds() <= src.freshness_sla_hours * 3600 else "stale"


def refresh_freshness(s: Session) -> dict[str, int]:
    counts: dict[str, int] = {}
    for src in s.execute(select(Source)).scalars():
        f = freshness(src)
        counts[f] = counts.get(f, 0) + 1
    return counts


def source_summary(s: Session, src: Source) -> dict[str, Any]:
    obs = s.execute(select(func.count(Observation.id)).where(Observation.source_id == src.id)).scalar_one()
    running = s.execute(select(func.count(CrawlJob.id)).where(CrawlJob.source_id == src.id,
                                                               CrawlJob.status.in_(["queued", "running"]))).scalar_one()
    return {"observations": obs, "active_jobs": running, "freshness": freshness(src)}
