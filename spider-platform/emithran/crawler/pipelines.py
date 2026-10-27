"""Item pipeline: source validation rules → ObservationStore / Document / Standard upserts."""
from __future__ import annotations

from ..db import session_scope
from .writer import persist_items, validate


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
        err = validate(item, spider.source.get("validation") or {})
        if err:
            self.crawler.stats.inc_value("emithran/items_invalid")
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
        with session_scope() as s:
            persist_items(s, items, source_id=spider.source["id"], job_id=spider.job_id,
                          inc=lambda name: stats.inc_value(f"emithran/{name}"))
