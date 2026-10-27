from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..config import get_settings
from ..db import init_db
from ..publicapi.app import install as install_public_api
from .routers import intel, ops

WEB = Path(__file__).resolve().parent.parent / "web"
log = logging.getLogger("mip.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    worker = None
    if get_settings().embedded_worker:
        from ..worker import Worker

        worker = Worker()
        worker.start()
        log.info("embedded worker started")
    yield
    if worker:
        worker.stop()


app = FastAPI(title="Emithran Manufacturing Intelligence — Spider Platform", version="1.0.0", lifespan=lifespan,
              docs_url="/api/docs", openapi_url="/api/openapi.json")
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origins, allow_methods=["*"], allow_headers=["*"])
install_public_api(app)
app.include_router(ops.router)
app.include_router(intel.router)
app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="assets")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(WEB / "index.html")
