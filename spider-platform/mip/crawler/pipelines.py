"""Item pipeline: source validation rules → ObservationStore / Document / Standard upserts."""
from __future__ import annotations

import datetime as dt
import logging

from dateutil import parser as dateparser
from sqlalchemy import select

from ..db import session_scope
from ..models import Document, QualityIssue, Standard
from ..store import ObsIn, ObservationStore
from ..normalize.units import to_float

log = logging.getLogger(__name__)


def _validate(item: dict, rules: dict) -> str | None:
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


class StorePipeline:
    BATCH = 200

    def __init__(self, crawler):
        self.crawler = crawler
        self.buffer: list[dict] = []

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler)

    def process_item(self, item, spider=None):
        spider = spider or self.crawler.spider
        err = _validate(item, spider.source.get("validation") or {})
        if err:
            self.crawler.stats.inc_value("mip/items_invalid")
            item = {**item, "_invalid": err}
        self.buffer.append(item)
        if len(self.buffer) >= self.BATCH:
            self.flush(spider)
        return item

    def close_spider(self, spider=None):
        self.flush(spider or self.crawler.spider)

    def flush(self, spider) -> None:
        if not self.buffer:
            return
        items, self.buffer = self.buffer, []
        stats = self.crawler.stats
        src_id, job_id = spider.source["id"], spider.job_id
        with session_scope() as s:
            store = ObservationStore(s)
            for it in items:
                try:
                    if it.get("_invalid"):
                        s.add(QualityIssue(severity="error", code="validation_failed", message=it["_invalid"],
                                           source_id=src_id, job_id=job_id,
                                           context={k: str(v)[:300] for k, v in it.items() if k != "original"}))
                        continue
                    t = it["item_type"]
                    flags = list(it.get("flags", [])) + (["replayed"] if it.get("_replayed") else [])
                    if t == "observation":
                        r = store.add(ObsIn(
                            kind=it["kind"], entity=str(it["entity"]), metric=str(it["metric"]), value=it.get("value"),
                            unit=it.get("unit"), currency=it.get("currency"), period_date=it.get("period_date"),
                            country=it.get("country"), material_name=it.get("material_name"),
                            category_slug=it.get("category_slug"), source_id=src_id, source_url=it.get("_url"),
                            original=it.get("original", {}), flags=flags, job_id=job_id))
                        stats.inc_value(f"mip/items_{r.outcome}")
                    elif t == "document":
                        self._doc(s, it, src_id, job_id, flags)
                    elif t == "standard":
                        self._std(s, it, src_id)
                except Exception as exc:  # one bad record never aborts the batch
                    log.exception("store failed")
                    stats.inc_value("mip/items_error")
                    s.add(QualityIssue(severity="error", code="store_failed", message=str(exc)[:1000],
                                       source_id=src_id, job_id=job_id, context={"item": str(it)[:1000]}))

    def _doc(self, s, it, src_id, job_id, flags) -> None:
        stats = self.crawler.stats
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
                      text_excerpt=it.get("text_excerpt"), doi=it.get("doi"), license=it.get("license"),
                      raw={**(it.get("raw") or {}), "flags": flags}, source_id=src_id, job_id=job_id)
        if existing is None:
            s.add(Document(url=it["url"][:1000], **fields))
            stats.inc_value("mip/items_inserted")
        elif existing.title != fields["title"] or existing.published_at != pub:
            for k, v in fields.items():
                setattr(existing, k, v)
            stats.inc_value("mip/items_revised")
        else:
            stats.inc_value("mip/items_duplicate")
        s.flush()

    def _std(self, s, it, src_id) -> None:
        st = s.execute(select(Standard).where(Standard.code == it["code"], Standard.body == it.get("body"))).scalar_one_or_none()
        if st is None:
            s.add(Standard(code=it["code"], body=it.get("body"), title=it.get("title"), status=it.get("status"),
                           url=it.get("url"), source_id=src_id, raw=it.get("raw") or {}, categories=[]))
            self.crawler.stats.inc_value("mip/items_inserted")
        else:
            st.title, st.status = it.get("title") or st.title, it.get("status") or st.status
            self.crawler.stats.inc_value("mip/items_duplicate")
        s.flush()
