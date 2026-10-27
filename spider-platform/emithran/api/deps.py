from __future__ import annotations

import datetime as dt

from ..auth import admin
from ..db import get_session  # noqa: F401  (re-export)


require_admin = admin  # write access: Auth0 role `admin` (development: optional shared secret); see emithran/auth.py


def iso(d):
    """ISO string; naive datetimes are UTC (SQLite drops the zone), so mark them as such or browsers read them as local time."""
    if d is None:
        return None
    if isinstance(d, dt.datetime) and d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.isoformat()
