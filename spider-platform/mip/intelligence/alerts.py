"""Procurement / price-change / freshness alerts."""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import AlertEvent, AlertRule, Observation, Source
from ..registry.service import RUNNABLE_VERIFICATION, freshness

DEFAULT_RULES = [
    {"name": "FX move > 2% in 30 days (USD/INR)", "kind": "fx_change", "series_key": "USD/INR", "threshold_pct": 2.0, "window_days": 30},
    {"name": "FX move > 2% in 30 days (USD/EUR)", "kind": "fx_change", "series_key": "USD/EUR", "threshold_pct": 2.0, "window_days": 30},
    {"name": "FX move > 3% in 30 days (USD/MXN)", "kind": "fx_change", "series_key": "USD/MXN", "threshold_pct": 3.0, "window_days": 30},
    {"name": "Commodity price change > 5% month-on-month", "kind": "price_change", "threshold_pct": 5.0, "window_days": 40},
    {"name": "Active source stale beyond SLA", "kind": "stale_source"},
    {"name": "Source failing (3+ consecutive failures)", "kind": "source_failure"},
]


def ensure_default_rules(s: Session) -> None:
    if s.execute(select(AlertRule.id).limit(1)).first():
        return
    for r in DEFAULT_RULES:
        s.add(AlertRule(**r))
    s.flush()


def _fire(s: Session, rule: AlertRule, key: str, msg: str, payload: dict, severity="warning") -> bool:
    try:
        with s.begin_nested():
            s.add(AlertEvent(rule_id=rule.id, dedupe_key=key[:200], message=msg, payload=payload, severity=severity))
        return True
    except IntegrityError:
        return False


def _change(points: list[Observation], window: int) -> tuple[Observation, Observation] | None:
    if len(points) < 2:
        return None
    last = points[-1]
    cutoff = last.period_date - dt.timedelta(days=window)
    base = [p for p in points if p.period_date <= cutoff]
    first = base[-1] if base else points[0]
    if first is last or not first.value:
        return None
    return first, last


def evaluate_alerts(s: Session) -> dict[str, Any]:
    ensure_default_rules(s)
    fired = 0
    for rule in s.execute(select(AlertRule).where(AlertRule.active.is_(True))).scalars():
        if rule.kind == "fx_change" and rule.series_key:
            pts = s.execute(select(Observation).where(Observation.kind == "fx", Observation.entity == rule.series_key,
                                                      Observation.source_id.is_not(None),
                                                      Observation.status.in_(["verified", "unverified"]))
                            .order_by(Observation.period_date)).scalars().all()
            c = _change(pts, rule.window_days)
            if c:
                a, b = c
                pct = (b.value - a.value) / a.value * 100
                if abs(pct) >= (rule.threshold_pct or 0):
                    fired += _fire(s, rule, f"{rule.series_key}:{b.period_date}",
                                   f"{rule.series_key} moved {pct:+.2f}% ({a.value:.4g} on {a.period_date} → {b.value:.4g} on {b.period_date})",
                                   {"from": a.value, "to": b.value, "pct": pct, "observation_id": b.id})
        elif rule.kind == "price_change":
            q = select(Observation).where(Observation.kind == "price", Observation.source_id.is_not(None),
                                          Observation.status.in_(["verified", "unverified"]),
                                          Observation.period_date.is_not(None)).order_by(Observation.series_key, Observation.period_date)
            if rule.material_id:
                q = q.where(Observation.material_id == rule.material_id)
            by: dict[str, list[Observation]] = {}
            for o in s.execute(q).scalars():
                by.setdefault(o.series_key, []).append(o)
            for key, pts in by.items():
                c = _change(pts, rule.window_days)
                if not c:
                    continue
                a, b = c
                pct = (b.value - a.value) / a.value * 100
                if abs(pct) >= (rule.threshold_pct or 0):
                    fired += _fire(s, rule, f"{key}:{b.period_date}",
                                   f"{b.entity}: {pct:+.1f}% ({a.value:.4g} → {b.value:.4g} {b.unit or ''}, {a.period_date} → {b.period_date})",
                                   {"series_key": key, "pct": pct, "observation_id": b.id, "material_id": b.material_id})
        elif rule.kind in ("stale_source", "source_failure"):
            for src in s.execute(select(Source).where(Source.status == "active",
                                                      Source.verification_status.in_(RUNNABLE_VERIFICATION))).scalars():
                f = freshness(src)
                if rule.kind == "stale_source" and f == "stale":
                    fired += _fire(s, rule, f"{src.slug}:{dt.date.today()}", f"{src.name} has not refreshed within its {src.freshness_sla_hours} h SLA",
                                   {"source": src.slug}, "info")
                if rule.kind == "source_failure" and f == "failing":
                    fired += _fire(s, rule, f"{src.slug}:{src.consecutive_failures}", f"{src.name} failed {src.consecutive_failures} times in a row: {src.last_error or ''}"[:500],
                                   {"source": src.slug}, "error")
    s.flush()
    return {"fired": fired}
