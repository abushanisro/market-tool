"""Live manufacturing news feed: paged, filterable by taxonomy category, country, location and source."""
from __future__ import annotations

import datetime as dt
from collections import Counter
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import String, case, cast, func, or_, select
from sqlalchemy.orm import Session

from ...db import get_session
from ...models import Category, Country, Document, Source
from ...news.classify import load_topics
from ...auth import user
from ..deps import iso

router = APIRouter(prefix="/api/feed", tags=["feed"], dependencies=[Depends(user)])
SOURCE_LABEL = {"gdelt-manufacturing-news": "GDELT", "hackernews-manufacturing": "HN", "manufacturingdive-news": "MFG DIVE",
                "supplychaindive-news": "SC DIVE", "nist-manufacturing-news": "NIST"}


def _when():
    """Effective time: when it was published, or first seen if the publisher's date is in the future (event listings)."""
    return case((Document.published_at.is_(None), Document.observed_at), (Document.published_at > Document.observed_at, Document.observed_at),
                else_=Document.published_at)


def _effective(d: Document):
    p, o = d.published_at, d.observed_at
    if p is None or (o is not None and p.replace(tzinfo=None) > o.replace(tzinfo=None)):
        return o
    return p


def _split(tags: list[str] | None) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"cat": [], "country": [], "loc": [], "pub": []}
    for t in tags or []:
        k, _, v = t.partition(":")
        if k in out:
            out[k].append(v)
    return out


def _item(d: Document, src: dict[int, Source]) -> dict[str, Any]:
    t = _split(d.categories)
    s = src.get(d.source_id)
    raw = d.raw or {}
    return {"id": d.id, "title": d.title, "url": d.url, "published_at": iso(_effective(d)), "seen_at": iso(d.observed_at),
            "source": s.slug if s else None, "source_label": SOURCE_LABEL.get(s.slug, (s.name.split(":")[0] if s else "?")) if s else "?",
            "categories": t["cat"], "countries": t["country"], "locations": t["loc"], "publisher_country": (t["pub"] or [None])[0],
            "domain": raw.get("domain"), "points": raw.get("points"), "comments": raw.get("comments"), "discussion": raw.get("discussion")}


def _filters(cat: str | None, country: str | None, loc: str | None, source: str | None, q: str | None) -> list:
    cond = [Document.kind == "news"]
    tagcol = cast(Document.categories, String)
    if cat:
        cond.append(tagcol.like(f'%"cat:{cat}"%'))
    if country:
        cond.append(tagcol.like(f'%"country:{country}"%'))
    if loc:
        cond.append(tagcol.like(f'%"loc:{loc}"%'))
    if source:
        cond.append(Document.source_id == select(Source.id).where(Source.slug == source).scalar_subquery())
    if q:
        cond.append(or_(Document.title.ilike(f"%{q}%"), Document.url.ilike(f"%{q}%")))
    return cond


@router.get("")
def feed(cat: str | None = None, country: str | None = None, loc: str | None = None, source: str | None = None, q: str | None = None,
         page: int = Query(1, ge=1), size: int = Query(50, ge=10, le=200), since_id: int | None = None, s: Session = Depends(get_session)):
    """Newest first. ``since_id`` returns only items stored after that id (used by the live view to prepend new headlines)."""
    cond = _filters(cat, country, loc, source, q)
    if since_id is not None:
        cond.append(Document.id > since_id)
    total = s.execute(select(func.count()).select_from(Document).where(*cond)).scalar_one()
    rows = s.execute(select(Document).where(*cond).order_by(_when().desc(), Document.id.desc())
                     .offset(0 if since_id is not None else (page - 1) * size).limit(200 if since_id is not None else size)).scalars().all()
    src = {x.id: x for x in s.execute(select(Source)).scalars()}
    max_id = s.execute(select(func.max(Document.id)).where(Document.kind == "news")).scalar_one() or 0
    return {"total": total, "page": page, "size": size, "pages": max(1, -(-total // size)), "max_id": max_id, "items": [_item(d, src) for d in rows]}


@router.get("/facets")
def facets(days: int = Query(7, ge=1, le=3650), s: Session = Depends(get_session)):
    """Counts per taxonomy category, country and location over the last ``days``; every category is listed, even with zero news."""
    since = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(days=days)
    rows = s.execute(select(Document.categories, Document.source_id).where(Document.kind == "news", _when() >= since)).all()
    cats, countries, locs, sources = Counter(), Counter(), Counter(), Counter()
    for tags, sid in rows:
        t = _split(tags)
        cats.update(t["cat"]); countries.update(t["country"]); locs.update(t["loc"]); sources[sid] += 1
    fam = {c.slug: (c.name, c.family) for c in s.execute(select(Category)).scalars()}
    names = {c.iso3: c.name for c in s.execute(select(Country)).scalars()}
    topics = load_topics()
    memory_countries = list(topics["countries"])
    src = {x.id: x for x in s.execute(select(Source)).scalars()}
    hour = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(hours=1)
    last_hour = s.execute(select(func.count()).select_from(Document).where(Document.kind == "news", _when() >= hour)).scalar_one()
    return {"days": days, "headlines": len(rows), "last_hour": last_hour,
            "categories": [{"slug": slug, "name": fam.get(slug, (c["name"], "other"))[0], "family": fam.get(slug, (None, "other"))[1], "count": cats.get(slug, 0)}
                           for slug, c in topics["cats"].items()],
            "countries": sorted([{"iso3": i, "name": names.get(i, i), "count": n, "memory": i in memory_countries} for i, n in
                                 {**{c: 0 for c in memory_countries}, **countries}.items()], key=lambda x: (-x["count"], x["name"])),
            "locations": [{"name": k, "count": v} for k, v in locs.most_common(30)],
            "sources": [{"slug": src[i].slug, "label": SOURCE_LABEL.get(src[i].slug, src[i].name.split(":")[0]), "count": n} for i, n in sources.most_common() if i in src]}
