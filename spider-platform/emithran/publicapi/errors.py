"""Foundry-style error envelope.

Every error is ``{"errorCode", "errorName", "errorInstanceId", "parameters"}``; ``errorInstanceId``
is also written to the request log so support can trace one failing call.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("emithran.publicapi")

STATUS = {"INVALID_ARGUMENT": 400, "UNAUTHORIZED": 401, "PERMISSION_DENIED": 403, "NOT_FOUND": 404,
          "CONFLICT": 409, "REQUEST_ENTITY_TOO_LARGE": 413, "TOO_MANY_REQUESTS": 429, "INTERNAL": 500}

# Catalogue used by the docs portal (Errors page) — keep in sync with raises below.
ERROR_CATALOG: list[dict[str, Any]] = [
    {"errorName": "MissingCredentials", "errorCode": "UNAUTHORIZED", "description": "No Authorization header was provided."},
    {"errorName": "InvalidToken", "errorCode": "UNAUTHORIZED", "description": "The bearer token is malformed, expired, revoked or has an invalid signature."},
    {"errorName": "InvalidClientCredentials", "errorCode": "UNAUTHORIZED", "description": "client_id / client_secret do not match an active client."},
    {"errorName": "MissingScope", "errorCode": "PERMISSION_DENIED", "description": "The token is valid but lacks a scope required by the endpoint."},
    {"errorName": "InvalidScope", "errorCode": "INVALID_ARGUMENT", "description": "A requested scope is not granted to the client."},
    {"errorName": "UnsupportedGrantType", "errorCode": "INVALID_ARGUMENT", "description": "Only grant_type=client_credentials is supported."},
    {"errorName": "InvalidPageSize", "errorCode": "INVALID_ARGUMENT", "description": "Page sizes must be between 1 and the endpoint maximum."},
    {"errorName": "InvalidPageToken", "errorCode": "INVALID_ARGUMENT", "description": "The page token could not be used to retrieve the next page."},
    {"errorName": "InvalidRid", "errorCode": "INVALID_ARGUMENT", "description": "The resource identifier is not a valid ri.emithran.* RID for this resource type."},
    {"errorName": "OntologyNotFound", "errorCode": "NOT_FOUND", "description": "The ontology API name or RID does not exist."},
    {"errorName": "ObjectTypeNotFound", "errorCode": "NOT_FOUND", "description": "The object type API name does not exist."},
    {"errorName": "ObjectNotFound", "errorCode": "NOT_FOUND", "description": "No object with the given primary key exists."},
    {"errorName": "LinkTypeNotFound", "errorCode": "NOT_FOUND", "description": "The object type has no link with this API name."},
    {"errorName": "PropertiesNotFound", "errorCode": "INVALID_ARGUMENT", "description": "One or more referenced properties do not exist on the object type."},
    {"errorName": "PropertyNotFilterable", "errorCode": "INVALID_ARGUMENT", "description": "Struct properties cannot be used in where, orderBy or groupBy."},
    {"errorName": "InvalidQuery", "errorCode": "INVALID_ARGUMENT", "description": "The search or aggregation body is not a valid query."},
    {"errorName": "DatasetNotFound", "errorCode": "NOT_FOUND", "description": "The dataset RID does not exist."},
    {"errorName": "ColumnsNotFound", "errorCode": "INVALID_ARGUMENT", "description": "Requested columns do not exist in the dataset."},
    {"errorName": "FileNotFound", "errorCode": "NOT_FOUND", "description": "The dataset's original file is not present on the server."},
    {"errorName": "MaterialNotFound", "errorCode": "NOT_FOUND", "description": "The material RID does not exist."},
    {"errorName": "SourceNotFound", "errorCode": "NOT_FOUND", "description": "The source does not exist."},
    {"errorName": "BuildNotFound", "errorCode": "NOT_FOUND", "description": "The build (crawl job) does not exist."},
    {"errorName": "BuildNotPermitted", "errorCode": "INVALID_ARGUMENT", "description": "The source cannot be crawled (planned, blocked by terms, disabled, already running)."},
    {"errorName": "ApiFeaturePreviewUsageOnly", "errorCode": "INVALID_ARGUMENT", "description": "This endpoint is in preview; call it with the query parameter preview=true."},
    {"errorName": "RateLimitExceeded", "errorCode": "TOO_MANY_REQUESTS", "description": "The per-minute request budget of the client or key is exhausted. Retry after X-RateLimit-Reset."},
    {"errorName": "ClientNotFound", "errorCode": "NOT_FOUND", "description": "The API client does not exist."},
    {"errorName": "ApiKeyNotFound", "errorCode": "NOT_FOUND", "description": "The API key does not exist."},
    {"errorName": "InvalidArgument", "errorCode": "INVALID_ARGUMENT", "description": "A request parameter failed validation; see parameters."},
    {"errorName": "Internal", "errorCode": "INTERNAL", "description": "Unexpected server error. Quote errorInstanceId to support."},
]


class ApiError(Exception):
    def __init__(self, error_code: str, error_name: str, parameters: dict[str, Any] | None = None,
                 headers: dict[str, str] | None = None):
        self.error_code = error_code
        self.error_name = error_name
        self.parameters = parameters or {}
        self.headers = headers or {}
        self.instance_id = str(uuid.uuid4())
        super().__init__(f"{error_code}:{error_name}")

    @property
    def status(self) -> int:
        return STATUS.get(self.error_code, 400)


def not_found(name: str, **params) -> ApiError:
    return ApiError("NOT_FOUND", name, params)


def invalid(name: str, **params) -> ApiError:
    return ApiError("INVALID_ARGUMENT", name, params)


def _body(code: str, name: str, iid: str, params: dict) -> dict:
    return {"errorCode": code, "errorName": name, "errorInstanceId": iid, "parameters": params}


def is_v2(request: Request) -> bool:
    return request.url.path.startswith("/api/v2")


async def api_error_handler(request: Request, exc: ApiError):
    request.state.error_name, request.state.error_instance_id = exc.error_name, exc.instance_id
    return JSONResponse(_body(exc.error_code, exc.error_name, exc.instance_id, exc.parameters), status_code=exc.status,
                        headers=exc.headers)


async def validation_handler(request: Request, exc: RequestValidationError):
    if not is_v2(request):
        from fastapi.exception_handlers import request_validation_exception_handler
        return await request_validation_exception_handler(request, exc)
    err = invalid("InvalidArgument", errors=[{"location": ".".join(str(x) for x in e["loc"]), "message": e["msg"]}
                                             for e in exc.errors()][:20])
    return await api_error_handler(request, err)


async def http_handler(request: Request, exc: StarletteHTTPException):
    if not is_v2(request):
        from fastapi.exception_handlers import http_exception_handler
        return await http_exception_handler(request, exc)
    code = {401: "UNAUTHORIZED", 403: "PERMISSION_DENIED", 404: "NOT_FOUND", 409: "CONFLICT", 405: "INVALID_ARGUMENT"}.get(exc.status_code, "INVALID_ARGUMENT")
    name = {404: "EndpointNotFound", 405: "MethodNotAllowed"}.get(exc.status_code, "InvalidArgument")
    e = ApiError(code, name, {"detail": exc.detail} if exc.detail else {})
    return JSONResponse(_body(code, name, e.instance_id, e.parameters), status_code=exc.status_code)


async def internal_handler(request: Request, exc: Exception):
    iid = str(uuid.uuid4())
    log.exception("unhandled error %s", iid)
    request.state.error_name, request.state.error_instance_id = "Internal", iid
    return JSONResponse(_body("INTERNAL", "Internal", iid, {}), status_code=500)


def install(app) -> None:
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_handler)
    app.add_exception_handler(StarletteHTTPException, http_handler)
    app.add_exception_handler(Exception, internal_handler)
