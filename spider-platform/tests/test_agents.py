"""Emithran Brain: forecasting honesty, LLM gateway (privacy, fallback, grounding), agents, orchestration and API."""
import datetime as dt
import json
import random

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from conftest import FIX


# ------------------------------------------------------------------------------------------ forecasting
def test_forecast_beats_naive_on_trend_and_refuses_short_history():
    from mip.agents.forecast import holt_forecast, linear_trend_forecast, volatility_cone

    random.seed(3)
    y = [100 + 2 * i + random.gauss(0, 1) for i in range(60)]
    fc = holt_forecast(y, 6)
    assert fc["backtest"]["skill"] > 0.2 and 215 < fc["points"][-1]["p50"] < 240
    assert fc["points"][-1]["lo80"] < fc["points"][-1]["p50"] < fc["points"][-1]["hi80"]
    assert fc["points"][5]["hi80"] - fc["points"][5]["lo80"] > fc["points"][0]["hi80"] - fc["points"][0]["lo80"]   # widens with horizon
    assert holt_forecast([1, 2, 3, 4], 6) == {}                    # too short: no invented forecast
    cone = volatility_cone([100 + random.gauss(0, 1) for _ in range(120)], 30)
    assert cone["points"][-1]["p50"] == pytest.approx(cone["points"][0]["p50"])   # no directional claim for FX
    ols = linear_trend_forecast([2020, 2021, 2022, 2023, 2024], [30, 31, 32, 33, 34], [2025, 2026])
    assert ols["indicative"] and ols["points"][0]["p50"] == pytest.approx(35, abs=0.01)
    assert linear_trend_forecast([1, 2, 3], [1, 2, 3], [4]) == {}


# ------------------------------------------------------------------------------------------ gateway
def _gateway(handler):
    from mip.agents.llm import LLMGateway, Provider

    provs = {"local1": Provider("local1", "Local", "http://local/v1", local=True),
             "hosted": Provider("hosted", "Hosted", "http://hosted/v1", key_env="MIP_KEY_TESTHOSTED", rpm=None)}
    return LLMGateway(http=httpx.Client(transport=httpx.MockTransport(handler)), providers=provs)


def _assign(db, mapping, allow=False):
    from mip.agents.llm import ALLOW_HOSTED_KEY, ASSIGN_KEY
    from mip.db import session_scope
    from mip.models import AppSetting

    with session_scope() as s:
        s.merge(AppSetting(key=ASSIGN_KEY, value=mapping))
        s.merge(AppSetting(key=ALLOW_HOSTED_KEY, value=allow))


def _reply(obj):
    return httpx.Response(200, json={"choices": [{"message": {"content": "```json\n" + json.dumps(obj) + "\n```"}}], "usage": {"total_tokens": 9}})


def test_gateway_falls_back_and_blocks_hosted_for_internal(db, monkeypatch):
    monkeypatch.setenv("MIP_KEY_TESTHOSTED", "k")
    calls = []

    def handler(req):
        calls.append(str(req.url))
        if "local" in req.url.host:
            return httpx.Response(503)
        return _reply({"ok": True})

    gw = _gateway(handler)
    _assign(db, {"*": [{"provider": "local1", "model": "m1"}, {"provider": "hosted", "model": "m2"}]})
    obj, meta = gw.chat_json("commodity", "sys", "usr", internal=False)
    assert obj == {"ok": True} and meta["provider"] == "hosted" and meta["attempts"][0]["error"] == "HTTP 503"
    assert [u.split("/")[2] for u in calls] == ["local", "hosted"]

    calls.clear()                                                    # internal data: hosted provider must never be called
    obj, meta = gw.chat_json("commodity", "sys", "usr", internal=True)
    assert obj is None and all("hosted" not in u for u in calls) and any("blocked for internal" in x for x in meta["skipped"])
    _assign(db, {"*": [{"provider": "hosted", "model": "m2"}]}, allow=True)   # operator explicitly allows it
    assert gw.chat_json("commodity", "s", "u", internal=True)[0] == {"ok": True}


def test_gateway_without_key_or_assignment_is_silent(db):
    gw = _gateway(lambda r: _reply({"x": 1}))
    assert gw.chat_json("commodity", "s", "u", internal=False)[0] is None      # nothing assigned
    _assign(db, {"*": [{"provider": "hosted", "model": "m"}]})
    obj, meta = gw.chat_json("commodity", "s", "u", internal=False)
    assert obj is None and "no API key" in meta["skipped"][0]                   # key env not set


# ------------------------------------------------------------------------------------------ data helpers
def _seed_series(slug="worldbank-pink-sheet", kind="price", entity="Copper", unit="$/mt", months=72, start=5000.0, growth=0.04, country="WLD"):
    from mip.db import session_scope
    from mip.models import Observation, Source

    with session_scope() as s:
        sid = s.execute(select(Source.id).where(Source.slug == slug)).scalar_one()
        d0 = dt.date(2020, 11, 1)
        for i in range(months):
            y, m = divmod(d0.month - 1 + i, 12)
            day = dt.date(d0.year + y, m + 1, 1)
            v = start * (1 + growth) ** i
            s.add(Observation(kind=kind, series_key=f"{slug}|{entity}|{country}", fingerprint=f"{slug}-{entity}-{i}", source_id=sid, entity=entity,
                              metric="monthly_average_price", value=v, unit=unit, period_date=day, country_iso3=country, status="unverified",
                              quality_flags=["source_unverified"]))


def _run(use_llm=False, gateway=None):
    from mip.agents.brain import run_brain
    from mip.db import session_scope
    from mip.models import AgentRun

    rid = run_brain("manual", use_llm=use_llm, gateway=gateway)
    with session_scope() as s:
        return rid, s.get(AgentRun, rid)


def _findings(**kw):
    from mip.db import session_scope
    from mip.models import AgentFinding

    with session_scope() as s:
        q = select(AgentFinding)
        for k, v in kw.items():
            q = q.where(getattr(AgentFinding, k) == v)
        return s.execute(q).scalars().all()


# ------------------------------------------------------------------------------------------ agents + brain
def test_commodity_agent_flags_move_with_evidence_and_honest_forecast(seeded):
    _seed_series()
    rid, run = _run()
    assert run.status in ("succeeded", "partial") and run.mode == "rules" and run.agents["commodity"]["status"] == "ok"
    f = _findings(agent="commodity", key="commodity:copper")[0]
    assert f.severity in ("high", "critical") and f.forecast and f.forecast["points"] and f.forecast["history"]
    ids = [e["id"] for e in f.evidence]
    assert ids == sorted(set(ids)) and {"e1", "e2", "e4"} <= set(ids)               # every claim carries cited evidence
    ev = {e["label"]: e["value"] for e in f.evidence}
    assert ev["change over ~12 months"] > 10 and ev["history length"] == 72
    assert "commodity:copper" in f.tags and f.analysis["mode"] == "rules"   # family tags appear only when the taxonomy has such materials


def test_flag_lifecycle_ack_survives_rerun_and_resolves_when_gone(seeded):
    from mip.db import session_scope
    from mip.models import AgentFinding, Observation

    _seed_series()
    _run()
    f = _findings(agent="commodity", key="commodity:copper")[0]
    with session_scope() as s:
        s.get(AgentFinding, f.id).status = "ack"
    _run()
    again = _findings(agent="commodity", key="commodity:copper")[0]
    assert again.id == f.id and again.status == "ack" and again.last_seen >= f.last_seen   # acknowledged flag is updated in place
    with session_scope() as s:                                                              # the underlying data disappears
        for o in s.execute(select(Observation).where(Observation.entity == "Copper")).scalars():
            s.delete(o)
    _run()
    gone = _findings(agent="commodity", key="commodity:copper")[0]
    assert gone.status == "resolved" and gone.resolved_at is not None


def test_agent_failure_is_isolated_and_recorded(seeded, monkeypatch):
    from mip.agents import numeric_agents

    monkeypatch.setattr(numeric_agents.FXAgent, "collect", lambda self, ctx: (_ for _ in ()).throw(RuntimeError("boom")))
    _, run = _run()
    assert run.status == "partial" and run.agents["fx"]["status"] == "error" and "boom" in run.agents["fx"]["error"]
    assert run.agents["data_quality"]["status"] == "ok"                       # others still ran


def test_red_team_contests_claims_on_short_history_and_corroboration_boosts_confidence(seeded):
    _seed_series(months=30, growth=0.03)                                     # steep but only 30 months of history
    _run()
    f = _findings(agent="commodity", key="commodity:copper")[0]
    assert f.contested and any(c["weight"] == "major" and "30 months" in c["text"] for c in f.analysis["challenges"])
    ch = _findings(agent="red_team", key="challenge:commodity:commodity:copper")
    assert ch and ch[0].entities["target"] == {"agent": "commodity", "key": "commodity:copper"} and ch[0].kind == "challenge"


def test_data_quality_agent_reports_real_gaps(seeded):
    _run()
    keys = {f.key for f in _findings(agent="data_quality")}
    assert "dq:unresolved-ids" in keys                                              # MHR/LHR are undefined in the fixture too
    assert "dq:labour-coverage" not in keys                                         # fixture machines all carry labour rates: no false alarm
    assert any(k.startswith("dq:source:") and k.endswith(":never") for k in keys)  # sources that never ran are reported
    scout = {f.key for f in _findings(agent="source_scout")}
    assert "scout:news-coverage" in scout                                          # no news crawled in the fixture database


# ------------------------------------------------------------------------------------------ LLM grounding
def test_llm_opinion_is_grounded_clamped_and_hosted_blocked_for_internal(seeded, monkeypatch):
    monkeypatch.setenv("MIP_KEY_TESTHOSTED", "k")
    _seed_series()
    seen = []

    def handler(req):
        body = json.loads(req.content)
        seen.append((req.url.host, body["messages"][1]["content"]))
        user = body["messages"][1]["content"]
        if '"finding"' not in user or "Argue against" in user:
            return _reply({"challenges": []})
        return _reply({"opinions": [
            {"finding": 0, "stance": "downgrade", "severity": "info", "rationale": "Looks cyclical.", "cites": ["e1"]},        # grounded; 2 steps -> clamped to 1
            {"finding": 1, "stance": "upgrade", "severity": "critical", "rationale": "Invented", "cites": ["e99"]}],           # cites nothing real -> dropped
            "watch": [{"title": "Watch copper demand", "rationale": "x", "finding": 0, "cites": ["e1"]},
                      {"title": "Ungrounded watch", "rationale": "y", "finding": 0, "cites": ["nope"]}]})

    gw = _gateway(handler)
    _assign(seeded, {"commodity": [{"provider": "hosted", "model": "big"}]})
    _, run = _run(use_llm=True, gateway=gw)
    assert run.agents["commodity"]["mode"] == "llm" and run.agents["commodity"]["model"] == "hosted/big"
    top = sorted(_findings(agent="commodity", kind="risk"), key=lambda r: r.id)
    changed = [f for f in top if f.analysis.get("stance") == "downgrade"]
    assert len(changed) == 1 and changed[0].analysis["opinion"] == "Looks cyclical."
    assert not any(f.analysis.get("opinion") == "Invented" for f in top)           # ungrounded opinion discarded
    order = ["info", "low", "medium", "high", "critical"]
    assert order.index(changed[0].analysis["severity_rules"]) - order.index(changed[0].severity) <= 1   # at most one step
    watch = [f.title for f in _findings(agent="commodity", kind="opinion")]
    assert watch == ["Watch copper demand"]
    # the commodity agent's internal-data findings must not have been sent to the hosted provider
    assert not any("emithran-taxonomy" in c for h, c in seen if h == "hosted" and "Taxonomy prices" in c)


# ------------------------------------------------------------------------------------------ API
@pytest.fixture()
def client(seeded):
    from mip.api.main import app

    with TestClient(app) as c:
        yield c


def test_api_run_overview_findings_status_and_llm_setup(client):
    import time

    _seed_series()
    assert client.get("/api/agents/overview").json()["last_run"] is None
    r = client.post("/api/agents/run", json={"use_llm": False})
    assert r.status_code == 200
    rid = r.json()["run_id"]
    for _ in range(100):
        st = client.get(f"/api/agents/runs/{rid}").json()
        if st["status"] != "running":
            break
        time.sleep(0.1)
    assert st["status"] in ("succeeded", "partial") and st["mode"] == "rules" and st["summary"]["overall"]["level"]
    ov = client.get("/api/agents/overview").json()
    assert ov["risk"]["counts"]["open"] > 0 and ov["brief"]["mode"] == "rules" and ov["top"]
    st = ov["forecast_stats"]
    assert st["evaluated"] >= 1 and st["credible"] == len(ov["forecasts"])            # only forecasts that beat naive are listed
    assert all(f["skill"] is not None and f["skill"] > 0.1 for f in ov["forecasts"])
    assert set(ov["risk"]["domains"]) >= {"commodities", "fx", "labour", "data_quality", "news", "data_gaps"}
    lst = client.get("/api/agents/findings", params={"agent": "commodity", "severity": "high"}).json()
    assert lst["items"] and all(i["agent"] == "commodity" for i in lst["items"])
    one = client.get(f"/api/agents/findings/{lst['items'][0]['id']}").json()
    assert one["evidence"] and one["forecast"]["points"]
    assert client.post(f"/api/agents/findings/{one['id']}/status", json={"status": "dismissed"}).json()["status"] == "dismissed"
    assert client.post(f"/api/agents/findings/{one['id']}/status", json={"status": "bogus"}).status_code == 422
    assert one["id"] not in [i["id"] for i in client.get("/api/agents/findings").json()["items"]]   # dismissed leaves the live list
    assert len(client.get("/api/agents").json()["agents"]) == 7
    assert client.post("/api/agents/run", json={"agents": ["nope"]}).status_code == 422

    bad = client.post("/api/agents/llm/assign", json={"assignments": {"commodity": [{"provider": "nope", "model": "x"}]}})
    assert bad.status_code == 422
    ok = client.post("/api/agents/llm/assign", json={"assignments": {"*": [{"provider": "ollama", "model": "gemma"}]}, "allow_hosted_internal": False})
    assert ok.status_code == 200 and ok.json()["assignments"]["*"][0]["model"] == "gemma"
    keys = json.dumps(client.get("/api/agents/llm").json())
    assert "MIP_KEY_OPENROUTER" in keys and "key_present" in keys                  # variable names and presence only, never values


def test_run_endpoint_rejects_concurrent_runs_and_requires_admin(client, monkeypatch):
    from mip import config
    from mip.agents.brain import start_run

    start_run("manual")                                                             # a run is already in progress
    assert client.post("/api/agents/run", json={}).status_code == 409
    monkeypatch.setenv("MIP_ADMIN_TOKEN", "s3")
    config.get_settings.cache_clear()
    assert client.post("/api/agents/run", json={}).status_code == 401
    assert client.post("/api/agents/llm/assign", json={}).status_code == 401
