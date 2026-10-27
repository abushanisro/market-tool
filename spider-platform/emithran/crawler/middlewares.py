"""Downloader middlewares: replay, incremental conditional GET, exponential-backoff retry."""
from __future__ import annotations

import asyncio
import email.utils
import hashlib
import json
import logging
import random
import time
from pathlib import Path

from scrapy import signals
from scrapy.exceptions import IgnoreRequest, NotConfigured
from scrapy.http import Response, TextResponse
from scrapy.utils.response import response_status_message

log = logging.getLogger(__name__)

RETRY_HTTP = {408, 425, 429, 500, 502, 503, 504, 522, 524}


class ReplayMiddleware:
    """Serve recorded responses for real URLs (tests / offline demos).

    ``EMITHRAN_REPLAY_DIR`` must contain ``index.json``: {url: {"file": ..., "status": 200, "headers": {...}}}.
    URLs not in the index get a synthetic 404 — the replay never reaches the network.
    Every item produced from a replayed response is flagged ``replayed`` by the pipeline.
    """

    def __init__(self, root: Path):
        self.root = root
        self.index = json.loads((root / "index.json").read_text())

    @classmethod
    def from_crawler(cls, crawler):
        d = crawler.settings.get("EMITHRAN_REPLAY_DIR")
        if not d:
            raise NotConfigured
        return cls(Path(d))

    def process_request(self, request, spider=None):
        if request.url.endswith("/robots.txt") and request.url not in self.index:
            return Response(url=request.url, status=404, body=b"", request=request)
        rec = self.index.get(request.url)
        if rec is None:
            return Response(url=request.url, status=404, body=b"not recorded", request=request)
        body = (self.root / rec["file"]).read_bytes()
        headers = {"Content-Type": rec.get("content_type", "application/octet-stream"), **rec.get("headers", {})}
        request.meta["replayed"] = True
        request.meta["replay"] = {k: rec.get(k) for k in ("captured_at", "recorded_url", "note") if rec.get(k)}
        cls = TextResponse if any(t in headers["Content-Type"] for t in ("json", "xml", "html", "text", "csv")) else Response
        if "html" in headers["Content-Type"]:
            from scrapy.http import HtmlResponse
            cls = HtmlResponse
        return cls(url=request.url, status=rec.get("status", 200), headers=headers, body=body, request=request,
                   **({"encoding": "utf-8"} if cls is not Response else {}))


class IncrementalMiddleware:
    """Adds If-None-Match / If-Modified-Since from the per-URL checkpoint; 304 → skip."""

    def __init__(self, crawler):
        self.crawler = crawler

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler)

    def process_request(self, request, spider=None):
        spider = spider or self.crawler.spider
        if request.meta.get("force_refresh") or not hasattr(spider, "checkpoints"):
            return None
        cp = spider.checkpoints.get(request.url)
        if cp:
            if cp.get("etag"):
                request.headers.setdefault("If-None-Match", cp["etag"])
            if cp.get("last_modified"):
                request.headers.setdefault("If-Modified-Since", cp["last_modified"])
        return None

    def process_response(self, request, response, spider=None):
        spider = spider or self.crawler.spider
        if response.status == 304:
            spider.record_url(request.url, status="not_modified", http_status=304, depth=request.meta.get("depth", 0))
            self.crawler.stats.inc_value("emithran/not_modified")
            raise IgnoreRequest("not modified")
        return response


class BackoffRetryMiddleware:
    """Bounded retries with exponential backoff + jitter, honouring Retry-After (replaces RetryMiddleware)."""

    def __init__(self, crawler):
        s = crawler.settings
        self.crawler = crawler
        self.max_retries = s.getint("EMITHRAN_MAX_RETRIES", 4)
        self.base = s.getfloat("EMITHRAN_BACKOFF_BASE", 2.0)
        self.cap = s.getfloat("EMITHRAN_BACKOFF_CAP", 120.0)

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler)

    def _delay(self, attempt: int, response=None) -> float:
        if response is not None:
            ra = response.headers.get("Retry-After")
            if ra:
                ra = ra.decode()
                try:
                    return min(self.cap, float(ra))
                except ValueError:
                    dt = email.utils.parsedate_to_datetime(ra)
                    if dt:
                        return max(0.0, min(self.cap, dt.timestamp() - time.time()))
        return min(self.cap, self.base * (2 ** attempt)) * (0.5 + random.random() / 2)

    async def _retry(self, request, reason, response=None):
        attempt = request.meta.get("emithran_retry", 0)
        stats = self.crawler.stats
        if attempt >= self.max_retries or request.meta.get("dont_retry"):
            stats.inc_value("emithran/retry_exhausted")
            return None
        delay = self._delay(attempt, response)
        stats.inc_value("emithran/retries")
        log.info("retry %s/%s in %.1fs for %s (%s)", attempt + 1, self.max_retries, delay, request.url, reason)
        await asyncio.sleep(delay)
        r = request.copy()
        r.meta["emithran_retry"] = attempt + 1
        r.dont_filter = True
        r.priority = request.priority - 1
        return r

    async def process_response(self, request, response, spider=None):
        if response.status in RETRY_HTTP:
            r = await self._retry(request, response_status_message(response.status), response)
            if r is not None:
                return r
        return response

    async def process_exception(self, request, exception, spider=None):
        if isinstance(exception, IgnoreRequest):
            return None
        return await self._retry(request, type(exception).__name__)


def url_hash(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()
