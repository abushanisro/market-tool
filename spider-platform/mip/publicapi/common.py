"""RIDs, paging and shared response helpers."""
from __future__ import annotations

import base64
import datetime as dt
import json
import re
from typing import Any

from .errors import invalid

RID_RE = re.compile(r"^ri\.emithran\.main\.([a-z\-]+)\.([A-Za-z0-9_\-.:]+)$")
DEFAULT_PAGE, MAX_PAGE = 100, 1000


def rid(kind: str, pk: Any) -> str:
    return f"ri.emithran.main.{kind}.{pk}"


def parse_rid(value: str, kind: str) -> str:
    """Accept a RID of the given kind (or a bare primary key) and return the key part."""
    m = RID_RE.match(value)
    if m:
        if m.group(1) != kind:
            raise invalid("InvalidRid", rid=value, expectedType=kind)
        return m.group(2)
    if value.startswith("ri."):
        raise invalid("InvalidRid", rid=value, expectedType=kind)
    return value


def page_params(page_size: int | None, page_token: str | None, maximum: int = MAX_PAGE) -> tuple[int, int]:
    size = DEFAULT_PAGE if page_size is None else page_size
    if size < 1 or size > maximum:
        raise invalid("InvalidPageSize", pageSize=page_size, maximum=maximum)
    offset = 0
    if page_token:
        try:
            data = json.loads(base64.urlsafe_b64decode(page_token + "=" * (-len(page_token) % 4)))
            offset = int(data["o"])
            if offset < 0:
                raise ValueError
        except Exception:
            raise invalid("InvalidPageToken", pageToken=page_token)
    return size, offset


def next_token(offset: int, size: int, total: int | None, got: int) -> str | None:
    nxt = offset + size
    if got < size or (total is not None and nxt >= total):
        return None
    return base64.urlsafe_b64encode(json.dumps({"o": nxt}).encode()).decode().rstrip("=")


def jsonable(v: Any) -> Any:
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    return v
