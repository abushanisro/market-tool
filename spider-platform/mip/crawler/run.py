"""Run one crawl job in the current process: ``python -m mip.crawler.run <job_id>``.

The worker launches each job as a separate OS process → failure isolation (a crashing or
hanging source cannot affect other jobs or the API) and a fresh Twisted reactor per job.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import sys
import traceback

from scrapy.crawler import CrawlerProcess
from scrapy.settings import Settings

from ..config import get_settings
from ..db import session_scope
from sqlalchemy import select

from ..models import CrawlJob, CrawlLog, Source

log = logging.getLogger("mip.crawler")


def _override(key: str, default):
    from ..models import AppSetting

    with session_scope() as s:
        row = s.get(AppSetting, key)
        return type(default)(row.value) if row is not None else default


def build_settings(src: Source, job: CrawlJob) -> Settings:
    cfg = get_settings()
    page_cap = _override("global_max_pages_per_job", cfg.global_max_pages_per_job)
    s = Settings()
    s.setdict({
        "BOT_NAME": "mip",
        "USER_AGENT": cfg.user_agent,
        "ROBOTSTXT_OBEY": bool(src.respect_robots),
        "CONCURRENT_REQUESTS": max(1, src.concurrency),
        "CONCURRENT_REQUESTS_PER_DOMAIN": max(1, src.concurrency),
        "DOWNLOAD_DELAY": max(src.download_delay, 0.0),
        "RANDOMIZE_DOWNLOAD_DELAY": True,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_START_DELAY": max(src.download_delay, 0.5),
        "AUTOTHROTTLE_MAX_DELAY": 60,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": max(1.0, src.concurrency / 2),
        "DOWNLOAD_TIMEOUT": 60,
        "DOWNLOAD_MAXSIZE": 200 * 1024 * 1024,
        "DEPTH_LIMIT": max(0, src.max_depth) + 1,
        "CLOSESPIDER_PAGECOUNT": min(src.max_pages, page_cap) + 5,
        "CLOSESPIDER_TIMEOUT": _override("job_timeout_minutes", cfg.job_timeout_minutes) * 60,
        "COOKIES_ENABLED": False,
        "TELNETCONSOLE_ENABLED": False,
        "LOG_LEVEL": "INFO",
        "HTTPERROR_ALLOW_ALL": True,  # parse_response records 4xx/5xx explicitly
        "RETRY_ENABLED": False,  # replaced by BackoffRetryMiddleware
        "MIP_MAX_RETRIES": 4,
        "TWISTED_REACTOR": "twisted.internet.asyncioreactor.AsyncioSelectorReactor",
        "REQUEST_FINGERPRINTER_IMPLEMENTATION": "2.7",
        "DOWNLOADER_MIDDLEWARES": {
            "mip.crawler.middlewares.ReplayMiddleware": 50,
            "mip.crawler.middlewares.IncrementalMiddleware": 560,
            "mip.crawler.middlewares.BackoffRetryMiddleware": 550,
        },
        "ITEM_PIPELINES": {"mip.crawler.pipelines.StorePipeline": 300},
        "EXTENSIONS": {"mip.crawler.monitor.JobMonitor": 500},
        "DEFAULT_REQUEST_HEADERS": {"Accept-Language": "en", "From": cfg.contact_email} if cfg.contact_email else {"Accept-Language": "en"},
    })
    if cfg.replay_dir:
        s.set("MIP_REPLAY_DIR", str(cfg.replay_dir))
    if src.resumable:
        s.set("JOBDIR", str(cfg.jobs_dir / src.slug))  # resumable scheduler queue + dupefilter
    if src.render_js:  # only for sources that need browser rendering
        s.set("DOWNLOAD_HANDLERS", {
            "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
            "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
        })
        s.set("PLAYWRIGHT_BROWSER_TYPE", "chromium")
        s.set("PLAYWRIGHT_LAUNCH_OPTIONS", {"headless": True})
    return s


def _final_status(stats: dict, reason: str) -> str:
    if reason == "cancelled":
        return "cancelled"
    if reason not in ("finished", "closespider_pagecount"):
        return "failed"
    fetched = stats.get("pages_fetched", 0)
    failed = stats.get("requests_failed", 0) + stats.get("processing_errors", 0)
    if fetched == 0 and failed:
        return "failed"
    if failed and (stats.get("items_extracted", 0) == 0):
        return "failed"
    if failed:
        return "partial"
    return "succeeded"


def finalize(job_id: int, reason: str, error: str | None = None) -> str:
    cfg = get_settings()
    with session_scope() as s:
        job = s.get(CrawlJob, job_id)
        src = job.source
        stats = dict(job.stats or {})
        status = "failed" if error else _final_status(stats, reason)
        now = dt.datetime.now(dt.timezone.utc)
        if status == "failed" and not error:
            last = s.execute(select(CrawlLog.message).where(CrawlLog.job_id == job_id, CrawlLog.level == "error")
                             .order_by(CrawlLog.id.desc()).limit(1)).scalar_one_or_none()
            warn = s.execute(select(CrawlLog.message).where(CrawlLog.job_id == job_id, CrawlLog.level == "warning",
                                                            CrawlLog.message.like("HTTP %")).order_by(CrawlLog.id.desc()).limit(1)).scalar_one_or_none()
            error = (f"{stats.get('requests_failed', 0)} request(s) failed, {stats.get('processing_errors', 0)} processing error(s)"
                     f"{'; ' + (last or warn) if (last or warn) else ''}"
                     f"{' [replay mode: URL not recorded]' if cfg.replay_dir and warn and 'HTTP 404' in warn else ''}"
                     f" (close reason: {reason})")[:2000]
        job.status, job.finished_at, job.error = status, now, error
        if reason == "closespider_pagecount":
            stats["note"] = "page budget reached"
        job.stats = stats
        src.last_run_at = now
        stored = stats.get("items_stored", 0) + stats.get("items_revised", 0)
        if status in ("succeeded", "partial"):
            src.last_success_at = now
            src.last_records = stored
            src.total_records = (src.total_records or 0) + stored
            src.consecutive_failures = 0
            src.last_error = None
        elif status == "failed":
            src.failure_count = (src.failure_count or 0) + 1
            src.consecutive_failures = (src.consecutive_failures or 0) + 1
            src.last_error = job.error
            if src.consecutive_failures >= _override("auto_pause_after_failures", cfg.auto_pause_after_failures) and src.status == "active":
                src.status = "paused"  # circuit breaker
                s.add(CrawlLog(job_id=job_id, level="error",
                               message=f"source auto-paused after {src.consecutive_failures} consecutive failures"))
        s.add(CrawlLog(job_id=job_id, level="info", message=f"job finished: {status} ({reason})"))
        return status


def run_job(job_id: int) -> int:
    with session_scope() as s:
        job = s.get(CrawlJob, job_id)
        if job is None:
            print(f"job {job_id} not found", file=sys.stderr)
            return 2
        job.status = "running"
        job.started_at = dt.datetime.now(dt.timezone.utc)
        job.heartbeat_at = job.started_at
        job.pid = os.getpid()
        src = job.source
        settings = build_settings(src, job)
    try:
        from .spiders.registry_spider import RegistrySpider

        process = CrawlerProcess(settings, install_root_handler=True)
        crawler = process.create_crawler(RegistrySpider)
        process.crawl(crawler, job_id=job_id)
        process.start()
        reason = getattr(crawler.spider, "close_reason", None) or crawler.stats.get_value("finish_reason") or "finished"
        status = finalize(job_id, reason)
    except Exception as exc:  # crash → failed job, never a hung "running" row
        traceback.print_exc()
        status = finalize(job_id, "exception", error=f"{type(exc).__name__}: {exc}"[:2000])
    return 0 if status in ("succeeded", "partial", "cancelled") else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(run_job(int(sys.argv[1])))
