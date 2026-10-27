"""Public API v2: auth, scopes, paging, search DSL, aggregation, datasets, errors."""
import pytest
from fastapi.testclient import TestClient

ADMIN = {"X-Admin-Token": "t0k"}


@pytest.fixture()
def api(seeded, monkeypatch):
    monkeypatch.setenv("EMITHRAN_ADMIN_TOKEN", "t0k")
    from emithran import config
    config.get_settings.cache_clear()
    import emithran.publicapi.auth as auth
    auth._secret_cache = None
    from emithran.api.main import app
    return TestClient(app, raise_server_exceptions=False)


def bearer(c, scopes):
    cl = c.post("/api/v2/admin/clients", headers=ADMIN, json={"name": "test client", "scopes": scopes}).json()
    r = c.post("/api/v2/oauth2/token", data={"grant_type": "client_credentials", "client_id": cl["clientId"],
                                             "client_secret": cl["clientSecret"]})
    assert r.status_code == 200 and r.json()["token_type"] == "bearer"
    return {"Authorization": "Bearer " + r.json()["access_token"]}, cl


def test_auth_and_error_envelope(api):
    r = api.get("/api/v2/ontologies")
    assert r.status_code == 401 and set(r.json()) == {"errorCode", "errorName", "errorInstanceId", "parameters"}
    assert r.json()["errorName"] == "MissingCredentials"
    assert api.get("/api/v2/ontologies", headers={"Authorization": "Bearer emt_bad.sig"}).json()["errorName"] == "InvalidToken"
    bad = api.post("/api/v2/oauth2/token", data={"grant_type": "client_credentials", "client_id": "x", "client_secret": "y"})
    assert bad.status_code == 401 and bad.json()["errorName"] == "InvalidClientCredentials"
    h, cl = bearer(api, ["api:ontologies-read"])
    assert api.get("/api/v2/ontologies", headers=h).status_code == 200
    r = api.get("/api/v2/datasets", headers=h)
    assert r.status_code == 403 and r.json()["parameters"]["missingScopes"] == ["api:datasets-read"]
    # revocation invalidates tokens immediately
    api.post(f"/api/v2/admin/clients/{cl['clientId']}/revoke", headers=ADMIN)
    assert api.get("/api/v2/ontologies", headers=h).json()["errorName"] == "InvalidToken"


def test_api_key_rotation_and_rate_limit(api):
    k = api.post("/api/v2/admin/apiKeys", headers=ADMIN, json={"name": "test key", "scopes": ["api:ontologies-read"], "rateLimitPerMinute": 3}).json()
    h = {"Authorization": "Bearer " + k["apiKey"]}
    codes = [api.get("/api/v2/ontologies", headers=h).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    r = api.get("/api/v2/ontologies", headers=h)
    assert r.json()["errorName"] == "RateLimitExceeded" and "Retry-After" in r.headers
    listing = api.get("/api/v2/admin/apiKeys", headers=ADMIN).json()["data"]
    assert "apiKey" not in listing[0] and listing[0]["prefix"].startswith("emk_")


def test_ontology_objects_search_aggregate_links(api):
    h, _ = bearer(api, ["api:ontologies-read"])
    base = "/api/v2/ontologies/manufacturing"
    types = api.get(f"{base}/objectTypes?pageSize=100", headers=h).json()["data"]
    assert {"Material", "Machine", "Process", "Dataset", "Observation", "ManufacturingCategory", "UnresolvedIdentifier"} <= {t["apiName"] for t in types}
    p1 = api.get(f"{base}/objects/Material?pageSize=2", headers=h).json()
    assert p1["totalCount"] == 3 and p1["nextPageToken"]
    p2 = api.get(f"{base}/objects/Material?pageSize=2&pageToken={p1['nextPageToken']}", headers=h).json()
    assert len(p2["data"]) == 1 and p2["nextPageToken"] is None
    assert api.get(f"{base}/objects/Material?pageToken=@@", headers=h).json()["errorName"] == "InvalidPageToken"
    s = api.post(f"{base}/objects/Material/search", headers=h, json={
        "where": {"type": "or", "value": [{"type": "eq", "field": "family", "value": "zinc"}, {"type": "gt", "field": "densityKgM3", "value": 6000}]},
        "select": ["name"]}).json()
    assert [o["name"] for o in s["data"]] == ["Zinc Zamak 3"] and s["data"][0]["__rid"].startswith("ri.emithran.main.material.")
    a = api.post(f"{base}/objects/Observation/aggregate", headers=h, json={
        "aggregation": [{"type": "count", "name": "n"}], "groupBy": [{"type": "exact", "field": "status"}]}).json()
    assert {d["group"]["status"]: d["metrics"][0]["value"] for d in a["data"]} == {"verified": 4.0, "quarantined": 1.0}
    links = api.get(f"{base}/objects/ManufacturingCategory/die-casting/links/materials", headers=h).json()
    assert links["totalCount"] == 3
    assert api.get(f"{base}/objects/UnresolvedIdentifier/MHR", headers=h).json()["status"] == "unresolved"
    bad = api.post(f"{base}/objects/Material/search", headers=h, json={"where": {"type": "regex", "field": "name", "value": "x"}})
    assert bad.json()["errorName"] == "InvalidQuery"


def test_datasets_and_files(api):
    h, _ = bearer(api, ["api:datasets-read", "api:market-read"])
    ds = api.get("/api/v2/datasets?role=materials", headers=h).json()["data"][0]
    assert ds["path"] == "Die Casting/Raw Material/materials_master.csv"
    rows = api.get(f"/api/v2/datasets/{ds['rid']}/readTable?columns=Name&pageSize=10", headers=h).json()
    assert [r["values"]["Name"] for r in rows["data"]][:2] == ["Aluminum A380", "Zinc Zamak 3"]
    csv = api.get(f"/api/v2/datasets/{ds['rid']}/readTable?format=CSV&columns=Name&columns=Cut Code", headers=h)
    assert csv.text.splitlines()[0] == "Name,Cut Code"
    f = api.get(f"/api/v2/datasets/{ds['rid']}/files/content", headers=h)
    import hashlib
    assert hashlib.sha256(f.content).hexdigest() == ds["checksum"]["value"]
    sch = api.get(f"/api/v2/datasets/{ds['rid']}/schema", headers=h).json()["fieldSchemaList"]
    assert {x["name"]: x["type"] for x in sch}["Density (kg/m^3)"] == "LONG"
    assert api.post("/api/v2/intelligence/materials/1/alternatives", headers=h, json={"category": "die-casting"}).json()["errorName"] == "ApiFeaturePreviewUsageOnly"
    assert api.get("/api/v2/openapi.json").status_code == 200
