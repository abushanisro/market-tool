"""Query generators for news collectors: one request per manufacturing category and per country, from topics.yaml.

Used by ``start_urls_for`` (both crawl engines) when a source declares ``extraction.url_generator``; because the URLs are
built at plan time, editing topics.yaml changes what is collected without touching the source registry.
"""
from __future__ import annotations

import time
from urllib.parse import parse_qs, quote, urlparse

from .classify import load_topics

GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"
HN = "https://hn.algolia.com/api/v1/search_by_date"
MAX_TERMS = 6


def _or(terms: list[str]) -> str:
    q = [f'"{t}"' for t in terms]
    return q[0] if len(q) == 1 else "(" + " OR ".join(q) + ")"            # GDELT rejects parentheses around a single term


def gdelt_queries() -> list[tuple[str, str | None, str | None]]:
    """[(query, category slug, country ISO3)]: one per category, one generic manufacturing query per country."""
    t = load_topics()
    out = []
    for slug, c in t["cats"].items():
        if c["raw"]:
            out.append((f"{_or(c['raw'][:MAX_TERMS])} sourcelang:english", slug, None))
    for iso3, c in t["countries"].items():
        out.append((f'(manufacturing OR factory OR "production plant" OR foundry) sourcecountry:{c["gdelt"]} sourcelang:english', None, iso3))
    return out


def gdelt_urls(timespan: str = "6h", maxrecords: int = 75) -> list[str]:
    return [f"{GDELT}?query={quote(q, safe='')}&mode=artlist&format=json&maxrecords={maxrecords}&sort=datedesc&timespan={timespan}"
            for q, _c, _i in gdelt_queries()]


def gdelt_hint(url: str) -> tuple[str | None, str | None]:
    """Which category / country a GDELT request was built for (so a hit counts as a signal for that topic)."""
    q = (parse_qs(urlparse(url).query).get("query") or [""])[0]
    for query, slug, iso3 in gdelt_queries():
        if query == q:
            return slug, iso3
    return None, None


def hn_urls(hits: int = 20, days: int = 90) -> list[str]:
    """Hacker News: one search per strong category keyword (first two), manufacturing topics only, recent stories only."""
    t = load_topics()
    since = int(time.time()) - days * 86400
    urls = []
    for slug, c in t["cats"].items():
        for kw in c["raw"][:2]:
            urls.append(f"{HN}?tags=story&query={quote(kw, safe='')}&hitsPerPage={hits}&numericFilters={quote(f'created_at_i>{since}', safe='')}")
    return urls


GENERATORS = {"gdelt_topics": lambda opts, params: gdelt_urls(params.get("timespan") or opts.get("timespan", "6h")),
              "hn_topics": lambda opts, params: hn_urls()}


def generate(name: str, options: dict, params: dict) -> list[str]:
    if name not in GENERATORS:
        raise ValueError(f"unknown url_generator {name!r}")
    return GENERATORS[name](options, params)
