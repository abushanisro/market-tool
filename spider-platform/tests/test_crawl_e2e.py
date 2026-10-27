"""End-to-end Scrapy runs against recorded real responses (replay mode, no network).

Each crawl runs in a subprocess, exactly as the worker does it (one Twisted reactor per job).
"""
import json
import os
import subprocess
import sys

from sqlalchemy import func, select

from conftest import ROOT


def run(job_id):
    return subprocess.run([sys.executable, "-m", "emithran.crawler.run", str(job_id)], cwd=ROOT, env={**os.environ},
                          capture_output=True, text=True, timeout=120)


def queue(source, params=None):
    from emithran.db import session_scope
    from emithran.jobs.service import create_job

    with session_scope() as s:
        return create_job(s, source, params=params or {}).id


def job(job_id):
    from emithran.db import session_scope
    from emithran.models import CrawlJob

    with session_scope() as s:
        j = s.get(CrawlJob, job_id)
        return j.status, dict(j.stats), j.error


def test_fx_crawl_incremental_and_backfill(seeded):
    from emithran.db import session_scope
    from emithran.models import CrawlUrl, Observation, Source

    j1 = queue("frankfurter-fx")
    p = run(j1)
    status, stats, err = job(j1)
    assert status == "succeeded", (p.stdout[-2000:], p.stderr[-2000:], err)
    assert stats["items_stored"] == 29
    # second run: same content hash → unchanged, nothing re-inserted
    j2 = queue("frankfurter-fx")
    run(j2)
    status, stats, _ = job(j2)
    assert status == "succeeded" and stats.get("pages_unchanged") == 1 and stats.get("items_stored", 0) == 0
    # backfill window from recorded time series
    j3 = queue("frankfurter-fx", {"backfill": True, "start_date": "2026-07-01", "end_date": "2026-10-27"})
    run(j3)
    status, stats, _ = job(j3)
    assert status == "succeeded" and stats["items_stored"] == 420 and stats["items_duplicate"] == 5
    with session_scope() as s:
        inr = s.execute(select(Observation).where(Observation.entity == "USD/INR", Observation.source_id.is_not(None),
                                                  Observation.period_date == "2026-10-27")).scalar_one()
        assert inr.value == 95.12 and "replayed" in inr.quality_flags and inr.original["replay"]["captured_at"]
        assert s.execute(select(func.count()).select_from(CrawlUrl)).scalar_one() == 2
        src = s.execute(select(Source).where(Source.slug == "frankfurter-fx")).scalar_one()
        assert src.consecutive_failures == 0 and src.last_success_at is not None
        # internal-table prices are now converted with crawled FX in later imports
        from emithran.normalize.currency import FxConverter
        assert FxConverter(s).usd_rate("INR").rate == 95.12


def test_failure_isolation_and_partial(seeded):
    j = queue("worldbank-wdi-manufacturing")
    run(j)
    status, stats, _ = job(j)
    # MANF.ZS recorded, MANF.CD not recorded → 404 is tracked, good data still stored
    assert status == "partial" and stats["items_stored"] == 27 and stats["requests_failed"] == 1
    j = queue("eurostat-industrial-production")
    run(j)
    status, stats, err = job(j)
    assert status == "failed" and "replay mode" in err


def test_rss_documents(seeded):
    from emithran.db import session_scope
    from emithran.models import Document

    j = queue("manufacturingdive-news")
    run(j)
    assert job(j)[0] == "succeeded"
    with session_scope() as s:
        docs = s.execute(select(Document)).scalars().all()
        assert len(docs) == 5 and all(d.summary is None for d in docs)
