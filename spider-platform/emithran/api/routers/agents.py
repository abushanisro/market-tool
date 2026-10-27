"""Emithran Brain API: agents, findings (flags), runs, risk overview and LLM model setup."""
from __future__ import annotations

import datetime as dt
import threading
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from ...agents import brain
from ...agents.core import SEV_RANK
from ...agents.llm import ALLOW_HOSTED_KEY, ASSIGN_KEY, LLMGateway
from ...db import get_session, session_scope
from ...models import AgentEvent, AgentFinding, AgentRun, AppSetting
from ...auth import user
from ..deps import iso, require_admin, require_editor

router = APIRouter(prefix="/api/agents", tags=["agents"], dependencies=[Depends(user)])
STALE_RUN = dt.timedelta(minutes=15)


def finding_dict(f: AgentFinding, full: bool = False) -> dict[str, Any]:
    d = {"id": f.id, "agent": f.agent, "agent_name": brain.BY_ID[f.agent].name if f.agent in brain.BY_ID else f.agent, "key": f.key,
         "kind": f.kind, "title": f.title, "severity": f.severity, "confidence": f.confidence, "status": f.status,
         "contested": f.contested, "has_forecast": _credible(f.forecast), "tags": f.tags, "first_seen": iso(f.first_seen), "last_seen": iso(f.last_seen),
         "corroborated_by": (f.analysis or {}).get("corroborated_by", []), "mode": (f.analysis or {}).get("mode", "rules"),
         "summary": f.summary}
    if full:
        d.update(evidence=f.evidence, entities=f.entities, forecast=f.forecast, analysis=f.analysis, resolved_at=iso(f.resolved_at))
    return d


def _credible(fc: dict | None) -> bool:
    """A forecast is only advertised when it beat the naive baseline (or is explicitly indicative)."""
    if not fc:
        return False
    skill = (fc.get("backtest") or {}).get("skill")
    return bool(fc.get("indicative") or (skill is not None and skill > 0.1))


def run_dict(r: AgentRun | None) -> dict[str, Any] | None:
    if r is None:
        return None
    return {"id": r.id, "trigger": r.trigger, "status": r.status, "mode": r.mode, "started_at": iso(r.started_at),
            "finished_at": iso(r.finished_at), "agents": r.agents, "error": r.error}


def _last_run(s: Session) -> AgentRun | None:
    return s.execute(select(AgentRun).order_by(AgentRun.id.desc()).limit(1)).scalar_one_or_none()


@router.get("")
def agents(s: Session = Depends(get_session)):
    last = s.execute(select(AgentRun).where(AgentRun.status != "running").order_by(AgentRun.id.desc()).limit(1)).scalar_one_or_none()
    rows = s.execute(select(AgentFinding).where(AgentFinding.status.in_(["open", "ack"]))).scalars().all()
    out = []
    for a in brain.agent_specs():
        mine = [r for r in rows if r.agent == a["id"] and r.kind != "challenge"]
        counts = {sev: sum(1 for r in mine if r.severity == sev) for sev in ("critical", "high", "medium", "low", "info")}
        out.append({**a, "counts": counts, "open": len(mine), "last": (last.agents or {}).get(a["id"]) if last else None})
    return {"agents": out, "last_run": run_dict(last), "running": _last_run(s) is not None and _last_run(s).status == "running"}


@router.get("/overview")
def overview(s: Session = Depends(get_session)):
    rows = s.execute(select(AgentFinding)).scalars().all()
    live = [r for r in rows if r.status in ("open", "ack")]
    last = s.execute(select(AgentRun).where(AgentRun.status != "running").order_by(AgentRun.id.desc()).limit(1)).scalar_one_or_none()
    running = s.execute(select(AgentRun).where(AgentRun.status == "running").order_by(AgentRun.id.desc()).limit(1)).scalar_one_or_none()
    risk = brain.compute_risk(rows)
    top = sorted([r for r in live if r.kind in ("risk", "data_gap") and SEV_RANK[r.severity] >= 2], key=lambda r: (-SEV_RANK[r.severity], -r.confidence))[:10]
    by_id = {r.id: r for r in rows}
    brief = dict((last.summary or {}).get("brief") or {}) if last else {}
    for b in brief.get("bullets", []):
        b["items"] = [{"id": i, "title": by_id[i].title, "severity": by_id[i].severity} for i in b.get("findings", []) if i in by_id]
    fcs, evaluated = [], 0
    for r in live:
        f = r.forecast
        if f and f.get("points") and f.get("history") and not f.get("indicative") and (f.get("backtest") or {}).get("n", 0) > 0:
            evaluated += 1                                  # volatility cones (FX) make no directional claim and are never back-tested
            if not _credible(f):
                continue                                    # a forecast that does not beat the naive baseline is not shown as a forecast
            p = f["points"][-1]
            last_v = f["history"][-1]["value"]
            fcs.append({"id": r.id, "title": r.title, "agent": r.agent, "unit": f.get("unit"), "last": last_v, "p50": p["p50"], "lo80": p["lo80"],
                        "hi80": p["hi80"], "horizon": f.get("horizon"), "change_pct": round((p["p50"] - last_v) / last_v * 100, 1) if last_v else None,
                        "skill": (f.get("backtest") or {}).get("skill"), "method": f.get("method"), "severity": r.severity})
    gw = LLMGateway()
    status = gw.status()
    return {"risk": risk, "brief": brief, "top": [finding_dict(r) for r in top],
            "contested": [finding_dict(r) for r in live if r.contested][:10],
            "forecasts": sorted(fcs, key=lambda x: -abs(x["change_pct"] or 0))[:12], "forecast_stats": {"evaluated": evaluated, "credible": len(fcs)},
            "last_run": run_dict(last), "running": run_dict(running),
            "llm": {"providers_configured": sum(1 for p in status["providers"] if p["configured"] and not p["local"]),
                    "assigned_agents": sorted(status["assignments"]), "allow_hosted_internal": status["allow_hosted_internal"]}}


@router.get("/findings")
def findings(agent: str | None = None, severity: str | None = None, status: str = "live", kind: str | None = None, q: str | None = None,
             contested: bool | None = None, min_severity: str | None = None, limit: int = Query(200, le=500), offset: int = Query(0, ge=0),
             s: Session = Depends(get_session)):
    cond = []
    if agent:
        cond.append(AgentFinding.agent == agent)
    if severity:
        cond.append(AgentFinding.severity == severity)
    if kind:
        cond.append(AgentFinding.kind == kind)
    elif status == "live":
        cond.append(AgentFinding.kind != "challenge")
    if status == "live":
        cond.append(AgentFinding.status.in_(["open", "ack"]))
    elif status != "all":
        cond.append(AgentFinding.status == status)
    if contested is not None:
        cond.append(AgentFinding.contested.is_(contested))
    if q:
        like = f"%{q}%"
        cond.append(or_(AgentFinding.title.ilike(like), AgentFinding.summary.ilike(like), cast(AgentFinding.tags, String).ilike(like)))
    rows = s.execute(select(AgentFinding).where(*cond)).scalars().all()
    rows.sort(key=lambda r: (-SEV_RANK[r.severity], -r.confidence, r.title))
    if min_severity in SEV_RANK:
        rows = [r for r in rows if SEV_RANK[r.severity] >= SEV_RANK[min_severity]]
    return {"total": len(rows), "offset": offset, "limit": limit, "items": [finding_dict(r) for r in rows[offset:offset + limit]]}


@router.get("/findings/{fid}")
def finding(fid: int, s: Session = Depends(get_session)):
    f = s.get(AgentFinding, fid)
    if f is None:
        raise HTTPException(404, "finding not found")
    d = finding_dict(f, full=True)
    target = (f.entities or {}).get("target")
    d["challenges_of_this"] = [finding_dict(c) for c in s.execute(select(AgentFinding).where(
        AgentFinding.agent == "red_team", AgentFinding.key == f"challenge:{f.agent}:{f.key}")).scalars()]
    d["target"] = target
    return d


class StatusIn(BaseModel):
    status: str = Field(pattern="^(open|ack|dismissed)$")


@router.post("/findings/{fid}/status", dependencies=[Depends(require_editor)])
def set_status(fid: int, body: StatusIn, s: Session = Depends(get_session)):
    f = s.get(AgentFinding, fid)
    if f is None:
        raise HTTPException(404, "finding not found")
    f.status = body.status
    s.commit()
    return finding_dict(f)


class RunIn(BaseModel):
    agents: list[str] | None = None
    use_llm: bool | None = None


@router.post("/run", dependencies=[Depends(require_editor)])
def run(body: RunIn, s: Session = Depends(get_session)):
    bad = [a for a in (body.agents or []) if a not in brain.BY_ID]
    if bad:
        raise HTTPException(422, f"unknown agent(s): {', '.join(bad)}")
    cur = _last_run(s)
    if cur and cur.status == "running" and (dt.datetime.now(dt.timezone.utc) - cur.started_at.replace(tzinfo=dt.timezone.utc)) < STALE_RUN:
        raise HTTPException(409, f"run {cur.id} is already in progress")
    rid = brain.start_run("manual")
    threading.Thread(target=brain.run_brain, kwargs={"trigger": "manual", "agent_ids": body.agents, "use_llm": body.use_llm, "run_id": rid},
                     daemon=True, name=f"brain-{rid}").start()
    return {"run_id": rid, "status": "running"}


@router.get("/activity")
def activity(page: int = Query(1, ge=1), size: int = Query(25, ge=5, le=100), agent: str | None = None, event: str | None = None,
             s: Session = Depends(get_session)):
    """Append-only history: every flag that appeared, got worse or better, resolved or came back. Newest first, paged."""
    cond = [*( [AgentEvent.agent == agent] if agent else []), *( [AgentEvent.event == event] if event else [])]
    total = s.execute(select(func.count()).select_from(AgentEvent).where(*cond)).scalar_one()
    rows = s.execute(select(AgentEvent).where(*cond).order_by(AgentEvent.id.desc()).offset((page - 1) * size).limit(size)).scalars().all()
    return {"total": total, "page": page, "size": size, "pages": max(1, -(-total // size)),
            "items": [{"id": e.id, "ts": iso(e.ts), "run_id": e.run_id, "finding_id": e.finding_id, "agent": e.agent,
                       "agent_name": brain.BY_ID[e.agent].name if e.agent in brain.BY_ID else e.agent, "event": e.event, "title": e.title,
                       "severity": e.severity, "prev_severity": e.prev_severity} for e in rows]}


@router.get("/runs")
def runs(limit: int = Query(20, le=100), s: Session = Depends(get_session)):
    rows = s.execute(select(AgentRun).order_by(AgentRun.id.desc()).limit(limit)).scalars().all()
    return {"items": [{**run_dict(r), "summary": r.summary} for r in rows]}


@router.get("/runs/{rid}")
def run_status(rid: int, s: Session = Depends(get_session)):
    r = s.get(AgentRun, rid)
    if r is None:
        raise HTTPException(404, "run not found")
    return {**run_dict(r), "summary": r.summary}


@router.get("/llm")
def llm_status(probe: bool = False):
    return {**LLMGateway().status(probe=probe), "agent_ids": ["brain"] + [a["id"] for a in brain.agent_specs()]}


class Assignment(BaseModel):
    provider: str
    model: str = Field(min_length=1, max_length=200)


class AssignIn(BaseModel):
    assignments: dict[str, list[Assignment]] | None = None     # {"*": [...], "commodity": [...], "brain": [...]}
    allow_hosted_internal: bool | None = None


@router.post("/llm/assign", dependencies=[Depends(require_admin)])
def llm_assign(body: AssignIn, s: Session = Depends(get_session)):
    gw = LLMGateway()
    if body.assignments is not None:
        valid = {"*", "brain"} | {a["id"] for a in brain.agent_specs()}
        for k, v in body.assignments.items():
            if k not in valid:
                raise HTTPException(422, f"unknown agent '{k}'")
            for a in v:
                if a.provider not in gw.providers:
                    raise HTTPException(422, f"unknown provider '{a.provider}'")
        _save(s, ASSIGN_KEY, {k: [a.model_dump() for a in v] for k, v in body.assignments.items() if v})
    if body.allow_hosted_internal is not None:
        _save(s, ALLOW_HOSTED_KEY, body.allow_hosted_internal)
    s.commit()
    return LLMGateway().status()


def _save(s: Session, key: str, value: Any) -> None:
    row = s.get(AppSetting, key)
    if row is None:
        s.add(AppSetting(key=key, value=value))
    else:
        row.value = value
