from __future__ import annotations

from fastapi import Header, HTTPException

from ..config import get_settings
from ..db import get_session  # noqa: F401  (re-export)


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    token = get_settings().admin_token
    if token and x_admin_token != token:
        raise HTTPException(status_code=401, detail="X-Admin-Token required")


def iso(d):
    return d.isoformat() if d is not None else None
