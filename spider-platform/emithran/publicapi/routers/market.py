"""Market Intelligence · Material Intelligence."""
from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Body, Depends, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import get_session
from ...intelligence import market, materials
from ...models import Category, Material, Observation
from ..auth import scoped
from ..common import jsonable, parse_rid, rid
from ..errors import invalid, not_found
from .ontologies import ext

R = scoped("api:market-read")
router = APIRouter(prefix="/api/v2")
G = "Market Intelligence"


def _mat(s: Session, value: str) -> Material:
    key = parse_rid(value, "material")
    m = s.get(Material, int(key)) if key.isdigit() else None
    if m is None:
        raise not_found("MaterialNotFound", materialRid=value)
    return m


@router.get("/market/fxRates", summary="Get FX Rates", openapi_extra=ext(G, "Exchange Rates", "api:market-read"),
            description="Latest crawled exchange rate per currency against USD with the 30-day change, compared with the internal "
                        "`exchange_rate_table.csv` value and its date. `stale` is true when the latest point is older than 5 days.")
def fx_rates(currencies: list[str] | None = Query(None, description="Repeatable ISO 4217 filter, e.g. INR."),
             s: Session = Depends(get_session), _=Depends(R)):
    data = market.fx_overview(s)["pairs"]
    if currencies:
        want = {c.upper() for c in currencies}
        data = [p for p in data if p["currency"] in want]
    return {"base": "USD", "data": [{k: v for k, v in p.items() if k != "points"} for p in data]}


@router.get("/market/fxRates/{currency}/series", summary="Get FX Rate Series", openapi_extra=ext(G, "Exchange Rates", "api:market-read"),
            description="Daily USD/{currency} observations from crawled sources between `startDate` and `endDate` (inclusive).")
def fx_series(currency: str = Path(..., description="ISO 4217 code.", examples=["INR"]),
              startDate: dt.date | None = Query(None, description="ISO date, default 1 year ago."),
              endDate: dt.date | None = Query(None, description="ISO date, default today."),
              s: Session = Depends(get_session), _=Depends(R)):
    start = startDate or dt.date.today() - dt.timedelta(days=365)
    end = endDate or dt.date.today()
    if start > end:
        raise invalid("InvalidArgument", reason="startDate after endDate")
    rows = s.execute(select(Observation).where(Observation.kind == "fx", Observation.entity == f"USD/{currency.upper()}",
                                               Observation.source_id.is_not(None), Observation.status.in_(["verified", "unverified"]),
                                               Observation.period_date >= start, Observation.period_date <= end)
                     .order_by(Observation.period_date)).scalars().all()
    return {"pair": f"USD/{currency.upper()}", "data": [{"date": jsonable(o.period_date), "rate": o.value, "status": o.status,
                                                         "flags": o.quality_flags, "observationRid": rid("observation", o.id)} for o in rows]}


@router.get("/market/priceSeries", summary="Get Price Series", openapi_extra=ext(G, "Prices", "api:market-read"),
            description="Dated price observations grouped into series (one per source × entity × metric × country), with the change "
                        "between first and last point. Filter by material RID, entity label, country or metric.")
def price_series(materialRid: str | None = Query(None, description="Material RID or id."),
                 entity: str | None = Query(None, description="Exact entity label, e.g. `Aluminum` (Pink Sheet)."),
                 countryIso3: str | None = Query(None), metric: str | None = Query(None),
                 verifiedOnly: bool = Query(False, description="Exclude unverified observations."),
                 s: Session = Depends(get_session), _=Depends(R)):
    mid = _mat(s, materialRid).id if materialRid else None
    out = market.series(s, kind="price", entity=entity, material_id=mid, country=countryIso3, metric=metric,
                        include_unverified=not verifiedOnly)
    return {"data": out["series"]}


@router.get("/market/benchmarks", summary="Get Regional Benchmarks", openapi_extra=ext(G, "Prices", "api:market-read"),
            description="Median normalized USD/kg by material family × country across verified and unverified observations, with sample counts.")
def benchmarks(s: Session = Depends(get_session), _=Depends(R)):
    return market.family_country_matrix(s)


@router.get("/market/laborRates", summary="Get Labor Rates", openapi_extra=ext(G, "Labor & Overhead", "api:market-read"),
            description="Average labour, direct and indirect overhead (USD/h) per country across imported machine / work-center records. "
                        "Zero or blank rates are excluded from averages.")
def labor(s: Session = Depends(get_session), _=Depends(R)):
    return {"data": market.labor_rates(s)}


@router.get("/market/indicators", summary="Get Indicators", openapi_extra=ext(G, "Indicators", "api:market-read"),
            description="Macro / industry indicator series (e.g. World Bank manufacturing value added, Eurostat industrial production).")
def indicators(s: Session = Depends(get_session), _=Depends(R)):
    return {"data": market.indicators(s)["series"]}


# ------------------------------------------------------------------ material intelligence
MG = "Material Intelligence"


class CompareBody(BaseModel):
    materialRids: list[str] = Field(..., min_length=2, max_length=6, description="2–6 material RIDs or ids.")


@router.post("/intelligence/materials/compare", summary="Compare Materials", openapi_extra=ext(MG, "Materials", "api:market-read"),
             description="Side-by-side designations, technical properties (as reported by source files) and latest USD/kg price per country.")
def compare(body: CompareBody = Body(..., openapi_examples={"x": {"summary": "Compare", "value": {"materialRids": ["ri.emithran.main.material.248", "ri.emithran.main.material.249"]}}}),
            s: Session = Depends(get_session), _=Depends(R)):
    ids = [_mat(s, r).id for r in body.materialRids]
    out = materials.compare(s, ids)
    for m in out["materials"]:
        m["rid"] = rid("material", m["id"])
    return out


class AltBody(BaseModel):
    category: str = Field(..., description="Category slug giving the process context, e.g. `die-casting`.")
    countryIso3: str = Field("USA", description="Country whose prices are used for the cost factor.")
    weights: dict[str, float] = Field(default_factory=lambda: {"cost": 0.4, "technical": 0.4, "availability": 0.1, "leadTime": 0.1},
                                      description="cost, technical, availability, leadTime. Factors without verified data are dropped and the rest re-normalized.")
    sameFamily: bool = Field(True, description="Restrict candidates to the reference material's family.")
    limit: int = Field(10, ge=1, le=50)


@router.post("/intelligence/materials/{materialRid}/alternatives", summary="Get Material Alternatives",
             openapi_extra=ext(MG, "Materials", "api:market-read", preview=True),
             description="**Preview** — call with `preview=true`. Ranks alternative materials for a process. Candidates must be listed "
                         "for the same category, share the material class (and by default the family) and carry the reference's process "
                         "capability flags (e.g. `Can Die Cast`). Nothing is estimated: factors without data are reported as null.")
def alternatives(materialRid: str = Path(..., examples=["ri.emithran.main.material.248"]),
                 preview: bool = Query(False, description="Must be true for preview endpoints."),
                 body: AltBody = Body(...), s: Session = Depends(get_session), _=Depends(R)):
    if not preview:
        raise invalid("ApiFeaturePreviewUsageOnly", endpoint="Get Material Alternatives")
    m = _mat(s, materialRid)
    if s.execute(select(Category.id).where(Category.slug == body.category)).first() is None:
        raise not_found("ObjectNotFound", objectType="ManufacturingCategory", primaryKey=body.category)
    w = {"cost": body.weights.get("cost", 0.4), "technical": body.weights.get("technical", 0.4),
         "availability": body.weights.get("availability", 0.1), "lead_time": body.weights.get("leadTime", 0.1)}
    out = materials.alternatives(s, m.id, body.category, w, country=body.countryIso3, limit=body.limit, same_family=body.sameFamily)
    for it in out["items"]:
        it["rid"] = rid("material", it["id"])
    out["reference"]["rid"] = rid("material", m.id)
    return out


@router.get("/intelligence/categories/{category}/materialFamilies", summary="Get Material Families By Process",
            openapi_extra=ext(MG, "Processes", "api:market-read"),
            description="Material families (with counts) that the taxonomy lists for a manufacturing category.")
def families(category: str = Path(..., examples=["die-casting"]), s: Session = Depends(get_session), _=Depends(R)):
    try:
        return materials.by_process(s, category)
    except ValueError:
        raise not_found("ObjectNotFound", objectType="ManufacturingCategory", primaryKey=category)
