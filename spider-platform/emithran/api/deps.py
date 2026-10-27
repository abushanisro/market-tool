from __future__ import annotations

import datetime as dt

from fastapi import Header, HTTPException

from ..config import get_settings
from ..db import get_session  # noqa: F401  (re-export)


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    token = get_settings().admin_token
    if token and x_admin_token != token:
        raise HTTPException(status_code=401, detail="X-Admin-Token required")


def iso(d):
    """ISO string; naive datetimes are UTC (SQLite drops the zone), so mark them as such or browsers read them as local time."""
    if d is None:
        return None
    if isinstance(d, dt.datetime) and d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.isoformat()
