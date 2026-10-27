"""Market intelligence: price series, country comparisons, FX, indicators, labour/overhead rates."""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from statistics import median
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Country, Machine, Observation, Source
from ..normalize.countries import NAME_BY_ISO3


def series(s: Session, *, kind: str, entity: str | None = None, material_id: int | None = None,
           country: str | None = None, metric: str | None = None, include_unverified: bool = True,
           start: dt.date | None = None) -> dict[str, Any]:
    q = select(Observation).where(Observation.kind == kind)
    if entity:
        q = q.where(Observation.entity == entity)
    if material_id:
        q = q.where(Observation.material_id == material_id)
    if country:
        q = q.where(Observation.country_iso3 == country)
    if metric:
        q = q.where(Observation.metric == metric)
    if start:
        q = q.where(Observation.period_date >= start)
    statuses = ["verified", "unverified"] if include_unverified else ["verified"]
    q = q.where(Observation.status.in_(statuses), Observation.period_date.is_not(None)).order_by(Observation.period_date)
    groups: dict[str, list] = defaultdict(list)
    meta: dict[str, dict] = {}
    for o in s.execute(q).scalars():
        groups[o.series_key].append({"date": o.period_date.isoformat(), "value": o.value,
                                     "value_usd": o.value_usd, "status": o.status, "flags": o.quality_flags or [], "id": o.id})
        meta[o.series_key] = {"entity": o.entity, "metric": o.metric, "country": o.country_iso3, "unit": o.unit,
                              "unit_normalized": o.unit_normalized, "source_id": o.source_id, "dataset_id": o.dataset_id}
    out = []
    for k, pts in groups.items():
        first, last = pts[0]["value"], pts[-1]["value"]
        chg = ((last - first) / first * 100) if first else None
        out.append({"key": k, **meta[k], "points": pts, "change_pct": round(chg, 2) if chg is not None else None,
                    "latest": pts[-1]})
    return {"series": out}


def fx_overview(s: Session, base: str = "USD") -> dict[str, Any]:
    """Latest crawled USD/xxx rates vs. 30-day-ago and vs. the internal exchange_rate_table."""
    rows = s.execute(select(Observation).where(Observation.kind == "fx", Observation.entity.like(f"{base}/%"),
                                               Observation.status.in_(["verified", "unverified"]))
                     .order_by(Observation.period_date)).scalars().all()
    by: dict[str, dict[str, list]] = defaultdict(lambda: {"crawled": [], "internal": []})
    for o in rows:
        bucket = "internal" if o.source_id is None else "crawled"
        by[o.entity][bucket].append(o)
    out = []
    today = dt.date.today()
    for pair, d in sorted(by.items()):
        cr, it = d["crawled"], d["internal"]
        latest = cr[-1] if cr else None
        prev30 = None
        if latest:
            cutoff = latest.period_date - dt.timedelta(days=30)
            older = [o for o in cr if o.period_date <= cutoff]
            prev30 = older[-1] if older else (cr[0] if len(cr) > 1 else None)
        internal = it[-1] if it else None
        rec = {"pair": pair, "currency": pair.split("/")[1],
               "latest": latest.value if latest else None,
               "latest_date": latest.period_date.isoformat() if latest else None,
               "latest_source_id": latest.source_id if latest else None,
               "replayed": bool(latest and "replayed" in (latest.quality_flags or [])),
               "change_30d_pct": round((latest.value - prev30.value) / prev30.value * 100, 2) if latest and prev30 and prev30.value else None,
               "internal_table_rate": internal.value if internal else None,
               "internal_table_date": internal.period_date.isoformat() if internal and internal.period_date else None,
               "internal_vs_latest_pct": round((internal.value - latest.value) / latest.value * 100, 2)
               if internal and latest and latest.value else None,
               "stale": (today - latest.period_date).days > 5 if latest else True,
               "points": [{"date": o.period_date.isoformat(), "value": o.value} for o in cr[-120:]]}
        out.append(rec)
    return {"base": base, "pairs": out}


def country_material_prices(s: Session, material_id: int) -> list[dict[str, Any]]:
    rows = s.execute(select(Observation).where(Observation.material_id == material_id, Observation.kind == "price",
                                               Observation.unit_normalized == "USD/kg",
                                               Observation.status.in_(["verified", "unverified"]))).scalars().all()
    by: dict[str, list] = defaultdict(list)
    for o in rows:
        by[o.country_iso3 or "-"].append(o)
    out = []
    for c, lst in by.items():
        vals = [o.value_usd for o in lst if o.value_usd]
        if not vals:
            continue
        out.append({"country": c, "country_name": NAME_BY_ISO3.get(c, c), "median_usd_kg": round(median(vals), 4),
                    "min_usd_kg": round(min(vals), 4), "max_usd_kg": round(max(vals), 4), "observations": len(vals),
                    "verified": sum(1 for o in lst if o.status == "verified")})
    return sorted(out, key=lambda r: r["median_usd_kg"])


def family_country_matrix(s: Session) -> dict[str, Any]:
    """Median USD/kg by material family × country — the regional benchmark grid."""
    from ..models import Material

    rows = s.execute(select(Material.family, Observation.country_iso3, Observation.value_usd)
                     .join(Material, Material.id == Observation.material_id)
                     .where(Observation.kind == "price", Observation.unit_normalized == "USD/kg",
                            Observation.status.in_(["verified", "unverified"]), Observation.value_usd.is_not(None))).all()
    grid: dict[tuple[str, str], list[float]] = defaultdict(list)
    for fam, c, v in rows:
        grid[(fam, c or "-")].append(v)
    countries = sorted({c for _, c in grid}, key=lambda c: -sum(len(v) for (f, cc), v in grid.items() if cc == c))
    fams = sorted({f for f, _ in grid}, key=lambda f: -sum(len(v) for (ff, c), v in grid.items() if ff == f))
    return {"countries": countries, "families": fams,
            "cells": [{"family": f, "country": c, "median": round(median(v), 3), "n": len(v)} for (f, c), v in grid.items()]}


def labor_rates(s: Session) -> list[dict[str, Any]]:
    # zero/blank rates mean "not set" in the source files — excluded from averages
    rows = s.execute(select(Machine.country_iso3, func.count(Machine.id),
                            func.avg(func.nullif(Machine.labor_rate_usd_hr, 0)),
                            func.avg(func.nullif(Machine.direct_overhead_usd_hr, 0)),
                            func.avg(func.nullif(Machine.indirect_overhead_usd_hr, 0)))
                     .where(Machine.country_iso3.is_not(None)).group_by(Machine.country_iso3)).all()
    names = {c.iso3: c.name for c in s.execute(select(Country)).scalars()}
    out = []
    for iso, n, lab, dov, iov in rows:
        out.append({"country": iso, "country_name": names.get(iso, iso), "machines": n,
                    "avg_labor_usd_hr": round(lab, 2) if lab else None,
                    "avg_direct_overhead_usd_hr": round(dov, 2) if dov else None,
                    "avg_indirect_overhead_usd_hr": round(iov, 2) if iov else None})
    return sorted(out, key=lambda r: -r["machines"])


def indicators(s: Session) -> dict[str, Any]:
    res = series(s, kind="indicator")
    return res


def market_freshness(s: Session) -> list[dict[str, Any]]:
    from ..registry.service import freshness

    out = []
    for src in s.execute(select(Source).where(Source.data_kinds.is_not(None))).scalars():
        kinds = src.data_kinds or []
        if not {"price", "fx", "indicator", "trade"} & set(kinds):
            continue
        latest = s.execute(select(func.max(Observation.period_date)).where(Observation.source_id == src.id)).scalar_one()
        out.append({"source": src.slug, "name": src.name, "kinds": kinds, "freshness": freshness(src),
                    "last_success_at": src.last_success_at.isoformat() if src.last_success_at else None,
                    "latest_period": latest.isoformat() if latest else None,
                    "verification_status": src.verification_status, "status": src.status})
    return out
