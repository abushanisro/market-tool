"""Export the API v2 spec, object-type catalogue and REAL example responses for the developer portal.

Usage (against a database that has the memory/ import):
    EMITHRAN_ADMIN_TOKEN=... python scripts/export_api_docs.py ../developer-portal/content

Examples are produced by calling the API in-process with a temporary key that is revoked afterwards;
long arrays are trimmed to 3 items so pages stay readable. Nothing is invented.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from emithran.api.main import app  # noqa: E402
from emithran.config import get_settings  # noqa: E402
from emithran.db import session_scope  # noqa: E402
from emithran.models import ApiClient, ApiKey, Dataset, Material  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "content")


def trim(v, depth=0):
    if isinstance(v, list):
        return [trim(x, depth + 1) for x in v[:3]]
    if isinstance(v, dict):
        return {k: trim(x, depth + 1) for k, x in list(v.items())[:40]}
    if isinstance(v, str) and len(v) > 300:
        return v[:300] + "…"
    return v


def main():
    admin = get_settings().admin_token
    if not admin:
        sys.exit("set EMITHRAN_ADMIN_TOKEN")
    c = TestClient(app, raise_server_exceptions=False)
    A = {"X-Admin-Token": admin}
    key = c.post("/api/v2/admin/apiKeys", headers=A, json={"name": "docs-example-export", "expiresInDays": 1, "scopes": [
        "api:ontologies-read", "api:datasets-read", "api:market-read", "api:orchestration-read"]}).json()
    H = {"Authorization": f"Bearer {key['apiKey']}"}
    with session_scope() as s:
        mat = s.execute(select(Material).where(Material.name == "Stainless Steel, AISI 304")).scalar_one_or_none() \
            or s.execute(select(Material)).scalars().first()
        mat2 = s.execute(select(Material).where(Material.family == mat.family, Material.id != mat.id)).scalars().first()
        ds = s.execute(select(Dataset).where(Dataset.role == "materials")).scalars().first()
        mid, mid2, dsid = mat.id, mat2.id, ds.id
    O = "/api/v2/ontologies/manufacturing"
    calls = [
        ("GET /api/v2/ontologies", "GET", "/api/v2/ontologies", None, None),
        ("GET /api/v2/ontologies/{ontology}", "GET", f"{O}", None, None),
        ("GET /api/v2/ontologies/{ontology}/objectTypes", "GET", f"{O}/objectTypes", {"pageSize": 2}, None),
        ("GET /api/v2/ontologies/{ontology}/objectTypes/{objectType}", "GET", f"{O}/objectTypes/Material", None, None),
        ("GET /api/v2/ontologies/{ontology}/objectTypes/{objectType}/outgoingLinkTypes", "GET", f"{O}/objectTypes/Material/outgoingLinkTypes", None, None),
        ("GET /api/v2/ontologies/{ontology}/objects/{objectType}", "GET", f"{O}/objects/Material", {"pageSize": 2, "select": ["name", "family", "grade"]}, None),
        ("GET /api/v2/ontologies/{ontology}/objects/{objectType}/{primaryKey}", "GET", f"{O}/objects/Material/{mid}", None, None),
        ("POST /api/v2/ontologies/{ontology}/objects/{objectType}/search", "POST", f"{O}/objects/Material/search", None, {
            "where": {"type": "and", "value": [{"type": "eq", "field": "family", "value": "aluminium"},
                                               {"type": "isNull", "field": "densityKgM3", "value": False}]},
            "orderBy": {"fields": [{"field": "name", "direction": "asc"}]}, "pageSize": 3, "select": ["name", "grade", "densityKgM3"]}),
        ("POST /api/v2/ontologies/{ontology}/objects/{objectType}/aggregate", "POST", f"{O}/objects/Observation/aggregate", None, {
            "aggregation": [{"type": "count", "name": "n"}, {"type": "avg", "field": "valueUsd", "name": "avgUsdPerKg"}],
            "where": {"type": "and", "value": [{"type": "eq", "field": "kind", "value": "price"},
                                               {"type": "eq", "field": "unitNormalized", "value": "USD/kg"},
                                               {"type": "eq", "field": "status", "value": "verified"}]},
            "groupBy": [{"type": "exact", "field": "countryIso3", "maxGroupCount": 10}]}),
        ("GET /api/v2/ontologies/{ontology}/objects/{objectType}/{primaryKey}/links/{linkType}", "GET",
         f"{O}/objects/ManufacturingCategory/die-casting/links/materials", {"pageSize": 2}, None),
        ("GET /api/v2/datasets", "GET", "/api/v2/datasets", {"category": "die-casting", "role": "materials"}, None),
        ("GET /api/v2/datasets/{datasetRid}", "GET", f"/api/v2/datasets/ri.emithran.main.dataset.{dsid}", None, None),
        ("GET /api/v2/datasets/{datasetRid}/schema", "GET", f"/api/v2/datasets/ri.emithran.main.dataset.{dsid}/schema", None, None),
        ("GET /api/v2/datasets/{datasetRid}/readTable", "GET", f"/api/v2/datasets/ri.emithran.main.dataset.{dsid}/readTable", {"pageSize": 2}, None),
        ("GET /api/v2/market/fxRates", "GET", "/api/v2/market/fxRates", {"currencies": ["INR", "EUR"]}, None),
        ("GET /api/v2/market/fxRates/{currency}/series", "GET", "/api/v2/market/fxRates/INR/series", {"startDate": "2026-10-21"}, None),
        ("GET /api/v2/market/priceSeries", "GET", "/api/v2/market/priceSeries", {"materialRid": f"ri.emithran.main.material.{mid}"}, None),
        ("GET /api/v2/market/benchmarks", "GET", "/api/v2/market/benchmarks", None, None),
        ("GET /api/v2/market/laborRates", "GET", "/api/v2/market/laborRates", None, None),
        ("GET /api/v2/market/indicators", "GET", "/api/v2/market/indicators", None, None),
        ("POST /api/v2/intelligence/materials/compare", "POST", "/api/v2/intelligence/materials/compare", None,
         {"materialRids": [f"ri.emithran.main.material.{mid}", f"ri.emithran.main.material.{mid2}"]}),
        ("POST /api/v2/intelligence/materials/{materialRid}/alternatives", "POST",
         f"/api/v2/intelligence/materials/ri.emithran.main.material.{mid}/alternatives", {"preview": "true"},
         {"category": "sheet-metal-stretch-forming", "countryIso3": "USA", "limit": 3}),
        ("GET /api/v2/intelligence/categories/{category}/materialFamilies", "GET", "/api/v2/intelligence/categories/die-casting/materialFamilies", None, None),
        ("GET /api/v2/orchestration/sources", "GET", "/api/v2/orchestration/sources", {"pageSize": 2}, None),
        ("GET /api/v2/orchestration/sources/{source}", "GET", "/api/v2/orchestration/sources/frankfurter-fx", None, None),
        ("GET /api/v2/orchestration/builds", "GET", "/api/v2/orchestration/builds", {"pageSize": 1}, None),
        ("GET /api/v2/meta/scopes", "GET", "/api/v2/meta/scopes", None, None),
    ]
    out = {"generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(), "examples": {}}
    for k, method, path, query, body in calls:
        r = c.request(method, path, params=query, json=body, headers=H)
        out["examples"][k] = {"request": {"method": method, "path": path, "query": query, "body": body},
                              "status": r.status_code, "response": trim(r.json())}
        print(r.status_code, k)
    # error example + token example (shape only; token value redacted)
    r = c.get(f"{O}/objects/Material/999999", headers=H)
    out["examples"]["error"] = {"status": r.status_code, "response": r.json()}
    out["examples"]["POST /api/v2/oauth2/token"] = {
        "request": {"method": "POST", "path": "/api/v2/oauth2/token", "form": {"grant_type": "client_credentials",
                    "client_id": "emc_…", "client_secret": "ems_…", "scope": "api:ontologies-read api:market-read"}},
        "status": 200, "response": {"access_token": "emt_eyJjaWQiOi…<redacted>", "token_type": "bearer", "expires_in": 3600,
                                    "scope": "api:ontologies-read api:market-read"}}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "examples.json").write_text(json.dumps(out, indent=1, default=str))
    (OUT / "openapi.json").write_text(json.dumps(c.get("/api/v2/openapi.json").json(), indent=1))
    types = c.get(f"{O}/objectTypes", params={"pageSize": 100}, headers=H).json()["data"]
    counts = {t["apiName"]: c.get(f"{O}/objects/{t['apiName']}", params={"pageSize": 1}, headers=H).json()["totalCount"] for t in types}
    samples = {t["apiName"]: trim(c.get(f"{O}/objects/{t['apiName']}", params={"pageSize": 1}, headers=H).json()["data"]) for t in types}
    (OUT / "object-types.json").write_text(json.dumps({"objectTypes": types, "counts": counts, "samples": samples}, indent=1, default=str))
    (OUT / "errors.json").write_text(json.dumps(c.get("/api/v2/meta/errors").json(), indent=1))
    c.post(f"/api/v2/admin/apiKeys/{key['prefix']}/revoke", headers=A)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
