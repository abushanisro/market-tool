"""Ontologies · Object Types · Ontology Objects · Linked Objects."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...db import get_session
from ..auth import scoped
from ..common import MAX_PAGE, next_token, page_params
from ..errors import not_found
from ..ontology import (
    ONTOLOGY, OBJECT_TYPES, aggregate, apply_order, check_ontology, compile_where, get_object, get_type, serialize,
)

R = scoped("api:ontologies-read")
router = APIRouter(prefix="/api/v2/ontologies")

ONT = Path(..., description="The API name (`manufacturing`) or RID of the ontology.", examples=["manufacturing"])
OTP = Path(..., description="The API name of the object type, e.g. `Material`. Use List Object Types to discover them.",
           examples=["Material"])
PAGE_SIZE = Query(None, alias="pageSize", description=f"The page size to use for the endpoint (1–{MAX_PAGE}, default 100).")
PAGE_TOKEN = Query(None, alias="pageToken", description="Omit on the first request; then pass `nextPageToken` from the previous response.")


def ext(group: str, resource: str, scope: str, preview: bool = False) -> dict:
    return {"x-group": group, "x-resource": resource, "x-scopes": [scope], "x-preview": preview}


class ObjectList(BaseModel):
    nextPageToken: str | None = Field(None, description="Token for the next page; absent on the last page.")
    totalCount: int = Field(..., description="Total number of objects matching the request.")
    data: list[dict[str, Any]] = Field(..., description="Objects. Each has `__apiName`, `__primaryKey`, `__rid`, `__title` plus properties.")


class SearchBody(BaseModel):
    where: dict[str, Any] | None = Field(None, description="Filter clause. Leaf types: eq, gt, gte, lt, lte, isNull, in, contains, startsWith, "
                                                          "containsAnyTerm, containsAllTerms (each with `field` and `value`); combinators: and, or (value: list), not (value: clause).")
    orderBy: dict[str, Any] | None = Field(None, description='`{"fields": [{"field": "name", "direction": "asc|desc"}]}`')
    pageSize: int | None = Field(None, description="Page size (1–1000, default 100).")
    pageToken: str | None = Field(None, description="Token from the previous response.")
    select: list[str] | None = Field(None, description="Property API names to include. Omit for all properties.")


class AggregateBody(BaseModel):
    aggregation: list[dict[str, Any]] = Field(..., description="Metrics: `{type: count|min|max|avg|sum|approximateDistinct, field?, name?}` (1–10).")
    where: dict[str, Any] | None = Field(None, description="Optional filter, same syntax as Search Objects.")
    groupBy: list[dict[str, Any]] | None = Field(None, description="`{type: exact, field, maxGroupCount?}` or `{type: fixedWidth, field, fixedWidth}`.")


@router.get("", summary="List Ontologies", openapi_extra=ext("Ontologies", "Ontologies", "api:ontologies-read"),
            description="Lists the ontologies visible to the caller. The platform exposes a single ontology, `manufacturing`, "
                        "that models every dataset imported from the memory/ folder plus crawled data.")
def list_ontologies(_=Depends(R)):
    return {"data": [ONTOLOGY]}


@router.get("/{ontology}", summary="Get Ontology", openapi_extra=ext("Ontologies", "Ontologies", "api:ontologies-read"),
            description="Gets a specific ontology with the given API name or RID.")
def get_ontology(ontology: str = ONT, _=Depends(R)):
    check_ontology(ontology)
    return ONTOLOGY


@router.get("/{ontology}/objectTypes", summary="List Object Types", openapi_extra=ext("Ontologies", "Object Types", "api:ontologies-read"),
            description="Lists the object types in the ontology with their properties, data types, primary key and links.")
def list_object_types(ontology: str = ONT, pageSize: int | None = PAGE_SIZE, pageToken: str | None = PAGE_TOKEN, _=Depends(R)):
    check_ontology(ontology)
    size, off = page_params(pageSize, pageToken)
    items = [o.metadata() for o in OBJECT_TYPES.values()]
    page = items[off:off + size]
    return {"nextPageToken": next_token(off, size, len(items), len(page)), "data": page}


@router.get("/{ontology}/objectTypes/{objectType}", summary="Get Object Type",
            openapi_extra=ext("Ontologies", "Object Types", "api:ontologies-read"),
            description="Gets one object type: display names, primary key, title property, each property's data type and "
                        "whether it can be filtered, and outgoing link types.")
def get_object_type(ontology: str = ONT, objectType: str = OTP, _=Depends(R)):
    check_ontology(ontology)
    return get_type(objectType).metadata()


@router.get("/{ontology}/objectTypes/{objectType}/outgoingLinkTypes", summary="List Outgoing Link Types",
            openapi_extra=ext("Ontologies", "Object Types", "api:ontologies-read"),
            description="Lists the link types that start at the object type, e.g. Material → categories, observations.")
def link_types(ontology: str = ONT, objectType: str = OTP, _=Depends(R)):
    check_ontology(ontology)
    return {"data": get_type(objectType).metadata()["links"]}


def _count(s: Session, stmt) -> int:
    return s.execute(select(func.count()).select_from(stmt.order_by(None).subquery())).scalar_one()


@router.get("/{ontology}/objects/{objectType}", summary="List Objects", response_model=ObjectList,
            openapi_extra=ext("Ontologies", "Ontology Objects", "api:ontologies-read"),
            description="Lists the objects of the given type, ordered by primary key. Use Search Objects to filter and sort.")
def list_objects(ontology: str = ONT, objectType: str = OTP, pageSize: int | None = PAGE_SIZE, pageToken: str | None = PAGE_TOKEN,
                 select_: list[str] | None = Query(None, alias="select", description="Repeatable. Property API names to return."),
                 s: Session = Depends(get_session), _=Depends(R)):
    check_ontology(ontology)
    ot = get_type(objectType)
    size, off = page_params(pageSize, pageToken)
    stmt = apply_order(ot, select(ot.model), None)
    total = _count(s, stmt)
    rows = s.execute(stmt.limit(size).offset(off)).scalars().all()
    return {"nextPageToken": next_token(off, size, total, len(rows)), "totalCount": total,
            "data": [serialize(ot, r, select_) for r in rows]}


@router.get("/{ontology}/objects/{objectType}/{primaryKey}", summary="Get Object",
            openapi_extra=ext("Ontologies", "Ontology Objects", "api:ontologies-read"),
            description="Gets a specific object by primary key (e.g. a Material id, a ManufacturingCategory slug, a Country ISO3).")
def get_obj(ontology: str = ONT, objectType: str = OTP,
            primaryKey: str = Path(..., description="Primary key value of the object.", examples=["248"]),
            select_: list[str] | None = Query(None, alias="select", description="Repeatable. Property API names to return."),
            s: Session = Depends(get_session), _=Depends(R)):
    check_ontology(ontology)
    ot = get_type(objectType)
    return serialize(ot, get_object(s, ot, primaryKey), select_)


@router.post("/{ontology}/objects/{objectType}/search", summary="Search Objects", response_model=ObjectList,
             openapi_extra=ext("Ontologies", "Ontology Objects", "api:ontologies-read"),
             description="Searches objects of the given type with a filter tree, ordering, property selection and paging.\n\n"
                         "| Query type | Description |\n|---|---|\n"
                         "| `eq`, `gt`, `gte`, `lt`, `lte` | Compare a scalar property with `value`. |\n"
                         "| `isNull` | `value: true` matches nulls, `false` non-nulls. |\n"
                         "| `in` | Property equals one of the listed values. |\n"
                         "| `contains` | Array property contains `value` (e.g. `qualityFlags`). |\n"
                         "| `startsWith` | Case-insensitive string prefix. |\n"
                         "| `containsAnyTerm` / `containsAllTerms` | Case-insensitive term match on a string. |\n"
                         "| `and`, `or` | `value` is a list of clauses. |\n| `not` | `value` is one clause. |")
def search_objects(ontology: str = ONT, objectType: str = OTP,
                   body: SearchBody = Body(..., openapi_examples={"aluminium": {"summary": "Aluminium grades with density", "value": {
                       "where": {"type": "and", "value": [{"type": "eq", "field": "family", "value": "aluminium"},
                                                          {"type": "isNull", "field": "densityKgM3", "value": False}]},
                       "orderBy": {"fields": [{"field": "name", "direction": "asc"}]}, "pageSize": 5,
                       "select": ["name", "grade", "densityKgM3"]}}}),
                   s: Session = Depends(get_session), _=Depends(R)):
    check_ontology(ontology)
    ot = get_type(objectType)
    size, off = page_params(body.pageSize, body.pageToken)
    stmt = select(ot.model)
    w = compile_where(ot, body.where)
    if w is not None:
        stmt = stmt.where(w)
    stmt = apply_order(ot, stmt, body.orderBy)
    total = _count(s, stmt)
    rows = s.execute(stmt.limit(size).offset(off)).scalars().all()
    return {"nextPageToken": next_token(off, size, total, len(rows)), "totalCount": total,
            "data": [serialize(ot, r, body.select) for r in rows]}


@router.post("/{ontology}/objects/{objectType}/aggregate", summary="Aggregate Objects",
             openapi_extra=ext("Ontologies", "Ontology Objects", "api:ontologies-read"),
             description="Computes count, min, max, avg, sum or approximateDistinct over objects, optionally filtered and grouped "
                         "(`exact` or numeric `fixedWidth` buckets). `accuracy` is `APPROXIMATE` when groups were truncated by `maxGroupCount`.")
def aggregate_objects(ontology: str = ONT, objectType: str = OTP,
                      body: AggregateBody = Body(..., openapi_examples={"byFamily": {"summary": "Median-free price stats by family", "value": {
                          "aggregation": [{"type": "count", "name": "n"}, {"type": "avg", "field": "valueUsd", "name": "avgUsdPerKg"}],
                          "where": {"type": "and", "value": [{"type": "eq", "field": "kind", "value": "price"},
                                                             {"type": "eq", "field": "unitNormalized", "value": "USD/kg"},
                                                             {"type": "eq", "field": "status", "value": "verified"}]},
                          "groupBy": [{"type": "exact", "field": "countryIso3", "maxGroupCount": 20}]}}}),
                      s: Session = Depends(get_session), _=Depends(R)):
    check_ontology(ontology)
    return aggregate(s, get_type(objectType), body.model_dump())


@router.get("/{ontology}/objects/{objectType}/{primaryKey}/links/{linkType}", summary="List Linked Objects", response_model=ObjectList,
            openapi_extra=ext("Ontologies", "Linked Objects", "api:ontologies-read"),
            description="Lists objects linked to the given object through the link type, e.g. "
                        "`ManufacturingCategory/die-casting/links/materials` or `Material/248/links/observations`.")
def linked_objects(ontology: str = ONT, objectType: str = OTP,
                   primaryKey: str = Path(..., description="Primary key of the source object.", examples=["die-casting"]),
                   linkType: str = Path(..., description="Link API name from List Outgoing Link Types.", examples=["materials"]),
                   pageSize: int | None = PAGE_SIZE, pageToken: str | None = PAGE_TOKEN,
                   s: Session = Depends(get_session), _=Depends(R)):
    check_ontology(ontology)
    ot = get_type(objectType)
    link = next((l for l in ot.links if l.api == linkType), None)
    if link is None:
        raise not_found("LinkTypeNotFound", objectType=ot.api, linkType=linkType)
    row = get_object(s, ot, primaryKey)
    target = get_type(link.target)
    size, off = page_params(pageSize, pageToken)
    stmt = link.query(row)
    total = _count(s, stmt)
    rows = s.execute(stmt.order_by(target.prop(target.pk).col).limit(size).offset(off)).scalars().all()
    return {"nextPageToken": next_token(off, size, total, len(rows)), "totalCount": total, "data": [serialize(target, r) for r in rows]}
