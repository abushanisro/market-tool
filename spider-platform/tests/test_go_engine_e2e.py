"""Go fetch worker end to end: real API server + real ``mip-fetch`` binary, launched by the real Worker.

Network is replaced by the recorded responses in tests/fixtures/replay (both engines read the same
index), so results must match the Scrapy engine's numbers in test_crawl_e2e.py exactly.
Skipped when no Go toolchain is available.
"""
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from sqlalchemy import func, select

from conftest import ROOT

GO = shutil.which("go") or next((str(p) for p in [Path.home() / "sdk" / "go1.27.3" / "bin" / "go"] if p.exists()), None)
pytestmark = pytest.mark.skipif(GO is None, reason="Go toolchain not installed")


@pytest.fixture(scope="session")
def fetcher_bin(tmp_path_factory):
    out = tmp_path_factory.mktemp("gobin") / "mip-fetch"
    r = subprocess.run([GO, "build", "-o", str(out), "./cmd/mip-fetch"], cwd=ROOT / "go-fetcher", capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return out


@pytest.fixture()
def server(seeded, fetcher_bin, monkeypatch):
    """API server on a free port, sharing the test SQLite database; Worker configured to use the Go binary."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    api = f"http://127.0.0.1:{port}"
    monkeypatch.setenv("MIP_ADMIN_TOKEN", "")  # the spawned server would otherwise read a developer .env from cwd
    monkeypatch.setenv("MIP_GO_FETCHER_BIN", str(fetcher_bin))
    monkeypatch.setenv("MIP_GO_FETCHER_API", api)
    from mip import config

    config.get_settings.cache_clear()
    proc = subprocess.Popen([sys.executable, "-m", "mip.cli", "serve", "--host", "127.0.0.1", "--port", str(port)],
                            cwd=ROOT, env={**os.environ}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"{api}/api/health", timeout=1).read()
                break
            except OSError:
                if proc.poll() is not None:
                    raise RuntimeError(proc.stdout.read().decode())
                time.sleep(0.1)
        else:
            raise RuntimeError("API server did not start")
        yield api
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def run_via_worker(source, params=None):
    """Queue a job and let the real Worker dispatch it; returns (status, stats, error, log text)."""
    from mip.config import get_settings
    from mip.db import session_scope
    from mip.jobs.service import create_job
    from mip.models import CrawlJob
    from mip.worker import Worker

    with session_scope() as s:
        jid = create_job(s, source, params=params or {}).id
    w = Worker()
    w.dispatch_tick()
    assert jid in w.procs, "worker did not launch the job"
    proc, _ = w.procs[jid]
    assert "mip-fetch" in proc.args[0], f"expected the Go binary, launched {proc.args}"
    proc.wait(timeout=90)
    w.dispatch_tick()  # reaps
    with session_scope() as s:
        j = s.get(CrawlJob, jid)
        return j.status, dict(j.stats), j.error, (get_settings().jobs_dir / f"job_{jid}.log").read_text()


def test_fx_crawl_incremental_and_backfill_matches_scrapy(server):
    from mip.db import session_scope
    from mip.models import CrawlLog, CrawlUrl, Observation

    status, stats, err, log = run_via_worker("frankfurter-fx")
    assert status == "succeeded", (err, log[-2000:])
    assert stats["items_stored"] == 29 and stats["items_extracted"] == 29           # same as the Scrapy e2e
    assert stats["pages_fetched"] == 1 and stats["urls_discovered"] == 1 and stats["bytes"] > 0
    assert "REPLAY MODE" in log

    # second run: conditional validators/hash → unchanged, body not even uploaded, nothing re-inserted
    status, stats, _, _ = run_via_worker("frankfurter-fx")
    assert status == "succeeded" and stats.get("pages_unchanged") == 1 and stats.get("items_stored", 0) == 0

    # backfill window served from the recorded time series
    status, stats, _, _ = run_via_worker("frankfurter-fx", {"backfill": True, "start_date": "2026-07-01", "end_date": "2026-10-27"})
    assert status == "succeeded" and stats["items_stored"] == 420 and stats["items_duplicate"] == 5

    with session_scope() as s:
        inr = s.execute(select(Observation).where(Observation.entity == "USD/INR", Observation.source_id.is_not(None),
                                                  Observation.period_date == "2026-10-27")).scalar_one()
        assert inr.value == 95.12 and "replayed" in inr.quality_flags and inr.original["replay"]["captured_at"]
        assert s.execute(select(func.count()).select_from(CrawlUrl)).scalar_one() == 2
        assert s.execute(select(func.count()).select_from(CrawlLog).where(CrawlLog.message.like("go fetcher started%"))).scalar_one() == 3


def test_rss_and_worldbank_through_go(server):
    status, stats, err, log = run_via_worker("manufacturingdive-news")
    assert status == "succeeded", (err, log[-1500:])
    assert stats["items_stored"] == 5

    status, stats, err, log = run_via_worker("worldbank-wdi-manufacturing")
    # only the .ZS URL is recorded; the .CD one is an unrecorded 404 → one failed request → partial, never a silent success
    assert status == "partial", (status, err, log[-1500:])
    assert stats["items_stored"] > 0 and stats["requests_failed"] == 1 and stats["http_errors"] == 1


def test_all_urls_failing_marks_the_job_failed_and_trips_nothing_silently(server):
    from mip.db import session_scope
    from mip.models import Source

    with session_scope() as s:
        src = s.execute(select(Source).where(Source.slug == "un-comtrade-preview")).scalar_one()
        src.verification_status = "verified"  # nothing for comtrade is recorded → every URL 404s
    status, stats, err, _ = run_via_worker("un-comtrade-preview")
    assert status == "failed" and "request(s) failed" in err
    assert stats["requests_failed"] == 8 and stats.get("items_stored", 0) == 0
