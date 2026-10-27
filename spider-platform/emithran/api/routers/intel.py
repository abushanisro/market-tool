"""Taxonomy, materials, market, data explorer, alerts, unresolved identifiers."""
from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from ...db import get_session
from ...intelligence import alerts as alerts_mod
from ...intelligence import market, materials
from ...models import (
    AlertEvent, AlertRule, Category, Dataset, DatasetRow, Document, Machine, Material, MaterialProcess,
    Observation, Process, QualityIssue, Source, Standard, TreeNode, UnresolvedIdentifier,
)
from ...normalize.materials import FAMILY_DISPLAY
from ...taxonomy.categories import FAMILY_LABELS
from ...auth import user
from ..deps import iso, require_admin

router = APIRouter(prefix="/api", dependencies=[Depends(user)])


# ---------------------------------------------------------------- taxonomy
@router.get("/taxonomy/categories")
def categories(s: Session = Depends(get_session)):
    cats = s.execute(select(Category).order_by(Category.family, Category.name)).scalars().all()
    mats = dict(s.execute(select(MaterialProcess.category_id, func.count(func.distinct(MaterialProcess.material_id)))
                          .group_by(MaterialProcess.category_id)).all())
    procs = dict(s.execute(select(Process.category_id, func.count()).group_by(Process.category_id)).all())
    machines = dict(s.execute(select(Machine.category_id, func.count()).group_by(Machine.category_id)).all())
    roles: dict[int, dict[str, int]] = {}
    for cid, role, n in s.execute(select(Dataset.category_id, Dataset.role, func.count()).group_by(Dataset.category_id, Dataset.role)):
        roles.setdefault(cid, {})[role] = n
    by_id = {c.id: c for c in cats}
    items = [{"id": c.id, "slug": c.slug, "name": c.name, "original_folder": c.original_folder,
              "tree_node_name": c.tree_node_name, "family": c.family, "family_label": FAMILY_LABELS.get(c.family, c.family),
              "parent": by_id[c.parent_id].slug if c.parent_id in by_id else None, "datasets": c.dataset_count,
              "rows": c.row_count, "materials": mats.get(c.id, 0), "processes": procs.get(c.id, 0),
              "machines": machines.get(c.id, 0), "roles": roles.get(c.id, {})} for c in cats]
    return {"items": items, "families": FAMILY_LABELS}


@router.get("/taxonomy/categories/{slug}")
def category_detail(slug: str, s: Session = Depends(get_session)):
    c = s.execute(select(Category).where(Category.slug == slug)).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, "category not found")
    datasets = s.execute(select(Dataset).where(Dataset.category_id == c.id).order_by(Dataset.role, Dataset.rel_path)).scalars()
    procs = s.execute(select(Process).where(Process.category_id == c.id).order_by(Process.name).limit(500)).scalars()
    mach = s.execute(select(Machine.process_name, func.count(), func.avg(Machine.labor_rate_usd_hr))
                     .where(Machine.category_id == c.id).group_by(Machine.process_name)
                     .order_by(func.count().desc()).limit(25)).all()
    fam = materials.by_process(s, slug)["families"]
    sources = [x.slug for x in s.execute(select(Source)).scalars() if {"*", slug} & set(x.categories or [])]
    return {"slug": c.slug, "name": c.name, "original_folder": c.original_folder, "tree_node_name": c.tree_node_name,
            "family": c.family,
            "datasets": [{"id": d.id, "rel_path": d.rel_path, "role": d.role, "file_type": d.file_type, "rows": d.row_count,
                          "columns": d.columns or [], "country": d.country_iso3} for d in datasets],
            "processes": [{"id": p.id, "name": p.name, "default_machine": p.default_machine,
                           "kinds": (p.attributes or {}).get("kinds", [])} for p in procs],
            "machine_total": s.execute(select(func.count()).select_from(Machine).where(Machine.category_id == c.id)).scalar_one(),
            "machine_groups": [{"process": p or "(unspecified)", "machines": n, "avg_labor_usd_hr": round(l, 2) if l else None}
                               for p, n, l in mach],
            "material_families": fam, "sources": sources}


@router.get("/taxonomy/categories/{slug}/machines")
def category_machines(slug: str, q: str | None = None, process: str | None = None, offset: int = Query(0, ge=0),
                      limit: int = Query(50, ge=1, le=200), s: Session = Depends(get_session)):
    """Machines / work centers of one category with their rates and every spec column kept from the source file.

    ``process`` filters by group ("__none__" = machines whose file names no process). Rates are exactly the values in
    the source (direct/indirect overhead and labour per hour); nothing is derived or combined here.
    """
    c = s.execute(select(Category).where(Category.slug == slug)).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, "category not found")
    base = Machine.category_id == c.id
    groups = s.execute(select(Machine.process_name, func.count(), func.avg(Machine.labor_rate_usd_hr),
                              func.avg(Machine.direct_overhead_usd_hr), func.avg(Machine.indirect_overhead_usd_hr))
                       .where(base).group_by(Machine.process_name).order_by(func.count().desc())).all()
    cond = [base]
    if process == "__none__":
        cond.append(Machine.process_name.is_(None))
    elif process:
        cond.append(Machine.process_name == process)
    if q:
        like = f"%{q.strip()}%"
        cond.append(or_(Machine.name.ilike(like), Machine.process_name.ilike(like), cast(Machine.attributes, String).ilike(like)))
    total = s.execute(select(func.count()).select_from(Machine).where(*cond)).scalar_one()
    rows = s.execute(select(Machine, Dataset.rel_path).join(Dataset, Dataset.id == Machine.dataset_id, isouter=True)
                     .where(*cond).order_by(Machine.process_name, Machine.name, Machine.id).offset(offset).limit(limit)).all()
    r2 = lambda v: round(v, 2) if v is not None else None
    return {"total": total, "offset": offset, "limit": limit,
            "groups": [{"process": g or "(unspecified)", "key": g or "__none__", "machines": n, "avg_labor_usd_hr": r2(l),
                        "avg_direct_overhead_usd_hr": r2(d), "avg_indirect_overhead_usd_hr": r2(i)} for g, n, l, d, i in groups],
            "items": [{"id": m.id, "name": m.name, "process": m.process_name, "country": m.country_iso3, "file": rel,
                       "labor_usd_hr": m.labor_rate_usd_hr, "direct_overhead_usd_hr": m.direct_overhead_usd_hr,
                       "indirect_overhead_usd_hr": m.indirect_overhead_usd_hr, "attributes": m.attributes or {}}
                      for m, rel in rows]}


@router.get("/taxonomy/tree")
def tree(s: Session = Depends(get_session)):
    rows = s.execute(select(TreeNode).order_by(TreeNode.id)).scalars()
    cats = {c.id: c.slug for c in s.execute(select(Category)).scalars()}
    return {"items": [{"level": r.level, "parent": r.parent_name, "name": r.name, "node_type": r.node_type,
                       "category": cats.get(r.category_id)} for r in rows]}


@router.get("/datasets/{dataset_id}")
def dataset_rows(dataset_id: int, q: str | None = None, limit: int = Query(50, le=500), offset: int = 0,
                 s: Session = Depends(get_session)):
    d = s.get(Dataset, dataset_id)
    if d is None:
        raise HTTPException(404, "dataset not found")
    stmt = select(DatasetRow).where(DatasetRow.dataset_id == d.id)
    if q:
        stmt = stmt.where(cast(DatasetRow.data, String).ilike(f"%{q}%"))
    total = s.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = s.execute(stmt.order_by(DatasetRow.row_index).limit(limit).offset(offset)).scalars()
    return {"dataset": {"id": d.id, "rel_path": d.rel_path, "role": d.role, "columns": d.columns, "rows": d.row_count,
                        "checksum": d.checksum, "imported_at": iso(d.imported_at), "notes": d.notes,
                        "category": d.category.slug if d.category else None},
            "total": total, "items": [{"row_index": r.row_index, "data": r.data} for r in rows]}


@router.get("/identifiers")
def identifiers(s: Session = Depends(get_session)):
    return {"items": [{"code": u.code, "status": u.status, "occurrences": u.occurrences, "evidence": u.evidence,
                       "confirmed_definition": u.confirmed_definition, "confirmed_by": u.confirmed_by,
                       "confirmed_at": iso(u.confirmed_at)}
                      for u in s.execute(select(UnresolvedIdentifier)).scalars()]}


class ConfirmIn(BaseModel):
    definition: str
    confirmed_by: str


@router.post("/identifiers/{code}/confirm", dependencies=[Depends(require_admin)])
def confirm_identifier(code: str, body: ConfirmIn, s: Session = Depends(get_session)):
    u = s.execute(select(UnresolvedIdentifier).where(UnresolvedIdentifier.code == code)).scalar_one_or_none()
    if u is None:
        raise HTTPException(404, "identifier not found")
    if len(body.definition.strip()) < 3 or not body.confirmed_by.strip():
        raise HTTPException(400, "definition and confirmed_by are required")
    u.status, u.confirmed_definition, u.confirmed_by = "confirmed", body.definition.strip(), body.confirmed_by.strip()
    u.confirmed_at = dt.datetime.now(dt.timezone.utc)
    s.commit()
    return {"code": u.code, "status": u.status}


# ---------------------------------------------------------------- materials
@router.get("/materials")
def list_materials(q: str | None = None, family: str | None = None, category: str | None = None,
                   limit: int = Query(50, le=500), offset: int = 0, s: Session = Depends(get_session)):
    return materials.search_materials(s, q, family, category, limit, offset)


@router.get("/materials/families")
def families(s: Session = Depends(get_session)):
    rows = s.execute(select(Material.family, func.count()).group_by(Material.family).order_by(func.count().desc())).all()
    return {"items": [{"family": f, "label": FAMILY_DISPLAY.get(f, f), "count": n} for f, n in rows]}


@router.get("/materials/compare")
def compare(ids: str, s: Session = Depends(get_session)):
    id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()][:6]
    if len(id_list) < 2:
        raise HTTPException(400, "provide 2–6 material ids")
    return materials.compare(s, id_list)


@router.get("/materials/by-process/{slug}")
def by_process(slug: str, s: Session = Depends(get_session)):
    try:
        return materials.by_process(s, slug)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.get("/materials/{mid}")
def material(mid: int, s: Session = Depends(get_session)):
    d = materials.material_detail(s, mid)
    if d is None:
        raise HTTPException(404, "material not found")
    return d


@router.get("/materials/{mid}/alternatives")
def alternatives(mid: int, category: str, country: str = "USA", w_cost: float = 0.4, w_technical: float = 0.4,
                 w_availability: float = 0.1, w_lead_time: float = 0.1, same_family: bool = True,
                 limit: int = Query(10, le=50),
                 s: Session = Depends(get_session)):
    try:
        return materials.alternatives(s, mid, category, {"cost": w_cost, "technical": w_technical,
                                                         "availability": w_availability, "lead_time": w_lead_time},
                                      country=country, limit=limit, same_family=same_family)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


# ---------------------------------------------------------------- market
@router.get("/market/fx")
def fx(s: Session = Depends(get_session)):
    return market.fx_overview(s)


@router.get("/market/series")
def series(kind: str = "price", entity: str | None = None, material_id: int | None = None, country: str | None = None,
           metric: str | None = None, verified_only: bool = False, s: Session = Depends(get_session)):
    return market.series(s, kind=kind, entity=entity, material_id=material_id, country=country, metric=metric,
                         include_unverified=not verified_only)


@router.get("/market/indicators")
def indicators(s: Session = Depends(get_session)):
    return market.indicators(s)


@router.get("/market/matrix")
def matrix(s: Session = Depends(get_session)):
    return market.family_country_matrix(s)


@router.get("/market/labor")
def labor(s: Session = Depends(get_session)):
    return {"items": market.labor_rates(s)}


@router.get("/market/freshness")
def mfresh(s: Session = Depends(get_session)):
    return {"items": market.market_freshness(s)}


@router.get("/market/countries/{material_id}")
def material_countries(material_id: int, s: Session = Depends(get_session)):
    return {"items": market.country_material_prices(s, material_id)}


@router.get("/market/benchmarks")
def benchmarks(s: Session = Depends(get_session)):
    """Crawled commodity benchmarks (e.g. Pink Sheet) — empty until such a source has run."""
    rows = s.execute(select(Observation.entity, func.count(), func.max(Observation.period_date))
                     .where(Observation.kind == "price", Observation.source_id.is_not(None))
                     .group_by(Observation.entity)).all()
    return {"items": [{"entity": e, "points": n, "latest": iso(d)} for e, n, d in rows]}


# ---------------------------------------------------------------- data explorer
def obs_row(o: Observation) -> dict[str, Any]:
    return {"id": o.id, "kind": o.kind, "entity": o.entity, "metric": o.metric, "value": o.value, "value_text": o.value_text,
            "unit": o.unit, "currency": o.currency, "value_usd": o.value_usd, "unit_normalized": o.unit_normalized,
            "country": o.country_iso3, "period_date": iso(o.period_date), "observed_at": iso(o.observed_at),
            "status": o.status, "confidence": o.confidence, "flags": o.quality_flags or [], "source_id": o.source_id,
            "dataset_id": o.dataset_id, "material_id": o.material_id, "source_url": o.source_url, "job_id": o.job_id}


@router.get("/data/observations")
def observations(kind: str | None = None, status: str | None = None, source: str | None = None, dataset_id: int | None = None,
                 country: str | None = None, flag: str | None = None, q: str | None = None, origin: str | None = None,
                 limit: int = Query(50, le=500), offset: int = 0, sort: str = "-observed_at", s: Session = Depends(get_session)):
    stmt = select(Observation)
    if kind:
        stmt = stmt.where(Observation.kind == kind)
    if status:
        stmt = stmt.where(Observation.status == status)
    if source:
        stmt = stmt.join(Source, Source.id == Observation.source_id).where(Source.slug == source)
    if origin == "internal":
        stmt = stmt.where(Observation.source_id.is_(None))
    elif origin == "crawled":
        stmt = stmt.where(Observation.source_id.is_not(None))
    if dataset_id:
        stmt = stmt.where(Observation.dataset_id == dataset_id)
    if country:
        stmt = stmt.where(Observation.country_iso3 == country)
    if flag:
        stmt = stmt.where(cast(Observation.quality_flags, String).like(f'%"{flag}"%'))
    if q:
        stmt = stmt.where(or_(Observation.entity.ilike(f"%{q}%"), Observation.metric.ilike(f"%{q}%")))
    total = s.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    col = {"observed_at": Observation.observed_at, "period_date": Observation.period_date, "value": Observation.value,
           "entity": Observation.entity, "confidence": Observation.confidence}.get(sort.lstrip("-"), Observation.observed_at)
    stmt = stmt.order_by(col.desc().nulls_last() if sort.startswith("-") else col.asc().nulls_last(), Observation.id.desc())
    rows = s.execute(stmt.limit(limit).offset(offset)).scalars().all()
    srcs = {x.id: x.slug for x in s.execute(select(Source)).scalars()}
    dss = {d.id: d.rel_path for d in s.execute(select(Dataset).where(Dataset.id.in_({o.dataset_id for o in rows if o.dataset_id}))).scalars()}
    return {"total": total, "items": [{**obs_row(o), "source": srcs.get(o.source_id), "dataset": dss.get(o.dataset_id)} for o in rows]}


@router.get("/data/observations/{oid}")
def observation(oid: int, s: Session = Depends(get_session)):
    o = s.get(Observation, oid)
    if o is None:
        raise HTTPException(404, "observation not found")
    src = s.get(Source, o.source_id) if o.source_id else None
    ds = s.get(Dataset, o.dataset_id) if o.dataset_id else None
    row = None
    if ds and isinstance(o.original, dict) and "row_index" in o.original:
        r = s.execute(select(DatasetRow).where(DatasetRow.dataset_id == ds.id,
                                               DatasetRow.row_index == o.original["row_index"])).scalar_one_or_none()
        row = r.data if r else None
    issues = s.execute(select(QualityIssue).where(QualityIssue.observation_id == o.id)).scalars()
    history = s.execute(select(Observation).where(Observation.series_key == o.series_key)
                        .order_by(Observation.period_date.desc()).limit(30)).scalars()
    mat = s.get(Material, o.material_id) if o.material_id else None
    return {**obs_row(o), "series_key": o.series_key, "original": o.original,
            "source": {"slug": src.slug, "name": src.name, "license": src.license, "attribution": src.attribution,
                       "verification_status": src.verification_status} if src else None,
            "dataset": {"id": ds.id, "rel_path": ds.rel_path, "role": ds.role} if ds else None, "source_row": row,
            "material": materials.material_card(mat) if mat else None,
            "issues": [{"severity": i.severity, "code": i.code, "message": i.message} for i in issues],
            "history": [obs_row(h) for h in history]}


@router.get("/data/issues")
def issues(severity: str | None = None, code: str | None = None, source: str | None = None,
           limit: int = Query(50, le=500), offset: int = 0, s: Session = Depends(get_session)):
    stmt = select(QualityIssue)
    if severity:
        stmt = stmt.where(QualityIssue.severity == severity)
    if code:
        stmt = stmt.where(QualityIssue.code == code)
    if source:
        stmt = stmt.join(Source, Source.id == QualityIssue.source_id).where(Source.slug == source)
    total = s.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    codes = dict(s.execute(select(QualityIssue.code, func.count()).group_by(QualityIssue.code)).all())
    rows = s.execute(stmt.order_by(QualityIssue.id.desc()).limit(limit).offset(offset)).scalars()
    dss = {}
    out = []
    for i in rows:
        if i.dataset_id and i.dataset_id not in dss:
            d = s.get(Dataset, i.dataset_id)
            dss[i.dataset_id] = d.rel_path if d else None
        out.append({"id": i.id, "created_at": iso(i.created_at), "severity": i.severity, "code": i.code, "message": i.message,
                    "observation_id": i.observation_id, "dataset": dss.get(i.dataset_id), "source_id": i.source_id,
                    "job_id": i.job_id, "context": i.context})
    return {"total": total, "codes": codes, "items": out}


@router.get("/data/documents")
def documents(kind: str | None = None, q: str | None = None, limit: int = Query(50, le=500), offset: int = 0,
              s: Session = Depends(get_session)):
    stmt = select(Document)
    if kind:
        stmt = stmt.where(Document.kind == kind)
    if q:
        stmt = stmt.where(Document.title.ilike(f"%{q}%"))
    total = s.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    srcs = {x.id: x.slug for x in s.execute(select(Source)).scalars()}
    rows = s.execute(stmt.order_by(Document.published_at.desc().nulls_last()).limit(limit).offset(offset)).scalars()
    return {"total": total, "items": [{"id": d.id, "kind": d.kind, "title": d.title, "url": d.url, "published_at": iso(d.published_at),
                                       "authors": d.authors, "doi": d.doi, "source": srcs.get(d.source_id),
                                       "observed_at": iso(d.observed_at), "flags": (d.raw or {}).get("flags", [])} for d in rows]}


@router.get("/data/standards")
def standards(q: str | None = None, s: Session = Depends(get_session)):
    stmt = select(Standard)
    if q:
        stmt = stmt.where(or_(Standard.code.ilike(f"%{q}%"), Standard.title.ilike(f"%{q}%")))
    rows = s.execute(stmt.order_by(Standard.body, Standard.code).limit(500)).scalars()
    return {"items": [{"id": x.id, "code": x.code, "body": x.body, "title": x.title, "status": x.status, "url": x.url,
                       "dataset_id": x.dataset_id, "source_id": x.source_id} for x in rows]}


# ---------------------------------------------------------------- alerts
@router.get("/alerts")
def list_alerts(include_ack: bool = False, s: Session = Depends(get_session)):
    alerts_mod.ensure_default_rules(s)
    s.commit()
    stmt = select(AlertEvent, AlertRule).join(AlertRule, AlertRule.id == AlertEvent.rule_id)
    if not include_ack:
        stmt = stmt.where(AlertEvent.acknowledged.is_(False))
    rows = s.execute(stmt.order_by(AlertEvent.triggered_at.desc()).limit(100)).all()
    rules = s.execute(select(AlertRule)).scalars()
    return {"events": [{"id": e.id, "rule": r.name, "kind": r.kind, "severity": e.severity, "message": e.message,
                        "triggered_at": iso(e.triggered_at), "payload": e.payload, "acknowledged": e.acknowledged} for e, r in rows],
            "rules": [{"id": r.id, "name": r.name, "kind": r.kind, "series_key": r.series_key, "threshold_pct": r.threshold_pct,
                       "window_days": r.window_days, "active": r.active, "material_id": r.material_id} for r in rules]}


class RuleIn(BaseModel):
    name: str
    kind: str
    series_key: str | None = None
    material_id: int | None = None
    threshold_pct: float | None = None
    window_days: int = 30


@router.post("/alerts/rules", dependencies=[Depends(require_admin)])
def add_rule(body: RuleIn, s: Session = Depends(get_session)):
    if body.kind not in {"price_change", "fx_change", "stale_source", "source_failure"}:
        raise HTTPException(400, "unknown rule kind")
    r = AlertRule(**body.model_dump())
    s.add(r)
    s.commit()
    return {"id": r.id}


@router.post("/alerts/evaluate", dependencies=[Depends(require_admin)])
def evaluate(s: Session = Depends(get_session)):
    res = alerts_mod.evaluate_alerts(s)
    s.commit()
    return res


@router.post("/alerts/{eid}/ack", dependencies=[Depends(require_admin)])
def ack(eid: int, s: Session = Depends(get_session)):
    e = s.get(AlertEvent, eid)
    if e is None:
        raise HTTPException(404, "alert not found")
    e.acknowledged = True
    s.commit()
    return {"ok": True}
