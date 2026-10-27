"""One spider for every registered source.

The source row (loaded from the registry) decides: start URLs / sitemaps / backfill windows,
scope (allowed domains, allow/deny regexes, depth, page budget), pagination and the parser.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import scrapy
from lxml import etree
from sqlalchemy import select

from ...config import get_settings
from ...db import session_scope
from ...models import CrawlJob, CrawlUrl, Source
from ..middlewares import url_hash
from ..parsers import PARSERS, Follow, ParseContext, jget

TRACKING = re.compile(r"^(utm_|fbclid|gclid|mc_eid|mc_cid|ref$|ref_src)")


def canonicalize(url: str) -> str:
    p = urlparse(url)
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not TRACKING.match(k)]
    return urlunparse((p.scheme.lower(), (p.hostname or "").lower() + (f":{p.port}" if p.port else ""),
                       p.path or "/", "", urlencode(q, safe=";:,*"), ""))


def render_template(url: str, params: dict[str, Any]) -> str:
    today = dt.date.today()
    values = {"today": today.isoformat(), "year": today.year, "contact_email": get_settings().contact_email or "",
              **params}
    out = url
    for k, v in values.items():
        out = out.replace("{" + k + "}", str(v))
    out = re.sub(r"[&?]mailto=(?=&|$)", "", out)  # drop empty mailto
    return out


def start_urls_for(source: dict[str, Any], params: dict[str, Any]) -> list[str]:
    """Start URLs for a job: explicit backfill windows or the source's templated start URLs."""
    bf = source.get("backfill") or {}
    if params.get("backfill") and bf.get("url_template"):
        start = dt.date.fromisoformat(params["start_date"])
        end = dt.date.fromisoformat(params.get("end_date") or dt.date.today().isoformat())
        chunk = int(bf.get("chunk_days", 365))
        urls, cur = [], start
        while cur <= end:
            stop = min(end, cur + dt.timedelta(days=chunk - 1))
            urls.append(render_template(bf["url_template"], {"start_date": cur.isoformat(), "end_date": stop.isoformat()}))
            cur = stop + dt.timedelta(days=1)
        return urls
    gen = (source.get("extraction") or {}).get("url_generator")
    if gen:                                                    # e.g. gdelt_topics: one request per manufacturing category / country
        from ...news.collect import generate

        return generate(gen, source.get("extraction") or {}, params)
    return [render_template(u, params) for u in source.get("start_urls") or []]


def next_page_urls(pg: dict[str, Any], response) -> list[tuple[str, int]]:
    """Follow-up (url, page) pairs for a source's ``pagination`` config; ``response`` is page 1."""
    try:
        data = json.loads(response.text)
    except ValueError:
        data = None
    url = response.url
    max_pages = int(pg.get("max_pages", 5))
    out: list[tuple[str, int]] = []
    if pg.get("type") == "page":
        if pg.get("pages_path"):
            pages = int(jget(data, pg["pages_path"], 1) or 1)
        elif pg.get("total_path"):
            total = int(jget(data, pg["total_path"], 0) or 0)
            per = int(jget(data, pg.get("per_page_path", ""), 0) or pg.get("size", 25))
            pages = -(-total // per) if per else 1
        else:
            pages = max_pages
        for p in range(2, min(pages, max_pages) + 1):
            out.append((_set_param(url, pg.get("param", "page"), p), p))
    elif pg.get("type") == "offset":
        total = int(jget(data, pg.get("total_path", ""), 0) or 0)
        size = int(pg.get("size", 20))
        for i in range(1, max_pages):
            off = i * size
            if off >= total:
                break
            out.append((_set_param(url, pg.get("param", "offset"), off), i + 1))
    elif pg.get("type") == "next_link":
        nxt = response.css(pg.get("selector", "a[rel=next]::attr(href)")).get()
        if nxt:
            out.append((response.urljoin(nxt), 1))
    return out


def snapshot(src: Source) -> dict[str, Any]:
    return {c.name: getattr(src, c.name) for c in Source.__table__.columns}


class RegistrySpider(scrapy.Spider):
    name = "registry"
    custom_settings: dict = {}

    def __init__(self, job_id: int, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.job_id = int(job_id)
        with session_scope() as s:
            job = s.get(CrawlJob, self.job_id)
            if job is None:
                raise ValueError(f"job {job_id} not found")
            self.source = snapshot(job.source)
            self.params = dict(job.params or {})
            self.checkpoints = {
                c.url: {"etag": c.etag, "last_modified": c.last_modified, "content_hash": c.content_hash,
                        "last_fetched": c.last_fetched}
                for c in s.execute(select(CrawlUrl).where(CrawlUrl.source_id == job.source_id)).scalars()
            }
        self.allowed_domains = list(self.source.get("allowed_domains") or [])
        self.allow = [re.compile(p) for p in self.source.get("allow_patterns") or []]
        self.deny = [re.compile(p) for p in self.source.get("deny_patterns") or []]
        self.seen: set[str] = set()
        self.pages_budget = min(int(self.source.get("max_pages") or 50), get_settings().global_max_pages_per_job)
        self.scheduled = 0
        self.url_updates: dict[str, dict] = {}
        self.force = bool(self.params.get("force_refresh"))
        self.ctx = ParseContext(self.source, dict(self.source.get("extraction") or {}), self.job_id, self.params)

    # ------------------------------------------------------------------ checkpoint bookkeeping
    def record_url(self, url: str, **fields) -> None:
        rec = self.url_updates.setdefault(url, {"url": url})
        rec.update(fields)

    # ------------------------------------------------------------------ scope
    def in_scope(self, url: str, depth: int) -> str | None:
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            return "scheme"
        host = (p.hostname or "").lower()
        if self.allowed_domains and not any(host == d or host.endswith("." + d) for d in self.allowed_domains):
            return "offsite"
        if depth > int(self.source.get("max_depth") or 0):
            return "depth"
        if any(rx.search(url) for rx in self.deny):
            return "denied"
        return None

    def make_request(self, url: str, *, depth: int = 0, parser: str | None = None, meta: dict | None = None,
                     callback=None, priority: int = 0, is_start: bool = False) -> scrapy.Request | None:
        url = canonicalize(url)
        if url in self.seen:
            self.crawler.stats.inc_value("emithran/urls_duplicate")
            return None
        reason = self.in_scope(url, depth)
        if reason:
            self.crawler.stats.inc_value(f"emithran/urls_out_of_scope/{reason}")
            return None
        # allow_patterns restrict *followed* links (start/pagination URLs are explicit)
        if self.allow and not is_start and not (meta or {}).get("pagination") and not any(rx.search(url) for rx in self.allow):
            self.crawler.stats.inc_value("emithran/urls_out_of_scope/pattern")
            return None
        if self.scheduled >= self.pages_budget:
            self.crawler.stats.inc_value("emithran/urls_over_budget")
            self.record_url(url, status="skipped", depth=depth, error="page budget reached")
            return None
        self.seen.add(url)
        self.scheduled += 1
        self.crawler.stats.inc_value("emithran/urls_discovered")
        self.crawler.stats.inc_value("emithran/urls_queued")
        self.record_url(url, status="discovered", depth=depth)
        m = {"depth": depth, "parser": parser, "force_refresh": self.force, **(meta or {})}
        if self.source.get("render_js"):
            m["playwright"] = True
        headers = {}
        if self.source.get("source_type") == "api":
            headers["Accept"] = "application/json"
        return scrapy.Request(url, callback=callback or self.parse_response, errback=self.on_error, meta=m,
                              priority=priority, headers=headers, dont_filter=False)

    # ------------------------------------------------------------------ start
    def start_urls_for_job(self) -> list[str]:
        return start_urls_for(self.source, self.params)

    async def start(self):
        for u in self.source.get("sitemap_urls") or []:
            r = self.make_request(render_template(u, self.params), callback=self.parse_sitemap, is_start=True)
            if r:
                yield r
        for u in self.start_urls_for_job():
            r = self.make_request(u, is_start=True, meta={"page": 1})
            if r:
                yield r

    # ------------------------------------------------------------------ sitemaps
    def parse_sitemap(self, response):
        self._fetched(response)
        root = etree.fromstring(response.body, parser=etree.XMLParser(recover=True, resolve_entities=False, no_network=True))
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        for loc in root.xpath("//sm:sitemap/sm:loc/text()", namespaces=ns):
            r = self.make_request(loc.strip(), callback=self.parse_sitemap, is_start=True)
            if r:
                yield r
        for u in root.xpath("//sm:url", namespaces=ns):
            loc = (u.findtext("sm:loc", namespaces=ns) or "").strip()
            lastmod = u.findtext("sm:lastmod", namespaces=ns)
            if not loc or (self.allow and not any(rx.search(loc) for rx in self.allow)):
                continue
            cp = self.checkpoints.get(canonicalize(loc))
            if cp and lastmod and cp.get("last_fetched") and not self.force:
                try:
                    lm = dt.datetime.fromisoformat(lastmod.replace("Z", "+00:00"))
                    lf = cp["last_fetched"] if cp["last_fetched"].tzinfo else cp["last_fetched"].replace(tzinfo=dt.timezone.utc)
                    if lm.tzinfo is None:
                        lm = lm.replace(tzinfo=dt.timezone.utc)
                    if lm <= lf:
                        self.crawler.stats.inc_value("emithran/sitemap_unchanged")
                        continue
                except ValueError:
                    pass
            r = self.make_request(loc, depth=0, is_start=True)
            if r:
                yield r

    # ------------------------------------------------------------------ main callback
    def _fetched(self, response) -> str:
        h = hashlib.sha256(response.body).hexdigest()
        self.record_url(response.request.url, status="fetched", http_status=response.status,
                        etag=(response.headers.get("ETag") or b"").decode() or None,
                        last_modified=(response.headers.get("Last-Modified") or b"").decode() or None,
                        content_hash=h, last_fetched=dt.datetime.now(dt.timezone.utc), last_job_id=self.job_id,
                        depth=response.meta.get("depth", 0))
        self.crawler.stats.inc_value("emithran/pages_fetched")
        return h

    def parse_response(self, response):
        url = response.request.url
        h = self._fetched(response)
        if response.status >= 400:
            self.record_url(url, status="failed", error=f"HTTP {response.status}")
            self.crawler.stats.inc_value("emithran/requests_failed")
            self.log_event("warning", f"HTTP {response.status} for {url}")
            return
        cp = self.checkpoints.get(url)
        if cp and cp.get("content_hash") == h and not self.force:
            self.record_url(url, status="unchanged")
            self.crawler.stats.inc_value("emithran/pages_unchanged")
            return
        extract_rx = self.ctx.options.get("extract_patterns")
        if extract_rx and not response.meta.get("parser") and not any(re.search(p, url) for p in extract_rx):
            # navigation page: discover links only
            for href in response.css("a::attr(href)").getall():
                r = self.make_request(response.urljoin(href), depth=response.meta.get("depth", 0) + 1)
                if r:
                    yield r
            return
        parser_name = response.meta.get("parser") or self.ctx.options.get("parser")
        parser = PARSERS.get(parser_name or "")
        if parser is None:
            raise ValueError(f"source has no runnable parser ({parser_name!r})")
        n = 0
        depth = response.meta.get("depth", 0)
        try:
            for out in parser(response, self.ctx):
                if isinstance(out, Follow):
                    r = self.make_request(out.url, depth=depth + 1, parser=out.parser, meta=out.meta, priority=out.priority)
                    if r:
                        yield r
                    continue
                out["_url"] = url
                out["_replayed"] = bool(response.meta.get("replayed"))
                if out["_replayed"]:
                    out.setdefault("original", {})["replay"] = response.meta.get("replay", {})
                n += 1
                self.crawler.stats.inc_value("emithran/items_extracted")
                yield out
        except Exception as exc:
            self.crawler.stats.inc_value("emithran/processing_errors")
            self.record_url(url, status="failed", error=f"parse error: {exc}"[:500])
            self.log_event("error", f"parse error on {url}: {exc}")
            return
        self.record_url(url, items_extracted=n)
        yield from self.paginate(response, depth)

    def paginate(self, response, depth):
        pg = self.source.get("pagination") or {}
        if not pg or response.meta.get("page", 1) != 1 or response.meta.get("parser") not in (None, self.ctx.options.get("parser")):
            return
        for url, page in next_page_urls(pg, response):
            r = self.make_request(url, depth=depth, meta={"page": page, "pagination": True})
            if r:
                yield r

    def on_error(self, failure):
        req = failure.request
        msg = f"{failure.type.__name__}: {failure.value}"[:500]
        if "not modified" in msg:
            return
        self.crawler.stats.inc_value("emithran/requests_failed")
        self.record_url(req.url, status="failed", error=msg, last_job_id=self.job_id)
        self.log_event("error", f"request failed {req.url}: {msg}")

    def log_event(self, level: str, message: str) -> None:
        mon = getattr(self.crawler, "emithran_monitor", None)
        if mon:
            mon.log(level, message)
        getattr(self.logger, level if level != "warning" else "warning")(message)


def _set_param(url: str, key: str, value) -> str:
    p = urlparse(url)
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k != key] + [(key, str(value))]
    return urlunparse(p._replace(query=urlencode(q, safe=";:,*")))
