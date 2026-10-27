from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .. import auth
from ..config import get_settings
from ..db import init_db
from ..fetchapi.router import router as fetcher_router
from ..publicapi.app import install as install_public_api
from . import live
from .routers import agents as agents_router, feed as feed_router, intel, ops

WEB = Path(__file__).resolve().parent.parent / "web"
log = logging.getLogger("emithran.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    auth.check_production()
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


PROD = get_settings().env == "production"
app = FastAPI(title="Emithran Manufacturing Intelligence: Spider Platform", version="1.0.0", lifespan=lifespan,
              docs_url=None if PROD else "/api/docs", openapi_url=None if PROD else "/api/openapi.json")
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(CORSMiddleware, allow_origins=[] if PROD else get_settings().cors_origins,    # production: browsers only reach us through the Vercel proxy
                    allow_methods=["*"], allow_headers=["*"])
install_public_api(app)
app.include_router(ops.public)
app.include_router(ops.router)
app.include_router(intel.router)
app.include_router(fetcher_router)
app.include_router(live.router)
app.include_router(agents_router.router)
app.include_router(feed_router.router)
app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="assets")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(WEB / "index.html")
