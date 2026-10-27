"""Control plane used by the Go fetch worker (/api/fetcher): plan → pages → heartbeat → finish."""
import base64
import datetime as dt
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from conftest import FIX

FX_URL = "https://api.frankfurter.dev/v1/latest?base=USD"
NOW = dt.datetime(2026, 10, 27, 16, 40, tzinfo=dt.timezone.utc).isoformat()


@pytest.fixture()
def client(seeded):
    from mip.api.main import app

    with TestClient(app) as c:
        yield c


def claimed_job(slug="frankfurter-fx", params=None) -> int:
    from mip.db import session_scope
    from mip.jobs.service import claim_jobs, create_job

    with session_scope() as s:
        jid = create_job(s, slug, params=params or {}).id
        claim_jobs(s, 100, "test-worker")  # claims every queued job; the helper's job is always among them
    return jid


def b64(path):
    return base64.b64encode((FIX / "replay" / path).read_bytes()).decode()


def page(url=FX_URL, file="frankfurter_latest_usd.json", **kw):
    p = {"url": url, "status": 200, "content_type": "application/json", "body_b64": b64(file), "content_hash": "h1",
         "etag": '"e1"', "fetched_at": NOW}
    p.update(kw)
    return p


def job_row(jid):
    from mip.db import session_scope
    from mip.models import CrawlJob

    with session_scope() as s:
        j = s.get(CrawlJob, jid)
        return j.status, dict(j.stats or {}), j.error


def test_plan_describes_job_and_marks_it_running(client):
    jid = claimed_job()
    r = client.get(f"/api/fetcher/jobs/{jid}/plan")
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["source"] == "frankfurter-fx" and plan["force"] is False
    assert plan["start_urls"] == [{"url": FX_URL, "depth": 0, "page": 1}]
    pol = plan["policy"]
    assert pol["concurrency"] >= 1 and pol["respect_robots"] is True and pol["accept"] == "application/json"
    assert pol["max_pages"] > 0 and pol["timeout_seconds"] > 0 and "emithran" in pol["user_agent"].lower()
    assert job_row(jid)[0] == "running"


def test_plan_expands_backfill_windows(client):
    jid = claimed_job(params={"backfill": True, "start_date": "2026-07-01", "end_date": "2026-10-27"})
    urls = [u["url"] for u in client.get(f"/api/fetcher/jobs/{jid}/plan").json()["start_urls"]]
    assert urls == ["https://api.frankfurter.dev/v1/2026-07-01..2026-10-27?base=USD"]


def test_plan_returns_checkpoints_from_previous_runs(client):
    jid = claimed_job()
    client.get(f"/api/fetcher/jobs/{jid}/plan")
    assert client.post(f"/api/fetcher/jobs/{jid}/pages", json={"pages": [page()]}).status_code == 200
    client.post(f"/api/fetcher/jobs/{jid}/finish", json={"reason": "finished"})
    jid2 = claimed_job()
    cps = client.get(f"/api/fetcher/jobs/{jid2}/plan").json()["checkpoints"]
    assert cps[FX_URL] == {"etag": '"e1"', "last_modified": None, "content_hash": "h1"}


def test_plan_refuses_unclaimed_and_unsupported_sources(client):
    from mip.db import session_scope
    from mip.jobs.service import create_job
    from mip.models import Source

    with session_scope() as s:
        queued = create_job(s, "frankfurter-fx").id
    assert client.get(f"/api/fetcher/jobs/{queued}/plan").status_code == 409  # still queued: nobody claimed it
    assert client.get("/api/fetcher/jobs/9999/plan").status_code == 404
    with session_scope() as s:
        from mip.jobs.service import cancel_job
        cancel_job(s, queued)

    with session_scope() as s:
        s.execute(select(Source).where(Source.slug == "manufacturingdive-news")).scalar_one().sitemap_urls = ["https://x/sitemap.xml"]
    jid = claimed_job("manufacturingdive-news")
    r = client.get(f"/api/fetcher/jobs/{jid}/plan")
    assert r.status_code == 422 and "sitemap" in r.json()["detail"]


def test_pages_are_parsed_and_stored_like_scrapy(client):
    from mip.db import session_scope
    from mip.models import CrawlUrl, Observation

    jid = claimed_job()
    client.get(f"/api/fetcher/jobs/{jid}/plan")
    r = client.post(f"/api/fetcher/jobs/{jid}/pages", json={"pages": [page(
        replayed=True, replay={"captured_at": "2026-10-27T16:40:00Z"})]})
    assert r.status_code == 200, r.text
    assert r.json()["follow"] == [] and r.json()["stats"]["items_stored"] == 29
    status, stats, _ = job_row(jid)
    assert stats["items_stored"] == 29 and stats["items_extracted"] == 29

    with session_scope() as s:
        inr = s.execute(select(Observation).where(Observation.entity == "USD/INR", Observation.period_date == "2026-10-27")).scalar_one()
        assert inr.value == 95.12 and "replayed" in inr.quality_flags and inr.original["replay"]["captured_at"]
        assert inr.source_url == FX_URL
        cp = s.execute(select(CrawlUrl).where(CrawlUrl.url == FX_URL)).scalar_one()
        assert (cp.status, cp.http_status, cp.etag, cp.content_hash, cp.items_extracted, cp.last_job_id) == \
               ("fetched", 200, '"e1"', "h1", 29, jid)
        assert cp.last_fetched is not None and cp.fetch_count == 1

    # the same payload again → fingerprint dedupe, nothing new stored
    r = client.post(f"/api/fetcher/jobs/{jid}/pages", json={"pages": [page()]})
    assert r.json()["stats"].get("items_stored", 0) == 0 and r.json()["stats"]["items_duplicate"] == 29


def test_unchanged_not_modified_and_failed_pages_only_touch_checkpoints(client):
    from mip.db import session_scope
    from mip.models import CrawlLog, CrawlUrl, Observation

    jid = claimed_job()
    client.get(f"/api/fetcher/jobs/{jid}/plan")
    pages = [
        {"url": "https://api.frankfurter.dev/v1/a", "status": 200, "unchanged": True, "content_hash": "h", "fetched_at": NOW},
        {"url": "https://api.frankfurter.dev/v1/b", "status": 304, "not_modified": True},
        {"url": "https://api.frankfurter.dev/v1/c", "status": 404, "error": "HTTP 404", "fetched_at": NOW},
        {"url": "https://api.frankfurter.dev/v1/d", "status": 0, "error": "connection refused"},
    ]
    r = client.post(f"/api/fetcher/jobs/{jid}/pages", json={"pages": pages})
    assert r.status_code == 200 and r.json()["stats"] == {}
    with session_scope() as s:
        st = {c.url[-1]: (c.status, c.error) for c in s.execute(select(CrawlUrl)).scalars()}
        assert st["a"][0] == "unchanged" and st["b"][0] == "not_modified"
        assert st["c"] == ("failed", "HTTP 404") and st["d"] == ("failed", "connection refused")
        assert s.execute(select(func.count()).select_from(Observation).where(Observation.job_id == jid)).scalar_one() == 0
        msgs = [m for (m,) in s.execute(select(CrawlLog.message).where(CrawlLog.job_id == jid))]
        assert any("404" in m for m in msgs) and any("connection refused" in m for m in msgs)


def test_pagination_follow_urls_use_the_shared_logic(client):
    jid = claimed_job("worldbank-wdi-manufacturing")
    client.get(f"/api/fetcher/jobs/{jid}/plan")
    body = json.loads((FIX / "replay" / "worldbank_NV.IND.MANF.ZS.json").read_text())
    body[0]["pages"] = 3
    url = "https://api.worldbank.org/v2/country/IND;CHN/indicator/NV.IND.MANF.ZS?format=json&per_page=100"
    p = {"url": url, "status": 200, "content_type": "application/json", "fetched_at": NOW,
         "body_b64": base64.b64encode(json.dumps(body).encode()).decode()}
    r = client.post(f"/api/fetcher/jobs/{jid}/pages", json={"pages": [p]}).json()
    assert [(f["page"], f["depth"]) for f in r["follow"]] == [(2, 0), (3, 0)]
    assert all("page=" in f["url"] and f["url"].startswith("https://api.worldbank.org/") for f in r["follow"])
    # a follow-up page (page=2) must not paginate again
    p2 = {**p, "url": r["follow"][0]["url"], "page": 2}
    assert client.post(f"/api/fetcher/jobs/{jid}/pages", json={"pages": [p2]}).json()["follow"] == []


def test_parser_follow_links_are_scope_checked(client):
    from mip.db import session_scope
    from mip.models import Source

    with session_scope() as s:  # a source whose parser yields Follow links (RSS article fetch) with depth 0 allowed only
        src = s.execute(select(Source).where(Source.slug == "manufacturingdive-news")).scalar_one()
        src.extraction = {**src.extraction, "fetch_articles": True}
        src.max_depth = 0
    jid = claimed_job("manufacturingdive-news")
    client.get(f"/api/fetcher/jobs/{jid}/plan")
    p = {"url": "https://www.manufacturingdive.com/feeds/news/", "status": 200, "content_type": "application/rss+xml",
         "body_b64": b64("manufacturingdive_news.xml"), "fetched_at": NOW}
    r = client.post(f"/api/fetcher/jobs/{jid}/pages", json={"pages": [p]}).json()
    assert r["follow"] == []  # article links sit at depth 1 > max_depth 0 → refused, counted
    assert job_row(jid)[1].get("urls_out_of_scope", 0) >= 1


def test_bad_payload_is_a_recorded_processing_error_not_a_500(client):
    from mip.db import session_scope
    from mip.models import CrawlUrl

    jid = claimed_job()
    client.get(f"/api/fetcher/jobs/{jid}/plan")
    bad = {"url": FX_URL, "status": 200, "content_type": "application/json", "fetched_at": NOW,
           "body_b64": base64.b64encode(b"<html>not json</html>").decode()}
    r = client.post(f"/api/fetcher/jobs/{jid}/pages", json={"pages": [bad]})
    assert r.status_code == 200
    assert job_row(jid)[1]["processing_errors"] == 1
    with session_scope() as s:
        cp = s.execute(select(CrawlUrl).where(CrawlUrl.url == FX_URL)).scalar_one()
        assert cp.status == "failed" and cp.error.startswith("parse error")


def test_validation_rules_apply(client):
    from mip.db import session_scope
    from mip.models import Source

    with session_scope() as s:
        s.execute(select(Source).where(Source.slug == "frankfurter-fx")).scalar_one().validation = {"max": 50}
    jid = claimed_job()
    client.get(f"/api/fetcher/jobs/{jid}/plan")
    r = client.post(f"/api/fetcher/jobs/{jid}/pages", json={"pages": [page()]}).json()
    stats = job_row(jid)[1]
    assert stats["items_invalid"] >= 1 and stats["items_extracted"] == 29 and r["stats"]["items_stored"] < 29


def test_heartbeat_merges_only_fetcher_stats_and_signals_cancel(client):
    from mip.db import session_scope
    from mip.jobs.service import cancel_job
    from mip.models import CrawlLog

    jid = claimed_job()
    client.get(f"/api/fetcher/jobs/{jid}/plan")
    r = client.post(f"/api/fetcher/jobs/{jid}/heartbeat", json={
        "stats": {"pages_fetched": 4, "retries": 2, "items_stored": 999}, "logs": [{"level": "info", "message": "hello"}]})
    assert r.json() == {"cancel": False}
    stats = job_row(jid)[1]
    assert stats["pages_fetched"] == 4 and stats["retries"] == 2 and "items_stored" not in stats  # items belong to ingest
    with session_scope() as s:
        assert s.execute(select(CrawlLog.message).where(CrawlLog.job_id == jid)).scalar_one() == "hello"
        cancel_job(s, jid)
    assert client.post(f"/api/fetcher/jobs/{jid}/heartbeat", json={}).json() == {"cancel": True}


def test_finish_uses_the_same_status_rules_as_scrapy(client):
    ok = claimed_job()
    client.get(f"/api/fetcher/jobs/{ok}/plan")
    client.post(f"/api/fetcher/jobs/{ok}/pages", json={"pages": [page()]})
    r = client.post(f"/api/fetcher/jobs/{ok}/finish", json={"reason": "finished", "stats": {"pages_fetched": 1}})
    assert r.json() == {"status": "succeeded"}
    from mip.db import session_scope
    from mip.models import Source
    with session_scope() as s:
        src = s.execute(select(Source).where(Source.slug == "frankfurter-fx")).scalar_one()
        assert src.last_success_at is not None and src.last_records == 29 and src.consecutive_failures == 0

    bad = claimed_job()
    client.get(f"/api/fetcher/jobs/{bad}/plan")
    r = client.post(f"/api/fetcher/jobs/{bad}/finish", json={"reason": "finished", "stats": {"requests_failed": 1}})
    assert r.json() == {"status": "failed"} and "1 request(s) failed" in job_row(bad)[2]

    to = claimed_job()
    client.get(f"/api/fetcher/jobs/{to}/plan")
    r = client.post(f"/api/fetcher/jobs/{to}/finish", json={"reason": "closespider_timeout", "error": "job exceeded 60s timeout"})
    assert r.json() == {"status": "failed"} and job_row(to)[2] == "job exceeded 60s timeout"


def test_finished_jobs_reject_further_pages(client):
    jid = claimed_job()
    client.get(f"/api/fetcher/jobs/{jid}/plan")
    client.post(f"/api/fetcher/jobs/{jid}/finish", json={"reason": "finished"})
    assert client.post(f"/api/fetcher/jobs/{jid}/pages", json={"pages": [page()]}).status_code == 409


def test_admin_token_is_required_when_configured(client, monkeypatch):
    from mip import config

    monkeypatch.setenv("MIP_ADMIN_TOKEN", "s3cret")
    config.get_settings.cache_clear()
    jid = claimed_job()
    assert client.get(f"/api/fetcher/jobs/{jid}/plan").status_code == 401
    assert client.get(f"/api/fetcher/jobs/{jid}/plan", headers={"X-Admin-Token": "wrong"}).status_code == 401
    assert client.get(f"/api/fetcher/jobs/{jid}/plan", headers={"X-Admin-Token": "s3cret"}).status_code == 200


def test_worker_picks_engine_per_source(seeded, tmp_path, monkeypatch):
    from mip import config
    from mip.db import session_scope
    from mip.models import CrawlJob, Source
    from mip.worker import Worker

    fake = tmp_path / "mip-fetch"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    with session_scope() as s:
        fx = claimed_job("frankfurter-fx")
        wb = claimed_job("worldbank-pink-sheet")  # no fetch_engine → always Scrapy

    def command(jid):
        with session_scope() as s:
            return Worker()._command(s.get(CrawlJob, jid))

    assert command(fx)[1:3] == ["-m", "mip.crawler.run"]  # binary not configured → Scrapy fallback
    monkeypatch.setenv("MIP_GO_FETCHER_BIN", str(fake))
    monkeypatch.setenv("MIP_GO_FETCHER_API", "http://127.0.0.1:9")
    config.get_settings.cache_clear()
    assert command(fx) == [str(fake), "run", "--job", str(fx), "--api", "http://127.0.0.1:9"]
    assert command(wb)[1:3] == ["-m", "mip.crawler.run"]
    with session_scope() as s:  # a Go-flagged source needing a Scrapy-only feature falls back too
        s.execute(select(Source).where(Source.slug == "frankfurter-fx")).scalar_one().render_js = True
    assert command(fx)[1:3] == ["-m", "mip.crawler.run"]


def test_live_feed_reports_changes(client):
    """The SSE feed emits a change event, and its version moves when a job is queued (what dashboards redraw on)."""
    import json

    from mip.api.live import live_version
    from mip.db import session_scope
    from mip.jobs.service import create_job

    before = live_version()
    with client.stream("GET", "/api/stream?once=true") as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        assert r.headers["cache-control"] == "no-cache"
        body = "".join(r.iter_text())
    assert "event: change" in body
    first = json.loads(body.split("data: ", 1)[1].split("\n", 1)[0])
    assert first == before
    with session_scope() as s:
        create_job(s, "frankfurter-fx")
    after = live_version()
    assert after["jobs"] > before["jobs"] and after["active_jobs"] == before["active_jobs"] + 1 and after != before
