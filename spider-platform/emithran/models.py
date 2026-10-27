"""Relational model.

Design principles
-----------------
* Original values are never overwritten: every normalized row keeps ``original`` (raw JSON)
  plus a provenance pointer (``source_id`` + ``source_url`` or ``dataset_id`` + row index).
* ``Observation`` is the single fact table for numeric/time-series data (prices, FX,
  indicators, machine/labour rates, properties, trade values).
* ``status`` separates verified data from uncertain data:
  verified | unverified | quarantined | superseded.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


TS = DateTime(timezone=True)


# --------------------------------------------------------------------------- taxonomy
class Category(Base):
    """A manufacturing category, one per folder in the existing taxonomy."""

    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    original_folder: Mapped[str | None] = mapped_column(String(255))  # preserved exactly
    tree_node_name: Mapped[str | None] = mapped_column(String(255))  # digital_factory_tree.csv node
    family: Mapped[str] = mapped_column(String(60), default="other")  # forming, casting, plastics...
    description: Mapped[str | None] = mapped_column(Text)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    dataset_count: Mapped[int] = mapped_column(Integer, default=0)
    row_count: Mapped[int] = mapped_column(Integer, default=0)


class TreeNode(Base):
    """Verbatim import of digital_factory_tree.csv."""

    __tablename__ = "tree_nodes"
    id: Mapped[int] = mapped_column(primary_key=True)
    level: Mapped[int] = mapped_column(Integer)
    parent_name: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255))
    node_type: Mapped[str] = mapped_column(String(40))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))


class Dataset(Base):
    """One imported file from the existing folder structure."""

    __tablename__ = "datasets"
    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), index=True)
    rel_path: Mapped[str] = mapped_column(String(600), unique=True)
    file_type: Mapped[str] = mapped_column(String(10))
    role: Mapped[str] = mapped_column(String(40), index=True)  # materials, machines, processes ...
    country_iso3: Mapped[str | None] = mapped_column(String(3))
    columns: Mapped[list[str]] = mapped_column(JSON, default=list)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    checksum: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    imported_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    notes: Mapped[str | None] = mapped_column(Text)
    category = relationship("Category")


class DatasetRow(Base):
    __tablename__ = "dataset_rows"
    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    row_index: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)


class Country(Base):
    __tablename__ = "countries"
    iso3: Mapped[str] = mapped_column(String(3), primary_key=True)
    iso2: Mapped[str | None] = mapped_column(String(2))
    name: Mapped[str] = mapped_column(String(120))
    currency: Mapped[str | None] = mapped_column(String(3))
    region: Mapped[str | None] = mapped_column(String(60))
    is_region: Mapped[bool] = mapped_column(Boolean, default=False)


class Material(Base):
    __tablename__ = "materials"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(200), unique=True, index=True)  # normalized identity key
    name: Mapped[str] = mapped_column(String(255))  # preferred display (original casing)
    original_names: Mapped[list[str]] = mapped_column(JSON, default=list)
    family: Mapped[str] = mapped_column(String(40), index=True)  # steel, stainless_steel, aluminium ...
    material_type: Mapped[str | None] = mapped_column(String(120))  # as in source ("Material Type")
    grade: Mapped[str | None] = mapped_column(String(120))
    designations: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)  # usa/din/en/gb/jis/european
    cut_code: Mapped[str | None] = mapped_column(String(80))
    density_kg_m3: Mapped[float | None] = mapped_column(Float)
    properties: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # normalized key -> {value, unit, original}


class MaterialProcess(Base):
    """Material <-> manufacturing category link, with the evidence dataset."""

    __tablename__ = "material_processes"
    __table_args__ = (UniqueConstraint("material_id", "category_id", "dataset_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id", ondelete="CASCADE"), index=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), index=True)
    dataset_id: Mapped[int | None] = mapped_column(ForeignKey("datasets.id"))
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # e.g. {"Can Die Cast": true}


class Machine(Base):
    __tablename__ = "machines"
    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), index=True)
    dataset_id: Mapped[int | None] = mapped_column(ForeignKey("datasets.id"))
    name: Mapped[str] = mapped_column(String(300))
    process_name: Mapped[str | None] = mapped_column(String(200))
    country_iso3: Mapped[str | None] = mapped_column(String(3))
    labor_rate_usd_hr: Mapped[float | None] = mapped_column(Float)
    direct_overhead_usd_hr: Mapped[float | None] = mapped_column(Float)
    indirect_overhead_usd_hr: Mapped[float | None] = mapped_column(Float)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Process(Base):
    __tablename__ = "processes"
    __table_args__ = (UniqueConstraint("category_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    normalized_name: Mapped[str] = mapped_column(String(255), index=True)
    default_machine: Mapped[str | None] = mapped_column(String(255))
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Standard(Base):
    __tablename__ = "standards"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(120), index=True)
    title: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(String(40))  # ISO, ASTM, BIS ...
    status: Mapped[str | None] = mapped_column(String(60))
    url: Mapped[str | None] = mapped_column(String(600))
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"))
    dataset_id: Mapped[int | None] = mapped_column(ForeignKey("datasets.id"))
    categories: Mapped[list[str]] = mapped_column(JSON, default=list)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    __table_args__ = (UniqueConstraint("code", "body"),)


class UnresolvedIdentifier(Base):
    """Identifiers such as MHR / LHR that must NOT be interpreted until confirmed."""

    __tablename__ = "unresolved_identifiers"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="unresolved")  # unresolved | confirmed
    confirmed_definition: Mapped[str | None] = mapped_column(Text)
    confirmed_by: Mapped[str | None] = mapped_column(String(120))
    confirmed_at: Mapped[dt.datetime | None] = mapped_column(TS)
    occurrences: Mapped[int] = mapped_column(Integer, default=0)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)


# --------------------------------------------------------------------------- sources & crawling
class Source(Base):
    __tablename__ = "sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    base_url: Mapped[str] = mapped_column(String(600))
    website_category: Mapped[str] = mapped_column(String(60))  # government, intergovernmental, research, news ...
    source_type: Mapped[str] = mapped_column(String(20))  # api|html|sitemap|rss|csv|xlsx|pdf|internal
    data_kinds: Mapped[list[str]] = mapped_column(JSON, default=list)  # price, fx, indicator, publication ...
    categories: Mapped[list[str]] = mapped_column(JSON, default=list)  # manufacturing category slugs ("*" = all)
    materials: Mapped[list[str]] = mapped_column(JSON, default=list)
    countries: Mapped[list[str]] = mapped_column(JSON, default=list)
    processes: Mapped[list[str]] = mapped_column(JSON, default=list)
    # access & licensing
    access_method: Mapped[str] = mapped_column(String(120))
    auth_env_var: Mapped[str | None] = mapped_column(String(80))
    license: Mapped[str | None] = mapped_column(String(160))
    license_url: Mapped[str | None] = mapped_column(String(600))
    commercial_use: Mapped[str] = mapped_column(String(20), default="unknown")  # yes|no|conditional|unknown
    attribution: Mapped[str | None] = mapped_column(Text)
    verification_status: Mapped[str] = mapped_column(String(30), default="planned")
    verified_at: Mapped[dt.date | None] = mapped_column(Date)
    update_frequency: Mapped[str | None] = mapped_column(String(60))
    reliability: Mapped[float] = mapped_column(Float, default=0.5)  # 0..1
    # crawl configuration
    status: Mapped[str] = mapped_column(String(20), default="paused")  # active|paused|disabled
    schedule_interval_minutes: Mapped[int | None] = mapped_column(Integer)
    schedule_cron: Mapped[str | None] = mapped_column(String(80))
    concurrency: Mapped[int] = mapped_column(Integer, default=2)
    download_delay: Mapped[float] = mapped_column(Float, default=1.0)
    max_pages: Mapped[int] = mapped_column(Integer, default=200)
    max_depth: Mapped[int] = mapped_column(Integer, default=2)
    respect_robots: Mapped[bool] = mapped_column(Boolean, default=True)
    render_js: Mapped[bool] = mapped_column(Boolean, default=False)
    resumable: Mapped[bool] = mapped_column(Boolean, default=False)
    allowed_domains: Mapped[list[str]] = mapped_column(JSON, default=list)
    allow_patterns: Mapped[list[str]] = mapped_column(JSON, default=list)
    deny_patterns: Mapped[list[str]] = mapped_column(JSON, default=list)
    start_urls: Mapped[list[str]] = mapped_column(JSON, default=list)
    sitemap_urls: Mapped[list[str]] = mapped_column(JSON, default=list)
    pagination: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    extraction: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # {"parser": ..., options}
    validation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    backfill: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    freshness_sla_hours: Mapped[int] = mapped_column(Integer, default=48)
    notes: Mapped[str | None] = mapped_column(Text)
    # run state
    last_run_at: Mapped[dt.datetime | None] = mapped_column(TS)
    last_success_at: Mapped[dt.datetime | None] = mapped_column(TS)
    last_records: Mapped[int] = mapped_column(Integer, default=0)
    total_records: Mapped[int] = mapped_column(Integer, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)


class CrawlJob(Base):
    __tablename__ = "crawl_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    trigger: Mapped[str] = mapped_column(String(20), default="manual")  # manual|schedule|backfill|retry
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow, index=True)
    started_at: Mapped[dt.datetime | None] = mapped_column(TS)
    finished_at: Mapped[dt.datetime | None] = mapped_column(TS)
    heartbeat_at: Mapped[dt.datetime | None] = mapped_column(TS)
    pid: Mapped[int | None] = mapped_column(Integer)
    worker: Mapped[str | None] = mapped_column(String(120))
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    source = relationship("Source")


class CrawlUrl(Base):
    """Per-URL checkpoint: discovery, conditional-GET validators and content hash."""

    __tablename__ = "crawl_urls"
    __table_args__ = (UniqueConstraint("source_id", "url_hash"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(Text)
    url_hash: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="discovered")  # discovered|fetched|not_modified|unchanged|failed|skipped
    depth: Mapped[int] = mapped_column(Integer, default=0)
    http_status: Mapped[int | None] = mapped_column(Integer)
    etag: Mapped[str | None] = mapped_column(String(300))
    last_modified: Mapped[str | None] = mapped_column(String(120))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    first_seen: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    last_fetched: Mapped[dt.datetime | None] = mapped_column(TS)
    fetch_count: Mapped[int] = mapped_column(Integer, default=0)
    items_extracted: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    last_job_id: Mapped[int | None] = mapped_column(Integer)


class CrawlLog(Base):
    __tablename__ = "crawl_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("crawl_jobs.id", ondelete="CASCADE"), index=True)
    ts: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    level: Mapped[str] = mapped_column(String(10))
    message: Mapped[str] = mapped_column(Text)


# --------------------------------------------------------------------------- facts
class Observation(Base):
    __tablename__ = "observations"
    __table_args__ = (
        UniqueConstraint("fingerprint"),
        Index("ix_obs_series", "series_key", "period_date"),
        Index("ix_obs_kind_status", "kind", "status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(30))  # price|fx|indicator|labor_rate|machine_rate|property|trade
    series_key: Mapped[str] = mapped_column(String(300))  # stable key for history (source+entity+metric+country)
    fingerprint: Mapped[str] = mapped_column(String(64))  # dedupe key (series+period+value)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"), index=True)
    dataset_id: Mapped[int | None] = mapped_column(ForeignKey("datasets.id"), index=True)
    material_id: Mapped[int | None] = mapped_column(ForeignKey("materials.id"), index=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), index=True)
    country_iso3: Mapped[str | None] = mapped_column(String(3), index=True)
    entity: Mapped[str] = mapped_column(String(300))  # original entity label (material name, currency pair...)
    metric: Mapped[str] = mapped_column(String(160))
    value: Mapped[float | None] = mapped_column(Float)
    value_text: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(60))  # original unit
    currency: Mapped[str | None] = mapped_column(String(3))  # original currency
    value_usd: Mapped[float | None] = mapped_column(Float)  # normalized (USD per normalized unit)
    unit_normalized: Mapped[str | None] = mapped_column(String(40))
    period_date: Mapped[dt.date | None] = mapped_column(Date)
    observed_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)  # extraction timestamp
    source_url: Mapped[str | None] = mapped_column(Text)
    original: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="unverified")
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    quality_flags: Mapped[list[str]] = mapped_column(JSON, default=list)
    job_id: Mapped[int | None] = mapped_column(Integer)


class Document(Base):
    """Publications, news, reports, standards metadata pages."""

    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))  # publication|news|report|standard
    url: Mapped[str] = mapped_column(String(1000), unique=True)
    title: Mapped[str] = mapped_column(Text)
    published_at: Mapped[dt.datetime | None] = mapped_column(TS)
    authors: Mapped[list[str]] = mapped_column(JSON, default=list)
    summary: Mapped[str | None] = mapped_column(Text)
    text_excerpt: Mapped[str | None] = mapped_column(Text)
    categories: Mapped[list[str]] = mapped_column(JSON, default=list)
    doi: Mapped[str | None] = mapped_column(String(200))
    license: Mapped[str | None] = mapped_column(String(120))
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    observed_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    job_id: Mapped[int | None] = mapped_column(Integer)


class QualityIssue(Base):
    __tablename__ = "quality_issues"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow, index=True)
    severity: Mapped[str] = mapped_column(String(10))  # info|warning|error
    code: Mapped[str] = mapped_column(String(60), index=True)
    message: Mapped[str] = mapped_column(Text)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"))
    dataset_id: Mapped[int | None] = mapped_column(ForeignKey("datasets.id"))
    observation_id: Mapped[int | None] = mapped_column(ForeignKey("observations.id", ondelete="CASCADE"))
    job_id: Mapped[int | None] = mapped_column(Integer)
    context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class AlertRule(Base):
    __tablename__ = "alert_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(30))  # price_change|fx_change|stale_source|source_failure
    series_key: Mapped[str | None] = mapped_column(String(300))
    material_id: Mapped[int | None] = mapped_column(ForeignKey("materials.id"))
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"))
    threshold_pct: Mapped[float | None] = mapped_column(Float)
    window_days: Mapped[int] = mapped_column(Integer, default=30)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)


class AlertEvent(Base):
    __tablename__ = "alert_events"
    __table_args__ = (UniqueConstraint("rule_id", "dedupe_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("alert_rules.id", ondelete="CASCADE"), index=True)
    triggered_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    dedupe_key: Mapped[str] = mapped_column(String(200))
    severity: Mapped[str] = mapped_column(String(10), default="warning")
    message: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)


# --------------------------------------------------------------------------- agents (Emithran Brain)
class AgentRun(Base):
    """One orchestrated run of the specialist agents."""

    __tablename__ = "agent_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    trigger: Mapped[str] = mapped_column(String(20), default="manual")  # manual|schedule
    status: Mapped[str] = mapped_column(String(20), default="running", index=True)  # running|succeeded|partial|failed
    mode: Mapped[str] = mapped_column(String(10), default="rules")  # rules|llm|mixed
    started_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow, index=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(TS)
    agents: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # per agent: status, findings, mode, model, seconds, error
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # risk scores, counts, brief
    error: Mapped[str | None] = mapped_column(Text)


class AgentFinding(Base):
    """A flag raised by an agent. Upserted by (agent, key) so acknowledgements survive later runs."""

    __tablename__ = "agent_findings"
    __table_args__ = (UniqueConstraint("agent", "key"), Index("ix_findings_status_sev", "status", "severity"))
    id: Mapped[int] = mapped_column(primary_key=True)
    agent: Mapped[str] = mapped_column(String(40), index=True)
    key: Mapped[str] = mapped_column(String(200))  # stable identity of the issue, e.g. "commodity:copper:uptrend"
    kind: Mapped[str] = mapped_column(String(20))  # risk|forecast|data_gap|opinion|challenge
    title: Mapped[str] = mapped_column(String(300))
    severity: Mapped[str] = mapped_column(String(10), default="info")  # info|low|medium|high|critical
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    summary: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)  # every number the finding rests on
    entities: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # categories / materials / countries affected
    forecast: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)  # e.g. "commodity:aluminium" (cross-agent corroboration)
    analysis: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # mode, model, LLM opinion, corroboration, challenges
    contested: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(12), default="open", index=True)  # open|ack|dismissed|resolved
    first_seen: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    last_seen: Mapped[dt.datetime] = mapped_column(TS, default=utcnow, index=True)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(TS)
    run_id: Mapped[int | None] = mapped_column(Integer)


class AgentEvent(Base):
    """Append-only history of what the agents did: a flag appeared, got worse, got better, resolved or came back."""

    __tablename__ = "agent_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(TS, default=utcnow, index=True)
    run_id: Mapped[int | None] = mapped_column(Integer, index=True)
    finding_id: Mapped[int | None] = mapped_column(Integer, index=True)
    agent: Mapped[str] = mapped_column(String(40), index=True)
    event: Mapped[str] = mapped_column(String(16))  # new|escalated|deescalated|resolved|reopened
    title: Mapped[str] = mapped_column(String(300))
    severity: Mapped[str] = mapped_column(String(10))
    prev_severity: Mapped[str | None] = mapped_column(String(10))


# --------------------------------------------------------------------------- public API (v2)
class ApiClient(Base):
    """OAuth2 client (client-credentials grant). The secret is stored only as a SHA-256 hash."""

    __tablename__ = "api_clients"
    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text)
    owner: Mapped[str | None] = mapped_column(String(160))
    secret_hash: Mapped[str] = mapped_column(String(64))
    secret_hint: Mapped[str] = mapped_column(String(12))  # last 4 chars for identification
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, default=600)
    token_ttl_seconds: Mapped[int] = mapped_column(Integer, default=3600)
    created_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    secret_rotated_at: Mapped[dt.datetime | None] = mapped_column(TS)
    last_token_at: Mapped[dt.datetime | None] = mapped_column(TS)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(TS)


class ApiKey(Base):
    """Personal/service API key (Bearer emk_…). Only prefix + SHA-256 hash are stored."""

    __tablename__ = "api_keys"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    owner: Mapped[str | None] = mapped_column(String(160))
    prefix: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    key_hash: Mapped[str] = mapped_column(String(64))
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, default=600)
    created_at: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    expires_at: Mapped[dt.datetime | None] = mapped_column(TS)
    last_used_at: Mapped[dt.datetime | None] = mapped_column(TS)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(TS)


class ApiRequestLog(Base):
    __tablename__ = "api_request_log"
    __table_args__ = (Index("ix_apireq_ts", "ts"), Index("ix_apireq_principal", "principal_type", "principal_id"))
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(TS, default=utcnow)
    principal_type: Mapped[str] = mapped_column(String(12))  # client | key | admin | anonymous
    principal_id: Mapped[str | None] = mapped_column(String(64))
    method: Mapped[str] = mapped_column(String(8))
    route: Mapped[str] = mapped_column(String(200))
    status: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[float] = mapped_column(Float)
    error_name: Mapped[str | None] = mapped_column(String(80))
    error_instance_id: Mapped[str | None] = mapped_column(String(40))
