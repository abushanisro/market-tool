"""Live change feed (server-sent events).

``GET /api/stream`` keeps one HTTP connection open and emits a ``change`` event whenever new observations or
documents arrive or crawl jobs start/finish, so open dashboards can redraw without polling every screen.
The feed only reports *that* something changed (plus cheap counters); screens re-query the normal API.
Without Redis this polls two indexed ``max(id)`` lookups every ``TICK`` seconds, which is negligible.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from ..db import session_scope
from ..models import AgentFinding, AgentRun, CrawlJob, Document, Observation

router = APIRouter(prefix="/api")
TICK = 2.0


def live_version() -> dict[str, Any]:
    """Cheap fingerprint of 'what is new'; two equal versions mean nothing a dashboard shows has changed."""
    with session_scope() as s:
        finished = s.execute(select(func.max(CrawlJob.finished_at))).scalar_one()
        return {
            "observations": s.execute(select(func.max(Observation.id))).scalar_one() or 0,
            "documents": s.execute(select(func.max(Document.id))).scalar_one() or 0,
            "jobs": s.execute(select(func.max(CrawlJob.id))).scalar_one() or 0,
            "active_jobs": s.execute(select(func.count()).select_from(CrawlJob)
                                     .where(CrawlJob.status.in_(["queued", "starting", "running"]))).scalar_one(),
            "last_finished": finished.isoformat() if finished else None,
            "agent_runs": s.execute(select(func.max(AgentRun.id))).scalar_one() or 0,
            "open_flags": s.execute(select(func.count()).select_from(AgentFinding).where(AgentFinding.status == "open")).scalar_one(),
        }


@router.get("/stream", include_in_schema=False)
async def stream(request: Request, once: bool = Query(False, description="send the first state and close (tests)")):
    async def events():
        yield "retry: 3000\n\n"
        last = None
        while not await request.is_disconnected():
            v = await run_in_threadpool(live_version)
            if v != last:
                last = v
                yield f"event: change\ndata: {json.dumps(v)}\n\n"
            else:
                yield ": keep-alive\n\n"
            if once:
                return
            await asyncio.sleep(TICK)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
