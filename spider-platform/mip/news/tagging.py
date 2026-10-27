"""Re-tag stored news after topics.yaml changes (or for headlines stored before tagging existed)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Document
from .classify import classify


def retag_all(s: Session) -> dict[str, int]:
    changed = relevant = total = 0
    for d in s.execute(select(Document).where(Document.kind == "news")).scalars():
        total += 1
        t = classify(d.title, d.summary, trusted=True, publisher_country=(d.raw or {}).get("publisher_country"))
        tags = sorted(set(t.tags) | {x for x in (d.categories or []) if x.startswith("pub:")})
        relevant += bool(t.categories)
        if tags != sorted(d.categories or []):
            d.categories, changed = tags, changed + 1
    return {"news": total, "changed": changed, "with_category": relevant}
