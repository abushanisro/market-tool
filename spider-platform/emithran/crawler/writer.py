"""Item persistence shared by every crawl engine (Scrapy pipeline, Go fetcher ingest).

``persist_items`` applies source validation rules and writes observations / documents / standards.
Counters are reported through ``inc(name)`` using the short names from ``monitor.STAT_KEYS``
(``items_inserted``, ``items_duplicate`` …) so each engine maps them to its own stats store.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Callable, Iterable

from dateutil import parser as dateparser
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CrawlUrl, Document, QualityIssue, Standard
from ..normalize.units import to_float
from .middlewares import url_hash
from ..store import ObsIn, ObservationStore

log = logging.getLogger(__name__)


def validate(item: dict, rules: dict) -> str | None:
    for f in rules.get("required", []):
        if item.get(f) in (None, ""):
            return f"missing required field {f!r}"
    if item.get("item_type") == "observation":
        v = to_float(item.get("value"))
        if v is not None:
            if "min" in rules and v < rules["min"]:
                return f"value {v} < min {rules['min']}"
            if "max" in rules and v > rules["max"]:
                return f"value {v} > max {rules['max']}"
        allowed = rules.get("allowed_units")
        if allowed and item.get("unit") not in allowed:
            return f"unit {item.get('unit')!r} not in {allowed}"
    return None


def persist_items(s: Session, items: Iterable[dict], *, source_id: int, job_id: int,
                  inc: Callable[[str], None]) -> None:
    store = ObservationStore(s)
    for it in items:
        try:
            if it.get("_invalid"):
                s.add(QualityIssue(severity="error", code="validation_failed", message=it["_invalid"],
                                   source_id=source_id, job_id=job_id,
                                   context={k: str(v)[:300] for k, v in it.items() if k != "original"}))
                continue
            t = it["item_type"]
            flags = list(it.get("flags", [])) + (["replayed"] if it.get("_replayed") else [])
            if t == "observation":
                r = store.add(ObsIn(
                    kind=it["kind"], entity=str(it["entity"]), metric=str(it["metric"]), value=it.get("value"),
                    unit=it.get("unit"), currency=it.get("currency"), period_date=it.get("period_date"),
                    country=it.get("country"), material_name=it.get("material_name"),
                    category_slug=it.get("category_slug"), source_id=source_id, source_url=it.get("_url"),
                    original=it.get("original", {}), flags=flags, job_id=job_id))
                inc(f"items_{r.outcome}")
            elif t == "document":
                _document(s, it, source_id, job_id, flags, inc)
            elif t == "standard":
                _standard(s, it, source_id, inc)
        except Exception as exc:  # one bad record never aborts the batch
            log.exception("store failed")
            inc("items_error")
            s.add(QualityIssue(severity="error", code="store_failed", message=str(exc)[:1000],
                               source_id=source_id, job_id=job_id, context={"item": str(it)[:1000]}))


def _document(s, it, source_id, job_id, flags, inc) -> None:
    categories = it.get("categories") or []
    if it.get("kind") == "news":
        # one place for tagging + the manufacturing-relevance gate, so every news source is treated identically
        from ..news.classify import classify

        hint = it.get("hint") or {}
        tagged = classify(it.get("title") or "", it.get("summary"), trusted=bool(it.get("trusted")) or not it.get("gate"),
                          publisher_country=it.get("publisher_country"))
        tags = set(tagged.tags)
        if hint.get("category") and (tagged.relevant or it.get("trusted")):
            tags.add(f"cat:{hint['category']}")                  # the query that returned it was about this category
        if it.get("gate") and not tagged.relevant and not (it.get("trusted") and hint.get("category")):
            inc("items_filtered")                                 # not manufacturing news: never stored
            return
        categories = sorted(tags)
    existing = s.execute(select(Document).where(Document.url == it["url"][:1000])).scalar_one_or_none()
    pub = None
    if it.get("published_at"):
        try:
            pub = dateparser.parse(str(it["published_at"]))
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=dt.timezone.utc)
        except (ValueError, OverflowError):
            pub = None
    fields = dict(kind=it.get("kind", "publication"), title=(it.get("title") or "")[:2000] or it["url"],
                  published_at=pub, authors=it.get("authors") or [], summary=it.get("summary"),
                  text_excerpt=it.get("text_excerpt"), doi=it.get("doi"), license=it.get("license"), categories=categories,
                  raw={**(it.get("raw") or {}), "flags": flags}, source_id=source_id, job_id=job_id)
    if existing is None:
        s.add(Document(url=it["url"][:1000], **fields))
        inc("items_inserted")
    elif existing.title != fields["title"] or existing.published_at != pub:
        for k, v in fields.items():
            setattr(existing, k, v)
        inc("items_revised")
    else:
        existing.raw, existing.categories = fields["raw"], categories     # e.g. Hacker News points moved; retagged after a topics.yaml edit
        inc("items_duplicate")
    s.flush()


def _standard(s, it, source_id, inc) -> None:
    st = s.execute(select(Standard).where(Standard.code == it["code"], Standard.body == it.get("body"))).scalar_one_or_none()
    if st is None:
        s.add(Standard(code=it["code"], body=it.get("body"), title=it.get("title"), status=it.get("status"),
                       url=it.get("url"), source_id=source_id, raw=it.get("raw") or {}, categories=[]))
        inc("items_inserted")
    else:
        st.title, st.status = it.get("title") or st.title, it.get("status") or st.status
        inc("items_duplicate")
    s.flush()


def upsert_crawl_urls(s: Session, source_id: int, updates: dict[str, dict[str, Any]]) -> None:
    """Merge per-URL checkpoint updates (``{url: {field: value}}``) into ``crawl_urls``."""
    if not updates:
        return
    hashes = {url_hash(u): u for u in updates}
    existing = {c.url_hash: c for c in s.execute(select(CrawlUrl).where(
        CrawlUrl.source_id == source_id, CrawlUrl.url_hash.in_(list(hashes)))).scalars()}
    for h, u in hashes.items():
        rec = dict(updates[u])
        rec.pop("url", None)
        c = existing.get(h)
        if c is None:
            c = CrawlUrl(source_id=source_id, url=u, url_hash=h)
            s.add(c)
        if rec.get("last_fetched") is not None:
            c.fetch_count = (c.fetch_count or 0) + 1
        for k, v in rec.items():
            if hasattr(c, k) and v is not None:
                setattr(c, k, v)
