"""Scrapy extension: live job progress, heartbeat, cancellation, logs and URL checkpoints."""
from __future__ import annotations

import datetime as dt
import logging
from collections import deque

from scrapy import signals
from twisted.internet import task

from ..db import session_scope
from ..models import CrawlJob, CrawlLog
from .writer import upsert_crawl_urls

STAT_KEYS = {
    "emithran/urls_discovered": "urls_discovered", "emithran/urls_queued": "urls_queued", "emithran/pages_fetched": "pages_fetched",
    "emithran/pages_unchanged": "pages_unchanged", "emithran/not_modified": "not_modified", "emithran/items_extracted": "items_extracted",
    "emithran/items_inserted": "items_stored", "emithran/items_revised": "items_revised", "emithran/items_duplicate": "items_duplicate",
    "emithran/items_quarantined": "items_quarantined", "emithran/items_invalid": "items_invalid", "emithran/items_error": "items_error", "emithran/items_filtered": "items_filtered",
    "emithran/requests_failed": "requests_failed", "emithran/processing_errors": "processing_errors", "emithran/retries": "retries",
    "emithran/urls_duplicate": "urls_duplicate", "emithran/urls_over_budget": "urls_over_budget",
}


class _JobLogHandler(logging.Handler):
    def __init__(self, monitor: "JobMonitor"):
        super().__init__(level=logging.INFO)
        self.monitor = monitor

    def emit(self, record):
        if record.levelno >= logging.WARNING or record.name.startswith("emithran"):
            self.monitor.log(record.levelname.lower(), self.format(record)[:2000])


class JobMonitor:
    INTERVAL = 3.0

    def __init__(self, crawler):
        self.crawler = crawler
        self.logs: deque[tuple[dt.datetime, str, str]] = deque()
        self.loop: task.LoopingCall | None = None
        self.handler = _JobLogHandler(self)
        crawler.emithran_monitor = self

    @classmethod
    def from_crawler(cls, crawler):
        ext = cls(crawler)
        crawler.signals.connect(ext.opened, signal=signals.spider_opened)
        crawler.signals.connect(ext.closed, signal=signals.spider_closed)
        return ext

    def log(self, level: str, message: str) -> None:
        self.logs.append((dt.datetime.now(dt.timezone.utc), level, message))

    def opened(self, spider):
        self.spider = spider
        logging.getLogger().addHandler(self.handler)
        self.log("info", f"spider opened for source {spider.source['slug']} (job {spider.job_id})")
        self.loop = task.LoopingCall(self.tick)
        self.loop.start(self.INTERVAL, now=False)

    def stats(self) -> dict:
        raw = self.crawler.stats.get_stats()
        out = {v: int(raw.get(k, 0)) for k, v in STAT_KEYS.items()}
        out["urls_out_of_scope"] = sum(int(v) for k, v in raw.items() if k.startswith("emithran/urls_out_of_scope/"))
        out["http_errors"] = sum(int(v) for k, v in raw.items() if k.startswith("downloader/response_status_count/")
                                 and k.rsplit("/", 1)[-1][:1] in "45")
        out["bytes"] = int(raw.get("downloader/response_bytes", 0))
        return out

    def flush(self) -> bool:
        """Persist progress; returns True when cancellation was requested."""
        sp = self.spider
        updates, sp.url_updates = sp.url_updates, {}
        logs = [self.logs.popleft() for _ in range(len(self.logs))]
        with session_scope() as s:
            job = s.get(CrawlJob, sp.job_id)
            job.stats = {**(job.stats or {}), **self.stats()}
            job.heartbeat_at = dt.datetime.now(dt.timezone.utc)
            for ts, level, msg in logs:
                s.add(CrawlLog(job_id=sp.job_id, ts=ts, level=level, message=msg))
            upsert_crawl_urls(s, sp.source["id"], updates)
            return bool(job.cancel_requested)

    def tick(self):
        try:
            if self.flush():
                self.log("warning", "cancellation requested — closing spider")
                self.crawler.engine.close_spider(self.spider, "cancelled")
        except Exception:  # never kill the crawl because of a monitoring hiccup
            logging.getLogger("emithran.monitor").exception("monitor tick failed")

    def closed(self, spider, reason):
        if self.loop and self.loop.running:
            self.loop.stop()
        self.log("info", f"spider closed: {reason}")
        spider.close_reason = reason
        # pipeline flushes in close_spider; final stats are written by run.finalize()
        self.flush()
        logging.getLogger().removeHandler(self.handler)
