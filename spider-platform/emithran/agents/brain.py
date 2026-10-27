"""Emithran Brain: orchestrates the specialist agents, reconciles their findings and records the outcome."""
from __future__ import annotations

import datetime as dt
import json
import math
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlalchemy import select

from ..db import session_scope
from ..models import AgentEvent, AgentFinding, AgentRun
from .core import SEV_RANK, SEVERITIES, Agent, AgentResult, Ctx, Finding
from .llm import LLMGateway
from .numeric_agents import CommodityAgent, FXAgent, LabourAgent
from .review_agents import DataQualityAgent, NewsRiskAgent, RedTeamAgent, SourceScoutAgent

SPECIALISTS: list[Agent] = [DataQualityAgent(), CommodityAgent(), FXAgent(), LabourAgent(), NewsRiskAgent(), SourceScoutAgent()]
RED_TEAM = RedTeamAgent()
ALL_AGENTS: list[Agent] = SPECIALISTS + [RED_TEAM]
BY_ID = {a.id: a for a in ALL_AGENTS}
WEIGHT = {"info": 0, "low": 2, "medium": 8, "high": 20, "critical": 40}
DOMAIN_LABEL = {"commodities": "Commodities & materials", "fx": "FX & currency", "labour": "Labour & cost", "data_quality": "Data quality",
                "news": "News & supply chain", "data_gaps": "Data gaps"}


def agent_specs() -> list[dict[str, str]]:
    return [{"id": a.id, "name": a.name, "job": a.job, "domain": a.domain} for a in ALL_AGENTS]


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _label(score: float) -> str:
    return "low" if score < 20 else "moderate" if score < 45 else "elevated" if score < 70 else "severe"


# ---------------------------------------------------------------------------------------------- risk
def compute_risk(rows: list[AgentFinding]) -> dict[str, Any]:
    """Risk scores from the findings that are still live (open or acknowledged). Contested findings count less."""
    dom: dict[str, float] = {}
    counts = {s: 0 for s in SEVERITIES}
    contested = 0
    for r in rows:
        if r.status not in ("open", "ack") or r.kind in ("challenge", "opinion"):
            continue
        counts[r.severity] += 1
        contested += bool(r.contested)
        w = WEIGHT[r.severity] * r.confidence * (0.6 if r.contested else 1.0)
        d = BY_ID[r.agent].domain if r.agent in BY_ID else "other"
        dom[d] = dom.get(d, 0.0) + w
    domains = {d: {"score": round(100 * (1 - math.exp(-x / 120)), 1), "label": DOMAIN_LABEL.get(d, d)} for d, x in dom.items()}
    for d in DOMAIN_LABEL:
        domains.setdefault(d, {"score": 0.0, "label": DOMAIN_LABEL[d]})
    for v in domains.values():
        v["level"] = _label(v["score"])
    total = sum(dom.values())
    overall = round(100 * (1 - math.exp(-total / 300)), 1)
    return {"overall": {"score": overall, "level": _label(overall)}, "domains": domains,
            "counts": {**counts, "open": sum(counts.values()), "contested": contested}}


# ---------------------------------------------------------------------------------------------- runs
def start_run(trigger: str = "manual") -> int:
    with session_scope() as s:
        run = AgentRun(trigger=trigger, status="running", mode="rules", agents={}, summary={})
        s.add(run)
        s.flush()
        return run.id


def run_brain(trigger: str = "manual", agent_ids: list[str] | None = None, use_llm: bool | None = None,
              gateway: LLMGateway | None = None, run_id: int | None = None) -> int:
    run_id = run_id or start_run(trigger)
    gw = gateway or LLMGateway()
    if use_llm is None:
        use_llm = bool(gw.assignments())
    wanted = [a for a in SPECIALISTS if not agent_ids or a.id in agent_ids]
    started = now_utc()
    results: dict[str, AgentResult] = {}

    def run_one(agent: Agent, prior: list[Finding]) -> AgentResult:
        with session_scope() as s:
            return agent.run(Ctx(session=s, gateway=gw, use_llm=use_llm, now=now_utc(), prior=prior))

    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            for res in pool.map(lambda a: run_one(a, []), wanted):
                results[res.agent] = res
        everything = [f for r in results.values() for f in r.findings]
        if not agent_ids or RED_TEAM.id in agent_ids:           # the skeptic reads everyone else's work
            results[RED_TEAM.id] = run_one(RED_TEAM, everything)
            everything += results[RED_TEAM.id].findings
        _corroborate(everything)
        with session_scope() as s:
            for aid, res in results.items():
                if res.error:
                    continue
                seen = set()
                for f in res.findings:
                    seen.add(f.key)
                    _upsert(s, run_id, f, res, started)
                _resolve_missing(s, run_id, aid, seen)
            s.flush()
            rows = s.execute(select(AgentFinding)).scalars().all()
            summary = compute_risk(rows)
            summary["brief"] = _brief(gw, rows, use_llm)
        status = "failed" if all(r.error for r in results.values()) else "partial" if any(r.error for r in results.values()) else "succeeded"
        err = None
    except Exception as exc:                                    # the run row must never stay "running"
        status, err, summary = "failed", f"{type(exc).__name__}: {exc}"[:1000], {}
    modes = {r.mode for r in results.values() if not r.error and r.findings}
    with session_scope() as s:
        run = s.get(AgentRun, run_id)
        run.status, run.finished_at, run.error, run.summary = status, now_utc(), err, summary
        run.mode = "llm" if modes == {"llm"} else "mixed" if "llm" in modes else "rules"
        run.agents = {aid: {"status": "error" if r.error else "ok", "findings": len(r.findings), "mode": r.mode, "model": r.model,
                            "seconds": r.seconds, "error": r.error, "notes": r.notes} for aid, r in results.items()}
    return run_id


def _corroborate(findings: list[Finding]) -> None:
    """Independent agents pointing at the same entity strengthen each other; tags are the shared vocabulary."""
    by_tag: dict[str, set[str]] = {}
    for f in findings:
        if f.kind in ("challenge", "opinion") or SEV_RANK[f.severity] < 1:
            continue
        for t in f.tags:
            by_tag.setdefault(t, set()).add(f.agent)
    for f in findings:
        if f.kind in ("challenge", "opinion") or SEV_RANK[f.severity] < 1:
            continue
        others = sorted({a for t in f.tags for a in by_tag.get(t, ()) if a != f.agent})
        if others:
            f.analysis["corroborated_by"] = others
            f.confidence = round(min(0.95, f.confidence + 0.1), 2)


def _upsert(s, run_id: int, f: Finding, res: AgentResult, started: dt.datetime) -> None:
    row = s.execute(select(AgentFinding).where(AgentFinding.agent == f.agent, AgentFinding.key == f.key)).scalar_one_or_none()
    analysis = {**f.analysis, "mode": f.analysis.get("mode") or res.mode}
    contested = any(c.get("weight") == "major" for c in analysis.get("challenges", []))
    fields = dict(kind=f.kind, title=f.title, severity=f.severity, confidence=f.confidence, summary=f.summary, evidence=f.evidence_dicts(),
                  entities=f.entities, forecast=f.forecast, tags=f.tags, analysis=analysis, contested=contested, run_id=run_id, last_seen=now_utc())
    if row is None:
        row = AgentFinding(agent=f.agent, key=f.key, status="open", **fields)
        s.add(row)
        s.flush()
        _event(s, run_id, row, "new")
        return
    prev, was = row.severity, row.status
    for k, v in fields.items():
        setattr(row, k, v)
    if row.status == "resolved" or (row.status == "dismissed" and SEV_RANK[f.severity] > SEV_RANK[prev]):
        row.status, row.resolved_at = "open", None                # it came back, or got worse after being dismissed
    if was == "resolved":
        _event(s, run_id, row, "reopened", prev)
    elif f.severity != prev:
        _event(s, run_id, row, "escalated" if SEV_RANK[f.severity] > SEV_RANK[prev] else "deescalated", prev)


def _event(s, run_id: int, row: AgentFinding, event: str, prev: str | None = None) -> None:
    if row.kind in ("challenge", "opinion") and event != "new":
        return                                                      # keep the history about real flags, not their commentary
    s.add(AgentEvent(run_id=run_id, finding_id=row.id, agent=row.agent, event=event, title=row.title, severity=row.severity, prev_severity=prev))


def _resolve_missing(s, run_id: int, agent_id: str, seen: set[str]) -> None:
    for row in s.execute(select(AgentFinding).where(AgentFinding.agent == agent_id, AgentFinding.status.in_(["open", "ack"]))).scalars():
        if row.key not in seen:
            row.status, row.resolved_at = "resolved", now_utc()
            if row.kind not in ("challenge", "opinion"):
                s.add(AgentEvent(run_id=run_id, finding_id=row.id, agent=row.agent, event="resolved", title=row.title, severity=row.severity))


# ---------------------------------------------------------------------------------------------- brief
def _brief(gw: LLMGateway, rows: list[AgentFinding], use_llm: bool) -> dict[str, Any]:
    live = [r for r in rows if r.status in ("open", "ack") and r.kind in ("risk", "forecast", "data_gap")]
    top = sorted(live, key=lambda r: (-SEV_RANK[r.severity], -r.confidence))[:10]
    urgent = [r for r in top if SEV_RANK[r.severity] >= 3]
    rules = {"mode": "rules",
             "headline": (f"{len(urgent)} high-severity flag(s) need attention" if urgent else "No high-severity flags; review the medium ones")
                         + (": " + "; ".join(r.title for r in urgent[:3]) if urgent else ""),
             "bullets": [{"text": r.title, "findings": [r.id]} for r in top[:5]]}
    if not (use_llm and top):
        return rules
    internal = any(e.get("origin") == "internal" for r in top for e in r.evidence)
    payload = [{"finding": i, "agent": r.agent, "title": r.title, "severity": r.severity, "summary": r.summary,
                "corroborated_by": r.analysis.get("corroborated_by", []), "contested": r.contested} for i, r in enumerate(top)]
    obj, meta = gw.chat_json("brain", "You are the chief analyst of a manufacturing-intelligence platform. Synthesize the specialists' findings into an executive "
                             "brief for a head of procurement. Use ONLY the findings given; do not add facts.",
                             'Return JSON {"headline":"<=25 words","bullets":[{"text":"<=40 words","findings":[<index>]}],"watch_next":["<=20 words"]}\n'
                             + json.dumps(payload, default=str), internal=internal, max_tokens=900)
    if not obj or not isinstance(obj.get("bullets"), list):
        return rules
    bullets = [{"text": str(b["text"])[:400], "findings": [top[i].id for i in b["findings"] if isinstance(i, int) and 0 <= i < len(top)]}
               for b in obj["bullets"] if isinstance(b, dict) and b.get("text") and isinstance(b.get("findings"), list)]
    bullets = [b for b in bullets if b["findings"]]               # a bullet that cites no finding is dropped
    if not bullets or not obj.get("headline"):
        return rules
    return {"mode": "llm", "model": f"{meta['provider']}/{meta['model']}", "headline": str(obj["headline"])[:300], "bullets": bullets[:6],
            "watch_next": [str(w)[:200] for w in (obj.get("watch_next") or [])[:4]]}
