"""Shared structures for every agent: evidence-backed findings, the agent base class and LLM grounding rules."""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from .llm import LLMGateway

SEVERITIES = ["info", "low", "medium", "high", "critical"]
SEV_RANK = {s: i for i, s in enumerate(SEVERITIES)}


@dataclass
class Evidence:
    """One fact a finding rests on. ``origin`` decides whether it may leave the machine (internal = taxonomy data)."""

    id: str
    label: str
    value: Any
    unit: str | None = None
    source: str | None = None
    as_of: str | None = None
    origin: str = "public"            # public | internal
    url: str | None = None


@dataclass
class Finding:
    agent: str
    key: str
    kind: str                          # risk | forecast | data_gap | opinion | challenge
    title: str
    severity: str
    confidence: float
    summary: str
    evidence: list[Evidence] = field(default_factory=list)
    entities: dict[str, Any] = field(default_factory=dict)
    forecast: dict | None = None
    tags: list[str] = field(default_factory=list)
    analysis: dict[str, Any] = field(default_factory=lambda: {"mode": "rules"})

    def internal(self) -> bool:
        return any(e.origin == "internal" for e in self.evidence)

    def evidence_dicts(self) -> list[dict]:
        return [asdict(e) for e in self.evidence]


@dataclass
class Ctx:
    session: Session
    gateway: LLMGateway
    use_llm: bool
    now: Any
    prior: list[Finding] = field(default_factory=list)   # findings of agents that ran earlier (red team reads these)


@dataclass
class AgentResult:
    agent: str
    findings: list[Finding]
    mode: str = "rules"                # rules | llm
    model: str | None = None
    seconds: float = 0.0
    error: str | None = None
    notes: list[str] = field(default_factory=list)


def clamp_sev(base: str, wanted: str) -> str:
    """An LLM may move a severity by at most one step; anything bigger is clipped."""
    b, w = SEV_RANK[base], SEV_RANK.get(wanted, SEV_RANK[base])
    return SEVERITIES[b + max(-1, min(1, w - b))]


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


class Agent:
    """A specialist with one job. Subclasses implement ``collect`` (deterministic, evidence-backed)."""

    id = "agent"
    name = "Agent"
    job = ""                           # one sentence: the only thing this agent does
    domain = "general"
    persona = ""                       # system prompt that gives the agent its distinct skill and temperament
    max_llm_findings = 12

    def collect(self, ctx: Ctx) -> list[Finding]:
        raise NotImplementedError

    # ------------------------------------------------------------------ orchestration hook
    def run(self, ctx: Ctx) -> AgentResult:
        t0 = time.time()
        res = AgentResult(agent=self.id, findings=[])
        try:
            res.findings = self.collect(ctx)
        except Exception as exc:       # one failing agent never stops the others
            res.error = f"{type(exc).__name__}: {exc}"[:500]
            return res
        if ctx.use_llm and res.findings:
            try:
                self.enrich(ctx, res)
            except Exception as exc:
                res.notes.append(f"LLM enrichment failed: {type(exc).__name__}")
        res.seconds = round(time.time() - t0, 2)
        return res

    # ------------------------------------------------------------------ LLM opinion layer (optional, grounded)
    def enrich(self, ctx: Ctx, res: AgentResult) -> None:
        top = sorted(res.findings, key=lambda f: -SEV_RANK[f.severity])[: self.max_llm_findings]
        internal = any(f.internal() for f in top)
        if not ctx.gateway.available(self.id, internal=internal):
            _, skipped = ctx.gateway.candidates(self.id, internal=internal)
            res.notes += skipped or ["no model assigned"]
            return
        payload = [{"finding": i, "title": f.title, "severity": f.severity, "summary": f.summary,
                    "evidence": [{"id": e.id, "label": e.label, "value": e.value, "unit": e.unit, "as_of": e.as_of} for e in f.evidence]}
                   for i, f in enumerate(top)]
        system = (self.persona + "\nRules: use ONLY the evidence given; never introduce numbers, names or events that are not in it. "
                  "Every opinion must cite evidence ids from that finding. Be concise.")
        user = ("Review these findings from your own analysis. Return JSON: "
                '{"opinions":[{"finding":<index>,"stance":"agree|upgrade|downgrade","severity":"info|low|medium|high|critical",'
                '"rationale":"<=60 words","cites":["<evidence id>"]}],'
                '"watch":[{"title":"...","rationale":"<=60 words","finding":<index>,"cites":["<evidence id>"]}]}\n\n'
                + json.dumps(payload, default=str))
        obj, meta = ctx.gateway.chat_json(self.id, system, user, internal=internal)
        if obj is None:
            res.notes.append("no model returned usable JSON: " + "; ".join(f"{a['provider']}:{a['error']}" for a in meta["attempts"]))
            return
        res.mode, res.model = "llm", f"{meta['provider']}/{meta['model']}"
        for op in obj.get("opinions") or []:
            f = _get(top, op.get("finding"))
            if f is None or not _grounded(f, op.get("cites")):
                continue                                   # ungrounded opinions are discarded, never shown
            before = f.severity
            stance = op.get("stance")
            if stance in ("upgrade", "downgrade"):
                f.severity = clamp_sev(f.severity, op.get("severity", f.severity))
            f.analysis = {**f.analysis, "mode": "llm", "model": res.model, "stance": stance,
                          "opinion": str(op.get("rationale", ""))[:600], "cites": op.get("cites"),
                          "severity_rules": before}
        for w in (obj.get("watch") or [])[:3]:
            f = _get(top, w.get("finding"))
            if f is None or not _grounded(f, w.get("cites")) or not w.get("title"):
                continue
            cited = [e for e in f.evidence if e.id in set(w["cites"])]
            res.findings.append(Finding(agent=self.id, key=f"watch:{slug(str(w['title']))}", kind="opinion", title=str(w["title"])[:200],
                                        severity="info", confidence=0.4, summary=str(w.get("rationale", ""))[:600], evidence=cited,
                                        entities=f.entities, tags=f.tags, analysis={"mode": "llm", "model": res.model}))


def _get(items: list[Finding], idx: Any) -> Finding | None:
    return items[idx] if isinstance(idx, int) and 0 <= idx < len(items) else None


def _grounded(f: Finding, cites: Any) -> bool:
    ids = {e.id for e in f.evidence}
    return isinstance(cites, list) and bool(cites) and all(c in ids for c in cites)
