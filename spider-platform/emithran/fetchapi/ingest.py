"""Turn pages fetched by an external engine into stored records.

Mirrors ``RegistrySpider.parse_response`` (same parsers, validation, checkpoints and scope rules) but is
driven by pages posted from the fetcher instead of the Scrapy downloader.
"""
from __future__ import annotations

import base64
import datetime as dt
import re
from collections import Counter
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field
from scrapy.http import Request, TextResponse
from scrapy.responsetypes import responsetypes

from ..crawler.parsers import PARSERS, Follow, ParseContext
from ..crawler.spiders.registry_spider import canonicalize, next_page_urls
from ..crawler.writer import persist_items, upsert_crawl_urls, validate
from ..db import session_scope
from ..models import CrawlJob, CrawlLog

# persist_items counter name → job.stats key (same mapping the Scrapy monitor applies)
STAT_NAME = {"items_inserted": "items_stored", "items_revised": "items_revised", "items_duplicate": "items_duplicate",
             "items_quarantined": "items_quarantined", "items_error": "items_error", "items_filtered": "items_filtered", "items_invalid": "items_invalid",
             "items_extracted": "items_extracted", "processing_errors": "processing_errors"}

# Stats owned by the fetcher (it reports totals); everything else is counted here.
FETCHER_STATS = {"urls_discovered", "urls_queued", "pages_fetched", "pages_unchanged", "not_modified", "requests_failed",
                 "retries", "urls_duplicate", "urls_over_budget", "http_errors", "bytes"}


class PageIn(BaseModel):
    url: str
    depth: int = 0
    page: int = 1
    parser: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    status: int = 0  # HTTP status; 0 = transport error (no response)
    content_type: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    body_b64: str | None = None
    content_hash: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    fetched_at: dt.datetime | None = None
    not_modified: bool = False
    unchanged: bool = False  # body hash equals the stored checkpoint; body is omitted
    error: str | None = None
    replayed: bool = False
    replay: dict[str, Any] = Field(default_factory=dict)


class Scope:
    """Which discovered links may be followed (same rules as ``RegistrySpider.in_scope``)."""

    def __init__(self, source: dict[str, Any]):
        self.domains = list(source.get("allowed_domains") or [])
        self.max_depth = int(source.get("max_depth") or 0)
        self.allow = [re.compile(p) for p in source.get("allow_patterns") or []]
        self.deny = [re.compile(p) for p in source.get("deny_patterns") or []]

    def reason(self, url: str, depth: int, *, explicit: bool) -> str | None:
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            return "scheme"
        host = (p.hostname or "").lower()
        if self.domains and not any(host == d or host.endswith("." + d) for d in self.domains):
            return "offsite"
        if depth > self.max_depth:
            return "depth"
        if any(rx.search(url) for rx in self.deny):
            return "denied"
        if self.allow and not explicit and not any(rx.search(url) for rx in self.allow):
            return "pattern"
        return None


def _response(page: PageIn, meta: dict[str, Any]):
    body = base64.b64decode(page.body_b64) if page.body_b64 else b""
    headers = {"Content-Type": page.content_type, **page.headers} if page.content_type else dict(page.headers)
    cls = responsetypes.from_args(headers=headers, url=page.url, body=body)
    kw = {"encoding": "utf-8"} if issubclass(cls, TextResponse) else {}
    return cls(url=page.url, status=page.status, headers=headers, body=body, request=Request(page.url, meta=meta), **kw)


def ingest_pages(job_id: int, pages: list[PageIn]) -> dict[str, Any]:
    """Parse + store a batch. Returns ``{"follow": [...], "stats": {...}}``; one transaction per batch."""
    from ..crawler.spiders.registry_spider import snapshot

    follow: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    logs: list[tuple[str, str]] = []
    updates: dict[str, dict[str, Any]] = {}
    items: list[dict[str, Any]] = []
    with session_scope() as s:
        job = s.get(CrawlJob, job_id)
        if job is None:
            raise LookupError(f"job {job_id} not found")
        src = snapshot(job.source)
        scope = Scope(src)
        opts = dict(src.get("extraction") or {})
        ctx = ParseContext(src, opts, job_id, dict(job.params or {}))
        validation = src.get("validation") or {}
        pg = src.get("pagination") or {}

        for page in pages:
            url = page.url
            upd = updates.setdefault(url, {"url": url, "depth": page.depth})
            if page.fetched_at:
                upd.update(http_status=page.status or None, etag=page.etag, last_modified=page.last_modified,
                           content_hash=page.content_hash, last_fetched=page.fetched_at, last_job_id=job_id)
            if page.not_modified:
                upd.update(status="not_modified", http_status=304)
                continue
            if page.error or page.status == 0 or page.status >= 400:
                msg = page.error or f"HTTP {page.status}"
                upd.update(status="failed", error=msg[:500], last_job_id=job_id)
                logs.append(("warning" if page.status else "error", f"{msg} for {url}" if page.status >= 400 else
                             f"request failed {url}: {msg}"))
                continue
            if page.unchanged:
                upd["status"] = "unchanged"
                continue
            upd["status"] = "fetched"
            meta = {"depth": page.depth, "parser": page.parser, "page": page.page, "replayed": page.replayed,
                    "replay": page.replay, **page.meta}
            response = _response(page, meta)
            parser = PARSERS.get(page.parser or opts.get("parser") or "")
            if parser is None:
                counts["processing_errors"] += 1
                upd.update(status="failed", error="source has no runnable parser")
                logs.append(("error", f"no runnable parser for {url}"))
                continue
            n = 0
            try:
                for out in parser(response, ctx):
                    if isinstance(out, Follow):
                        follow += _follow(scope, out.url, page.depth + 1, parser=out.parser, meta=out.meta,
                                          priority=out.priority, counts=counts)
                        continue
                    out["_url"] = url
                    out["_replayed"] = page.replayed
                    if page.replayed:
                        out.setdefault("original", {})["replay"] = page.replay
                    n += 1
                    counts["items_extracted"] += 1
                    err = validate(out, validation)
                    if err:
                        counts["items_invalid"] += 1
                        out = {**out, "_invalid": err}
                    items.append(out)
            except Exception as exc:
                counts["processing_errors"] += 1
                upd.update(status="failed", error=f"parse error: {exc}"[:500])
                logs.append(("error", f"parse error on {url}: {exc}"))
                continue
            upd["items_extracted"] = n
            if pg and page.page == 1 and page.parser in (None, opts.get("parser")):
                for nxt, num in next_page_urls(pg, response):
                    follow += _follow(scope, nxt, page.depth, meta={}, page=num, pagination=True, counts=counts)

        persist_items(s, items, source_id=src["id"], job_id=job_id, inc=lambda name: counts.update([name]))
        upsert_crawl_urls(s, src["id"], updates)
        stats = dict(job.stats or {})
        for k, v in counts.items():
            key = STAT_NAME.get(k, k)
            stats[key] = int(stats.get(key, 0)) + v
        job.stats = stats
        job.heartbeat_at = dt.datetime.now(dt.timezone.utc)
        for level, msg in logs:
            s.add(CrawlLog(job_id=job_id, level=level, message=msg[:2000]))
        out_stats = {STAT_NAME.get(k, k): v for k, v in counts.items()}
    return {"follow": follow, "stats": out_stats}


def _follow(scope: Scope, url: str, depth: int, *, parser: str | None = None, meta: dict | None = None,
            priority: int = 0, page: int = 1, pagination: bool = False, counts: Counter) -> list[dict[str, Any]]:
    url = canonicalize(url)
    reason = scope.reason(url, depth, explicit=pagination)
    if reason:
        counts["urls_out_of_scope"] += 1
        return []
    return [{"url": url, "depth": depth, "parser": parser, "meta": meta or {}, "priority": priority, "page": page}]
