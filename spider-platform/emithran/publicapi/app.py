"""Mount the public API v2 onto the FastAPI app and expose its own OpenAPI document."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from . import errors
from .auth import SCOPES, usage_middleware
from .routers import datasets, market, ontologies, platform

GROUP_ORDER = ["General", "Ontologies", "Datasets", "Market Intelligence", "Material Intelligence", "Orchestration", "Admin"]


def install(app: FastAPI) -> None:
    errors.install(app)
    app.middleware("http")(usage_middleware)
    for r in (platform.router, ontologies.router, datasets.router, market.router):
        app.include_router(r)
    _cache: dict = {}

    @app.get("/api/v2/openapi.json", include_in_schema=False)
    def v2_openapi():
        if "doc" not in _cache:
            doc = get_openapi(title="Emithran Manufacturing Intelligence API", version="2.0.0", routes=app.routes,
                              description="REST API over the Emithran manufacturing taxonomy, materials, machines, processes, "
                                          "prices and market data.",
                              servers=[{"url": "/", "description": "This deployment"}])
            doc["paths"] = {k: v for k, v in doc["paths"].items() if k.startswith("/api/v2")}
            doc.setdefault("components", {})["securitySchemes"] = {
                "oauth2": {"type": "oauth2", "flows": {"clientCredentials": {"tokenUrl": "/api/v2/oauth2/token", "scopes": SCOPES}}},
                "apiKey": {"type": "http", "scheme": "bearer", "bearerFormat": "emk_<prefix>_<secret>"},
            }
            for path, ops in doc["paths"].items():
                for op in ops.values():
                    sc = op.get("x-scopes") or []
                    if sc:
                        op["security"] = [{"oauth2": sc}, {"apiKey": []}]
            doc["x-group-order"] = GROUP_ORDER
            _cache["doc"] = doc
        return _cache["doc"]
