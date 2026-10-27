"""Material intelligence: discovery by process, grade comparison, prices, alternatives.

Rules
* Alternatives are only drawn from materials that the existing taxonomy lists for the *same*
  manufacturing category (process compatibility evidence), and — when the source material has
  capability flags such as "Can Die Cast" — only from candidates whose same flag is true.
* Factors without verified data (availability, lead time) are reported as "no verified data" and
  their weight is redistributed; nothing is estimated.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ..models import Category, Material, MaterialProcess, Observation
from ..normalize.materials import FAMILY_DISPLAY, METAL_FAMILIES, POLYMER_FAMILIES

TECH_PROPS = ["density", "uts", "yield_strength", "shear_strength", "youngs_modulus", "hardness",
              "thermal_conductivity", "melting_temp", "liquidus_temp", "eject_deflection_temp"]
TRUE = {"true", "1", "yes", "y", "t"}


def material_card(m: Material) -> dict[str, Any]:
    return {"id": m.id, "name": m.name, "family": m.family, "family_label": FAMILY_DISPLAY.get(m.family, m.family),
            "material_type": m.material_type, "grade": m.grade, "designations": m.designations or {},
            "cut_code": m.cut_code, "density_kg_m3": m.density_kg_m3, "original_names": m.original_names or []}


def search_materials(s: Session, q: str | None, family: str | None, category: str | None, limit: int, offset: int):
    stmt = select(Material)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Material.name).like(like), func.lower(Material.grade).like(like),
                              func.lower(Material.material_type).like(like), Material.key.like(like)))
    if family:
        stmt = stmt.where(Material.family == family)
    if category:
        stmt = stmt.join(MaterialProcess, MaterialProcess.material_id == Material.id).join(
            Category, Category.id == MaterialProcess.category_id).where(Category.slug == category).distinct()
    total = s.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = s.execute(stmt.order_by(Material.family, Material.name).limit(limit).offset(offset)).scalars().all()
    ids = [m.id for m in rows]
    latest = latest_prices(s, ids)
    cats = categories_for(s, ids)
    items = []
    for m in rows:
        c = material_card(m)
        c["latest_price"] = latest.get(m.id)
        c["categories"] = cats.get(m.id, [])
        items.append(c)
    return {"total": total, "items": items}


def categories_for(s: Session, ids: list[int]) -> dict[int, list[dict]]:
    if not ids:
        return {}
    out: dict[int, list[dict]] = defaultdict(list)
    seen = set()
    for mid, slug, name in s.execute(select(MaterialProcess.material_id, Category.slug, Category.name)
                                     .join(Category, Category.id == MaterialProcess.category_id)
                                     .where(MaterialProcess.material_id.in_(ids))):
        if (mid, slug) not in seen:
            seen.add((mid, slug))
            out[mid].append({"slug": slug, "name": name})
    return out


def latest_prices(s: Session, ids: list[int], country: str | None = "USA") -> dict[int, dict]:
    """Latest USD/kg price per material (verified first), optionally for one country."""
    if not ids:
        return {}
    q = select(Observation).where(Observation.material_id.in_(ids), Observation.kind == "price",
                                  Observation.unit_normalized == "USD/kg",
                                  Observation.status.in_(["verified", "unverified"]))
    if country:
        q = q.where(or_(Observation.country_iso3 == country, Observation.country_iso3 == "WLD"))
    best: dict[int, Observation] = {}
    for o in s.execute(q).scalars():
        cur = best.get(o.material_id)
        key = (o.status == "verified", o.period_date or dt.date.min, o.confidence)
        if cur is None or key > (cur.status == "verified", cur.period_date or dt.date.min, cur.confidence):
            best[o.material_id] = o
    return {mid: obs_brief(o) for mid, o in best.items()}


def obs_brief(o: Observation) -> dict[str, Any]:
    return {"id": o.id, "value_usd": o.value_usd, "unit": o.unit_normalized, "value": o.value, "unit_original": o.unit,
            "currency": o.currency, "country": o.country_iso3, "period_date": o.period_date.isoformat() if o.period_date else None,
            "status": o.status, "confidence": o.confidence, "flags": o.quality_flags or [], "metric": o.metric,
            "source_id": o.source_id, "dataset_id": o.dataset_id}


def material_detail(s: Session, mid: int) -> dict[str, Any] | None:
    m = s.get(Material, mid)
    if m is None:
        return None
    card = material_card(m)
    card["properties"] = m.properties or {}
    links = s.execute(select(MaterialProcess, Category).join(Category, Category.id == MaterialProcess.category_id)
                      .where(MaterialProcess.material_id == mid)).all()
    card["processes"] = [{"category": c.slug, "name": c.name, "capabilities": mp.capabilities, "dataset_id": mp.dataset_id}
                         for mp, c in links]
    obs = s.execute(select(Observation).where(Observation.material_id == mid, Observation.kind == "price")
                    .order_by(Observation.period_date)).scalars().all()
    card["prices"] = [obs_brief(o) for o in obs]
    by_country: dict[str, list] = defaultdict(list)
    for o in obs:
        if o.status in ("verified", "unverified") and o.unit_normalized == "USD/kg":
            by_country[o.country_iso3 or "-"].append(o)
    card["price_by_country"] = {
        c: obs_brief(max(lst, key=lambda o: (o.status == "verified", o.period_date or dt.date.min)))
        for c, lst in by_country.items()}
    card["history_points"] = len({(o.period_date, o.country_iso3) for o in obs if o.period_date})
    return card


def compare(s: Session, ids: list[int]) -> dict[str, Any]:
    mats = [material_detail(s, i) for i in ids]
    mats = [m for m in mats if m]
    keys = sorted({k for m in mats for k in m["properties"]}, key=lambda k: (k not in TECH_PROPS, k))
    rows = []
    for k in keys:
        vals = [m["properties"].get(k) for m in mats]
        unit = next((v.get("unit") for v in vals if v), None)
        rows.append({"property": k, "unit": unit, "values": [v.get("value") if v else None for v in vals]})
    desig_keys = sorted({k for m in mats for k in (m["designations"] or {})})
    return {"materials": [{k: m[k] for k in ("id", "name", "family", "family_label", "grade", "material_type",
                                             "processes", "price_by_country")} for m in mats],
            "properties": rows,
            "designations": [{"standard": k, "values": [m["designations"].get(k) for m in mats]} for k in desig_keys]}


def _capabilities(s: Session, mid: int, cat_id: int) -> dict[str, bool]:
    caps: dict[str, bool] = {}
    for (c,) in s.execute(select(MaterialProcess.capabilities).where(MaterialProcess.material_id == mid,
                                                                      MaterialProcess.category_id == cat_id)):
        for k, v in (c or {}).items():
            caps[k] = str(v).strip().lower() in TRUE
    return caps


def alternatives(s: Session, mid: int, category: str, weights: dict[str, float] | None = None,
                 country: str = "USA", limit: int = 10, same_family: bool = True) -> dict[str, Any]:
    weights = {"cost": 0.4, "technical": 0.4, "availability": 0.1, "lead_time": 0.1, **(weights or {})}
    ref = s.get(Material, mid)
    cat = s.execute(select(Category).where(Category.slug == category)).scalar_one_or_none()
    if ref is None or cat is None:
        raise ValueError("unknown material or category")
    linked = s.execute(select(MaterialProcess.id).where(MaterialProcess.material_id == mid,
                                                        MaterialProcess.category_id == cat.id)).first()
    if not linked:
        return {"reference": material_card(ref), "category": category, "items": [], "excluded": {},
                "notes": [f"{ref.name} is not listed for {cat.name} in the taxonomy — no compatibility basis for alternatives."]}
    ref_caps = {k: v for k, v in _capabilities(s, mid, cat.id).items() if v}
    cand_ids = [i for (i,) in s.execute(select(MaterialProcess.material_id).where(
        MaterialProcess.category_id == cat.id, MaterialProcess.material_id != mid).distinct())]
    excluded: dict[str, int] = defaultdict(int)
    prices = latest_prices(s, cand_ids + [mid], country)
    ref_price = (prices.get(mid) or {}).get("value_usd")
    ref_props = ref.properties or {}
    items = []
    for m in s.execute(select(Material).where(Material.id.in_(cand_ids))).scalars():
        # hard compatibility rules
        if same_family and m.family != ref.family:
            excluded["different_family"] += 1
            continue
        if (ref.family in METAL_FAMILIES) != (m.family in METAL_FAMILIES) or \
                (ref.family in POLYMER_FAMILIES) != (m.family in POLYMER_FAMILIES):
            excluded["different_material_class"] += 1
            continue
        if ref_caps:
            caps = _capabilities(s, m.id, cat.id)
            if not all(caps.get(k) for k in ref_caps):
                excluded["missing_process_capability"] += 1
                continue
        # technical similarity on properties both materials report
        diffs, compared = [], []
        for p in TECH_PROPS:
            a, b = (ref_props.get(p) or {}).get("value"), ((m.properties or {}).get(p) or {}).get("value")
            if isinstance(a, (int, float)) and isinstance(b, (int, float)) and a:
                diffs.append(min(1.0, abs(b - a) / abs(a)))
                compared.append(p)
        tech = (1 - sum(diffs) / len(diffs)) if diffs else None
        price = (prices.get(m.id) or {}).get("value_usd")
        cost = None
        if price and ref_price:
            cost = max(0.0, min(1.0, 0.5 + (ref_price - price) / (2 * ref_price)))  # 0.5 = same price
        factors = {"cost": cost, "technical": tech, "availability": None, "lead_time": None}
        avail_w = {k: w for k, w in weights.items() if factors.get(k) is not None and w > 0}
        if not avail_w:
            excluded["no_comparable_data"] += 1
            continue
        tw = sum(avail_w.values())
        score = sum(factors[k] * w for k, w in avail_w.items()) / tw
        items.append({**material_card(m), "score": round(score, 3), "factors": factors, "compared_properties": compared,
                      "price": prices.get(m.id), "same_family": m.family == ref.family})
    items.sort(key=lambda x: (-x["score"], not x["same_family"]))
    return {"reference": {**material_card(ref), "price": prices.get(mid), "required_capabilities": list(ref_caps)},
            "category": category, "weights": weights, "items": items[:limit], "excluded": dict(excluded),
            "notes": ["Availability and lead time have no verified data source yet — their weights are redistributed.",
                      "Candidates come only from materials the taxonomy lists for this process."]
            + (["Restricted to the same material family (set same_family=false to widen within the same material class)."] if same_family else [])}


def by_process(s: Session, category: str) -> dict[str, Any]:
    cat = s.execute(select(Category).where(Category.slug == category)).scalar_one_or_none()
    if cat is None:
        raise ValueError("unknown category")
    rows = s.execute(select(Material.family, func.count(func.distinct(Material.id)))
                     .join(MaterialProcess, MaterialProcess.material_id == Material.id)
                     .where(MaterialProcess.category_id == cat.id).group_by(Material.family)).all()
    return {"category": {"slug": cat.slug, "name": cat.name},
            "families": [{"family": f, "label": FAMILY_DISPLAY.get(f, f), "count": n} for f, n in sorted(rows, key=lambda r: -r[1])]}
