"""News collectors: GDELT article lists and Hacker News search, manufacturing only.

Parsers only extract; classification, the manufacturing-relevance gate and tagging happen in one place (writer.persist_items) so
every news source, including plain RSS, is treated identically. Only headline, link, date and publisher metadata are stored.
"""
from __future__ import annotations

import json

from ...news.classify import trusted_domain
from ...news.collect import gdelt_hint
from ...normalize.countries import to_iso3
from . import ParseContext, doc, register


@register("gdelt_artlist")
def gdelt_artlist(response, ctx: ParseContext):
    text = response.text.strip()
    if not text.startswith("{"):
        raise ValueError(f"GDELT: {text[:160] or 'empty response'}")          # errors (rate limit, bad query) arrive as plain text
    cat, country = gdelt_hint(response.url)
    for a in json.loads(text).get("articles") or []:
        if not a.get("url") or not a.get("title"):
            continue
        yield doc(kind="news", url=a["url"], title=a["title"].strip(), published_at=a.get("seendate"), authors=[], summary=None,
                  gate=True, trusted=trusted_domain(a.get("domain")), publisher_country=to_iso3(a.get("sourcecountry")),
                  hint={"category": cat, "country": country},
                  raw={"domain": a.get("domain"), "language": a.get("language"), "sourcecountry": a.get("sourcecountry"), "via": "gdelt"})


@register("hn_algolia")
def hn_algolia(response, ctx: ParseContext):
    for h in json.loads(response.text).get("hits") or []:
        title = (h.get("title") or h.get("story_title") or "").strip()
        hn_id = h.get("objectID")
        if not title or not hn_id:
            continue
        discussion = f"https://news.ycombinator.com/item?id={hn_id}"
        yield doc(kind="news", url=h.get("url") or discussion, title=title, published_at=h.get("created_at"), authors=[h["author"]] if h.get("author") else [],
                  summary=None, gate=True, raw={"via": "hackernews", "hn_id": hn_id, "points": h.get("points"), "comments": h.get("num_comments"),
                                                "discussion": discussion, "domain": (h.get("url") or "").split("/")[2] if h.get("url") else "news.ycombinator.com"})
