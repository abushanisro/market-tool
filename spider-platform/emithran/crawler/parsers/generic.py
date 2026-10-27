"""Configurable parsers: RSS/Atom, article text, HTML tables, CSS/XPath schemas, JSON-LD, AI-assisted.

All of them are driven by the source's ``extraction`` options. Example (html_table)::

    extraction:
      parser: html_table
      table_selector: "table.prices"
      kind: price
      columns: {entity: "Grade", value: "Price", unit: "Unit", period_date: "Date"}
      defaults: {currency: INR, unit: "INR/kg", country: IND, metric: "spot price"}
"""
from __future__ import annotations

import json
import logging
import os
import re
from email.utils import parsedate_to_datetime
from typing import Any

from lxml import etree, html as lhtml

from . import Follow, ParseContext, doc, obs, register

log = logging.getLogger(__name__)

ATOM = "{http://www.w3.org/2005/Atom}"


def _text(el) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip() if el is not None else ""


def _date(s: str | None) -> str | None:
    if not s:
        return None
    try:
        return parsedate_to_datetime(s).isoformat()
    except (TypeError, ValueError):
        return s


@register("rss")
def rss(response, ctx: ParseContext):
    """RSS 2.0 and Atom (arXiv). Stores headline/link/date; summaries only if store_summary=true."""
    root = etree.fromstring(response.body, parser=etree.XMLParser(recover=True, resolve_entities=False, no_network=True))
    store_summary = ctx.options.get("store_summary", False)
    kind = ctx.options.get("kind", "news" if "news" in ctx.source.get("data_kinds", []) else "publication")
    items = root.findall(".//item")
    for it in items:
        link = (it.findtext("link") or it.findtext("guid") or "").strip()
        if not link:
            continue
        yield doc(kind=kind, url=link, title=(it.findtext("title") or "").strip(), gate=bool(ctx.options.get("news_gate")),
                  published_at=_date(it.findtext("pubDate")),
                  summary=re.sub(r"<[^>]+>", " ", it.findtext("description") or "").strip()[:600] if store_summary else None,
                  raw={"categories": [c.text for c in it.findall("category") if c.text]})
        if ctx.options.get("fetch_articles"):
            yield Follow(link, parser="article")
    for e in root.findall(f".//{ATOM}entry"):
        link_el = e.find(f"{ATOM}link[@rel='alternate']") or e.find(f"{ATOM}link")
        link = link_el.get("href") if link_el is not None else e.findtext(f"{ATOM}id")
        if not link:
            continue
        yield doc(kind=kind, url=link, title=_text(e.find(f"{ATOM}title")), published_at=e.findtext(f"{ATOM}published"),
                  authors=[_text(a.find(f"{ATOM}name")) for a in e.findall(f"{ATOM}author")][:12],
                  summary=_text(e.find(f"{ATOM}summary"))[:600] if store_summary else None, raw={})


@register("article")
def article(response, ctx: ParseContext):
    """Main-text extraction for public reports/articles with trafilatura (Apache-2.0 ≥1.8)."""
    import trafilatura

    extracted = trafilatura.bare_extraction(response.text, url=response.url, with_metadata=True)
    if not extracted:
        return
    data = extracted if isinstance(extracted, dict) else extracted.as_dict()
    max_chars = int(ctx.options.get("excerpt_chars", 1200))
    yield doc(kind=ctx.options.get("kind", "report"), url=response.url, title=data.get("title") or response.url,
              published_at=data.get("date"), authors=[a for a in (data.get("author") or "").split("; ") if a],
              summary=data.get("description"), text_excerpt=(data.get("text") or "")[:max_chars],
              raw={"sitename": data.get("sitename"), "categories": data.get("categories"), "tags": data.get("tags")})


def _cell_map(header: list[str], cols: dict[str, str]) -> dict[str, int]:
    out = {}
    for field, wanted in cols.items():
        for i, h in enumerate(header):
            if h.strip().lower() == str(wanted).strip().lower():
                out[field] = i
                break
    return out


def rows_to_items(header: list[str], rows: list[list[Any]], ctx: ParseContext, url: str):
    cols = ctx.options.get("columns", {})
    defaults = ctx.options.get("defaults", {})
    kind = ctx.options.get("kind", "price")
    idx = _cell_map(header, cols)
    if "value" not in idx and "value_columns" not in ctx.options:
        raise ValueError(f"value column {cols.get('value')!r} not found in header {header}")
    value_cols = ctx.options.get("value_columns")  # wide tables: {"2026-01": "Jan 2026", ...}
    for r in rows:
        def get(f):
            i = idx.get(f)
            return r[i] if i is not None and i < len(r) else defaults.get(f)
        base = dict(kind=kind, entity=str(get("entity") or defaults.get("entity") or ""), metric=get("metric") or "value",
                    unit=get("unit"), currency=get("currency"), country=get("country"),
                    material_name=get("material") or (get("entity") if kind == "price" else None),
                    original={"row": [str(c) for c in r], "header": header, "url": url})
        if not base["entity"]:
            continue
        if value_cols:
            for period, colname in value_cols.items():
                if colname in header:
                    yield obs(**base, value=r[header.index(colname)], period_date=period)
        else:
            yield obs(**base, value=get("value"), period_date=get("period_date") or ctx.options.get("period_date"))


@register("html_table")
def html_table(response, ctx: ParseContext):
    tree = lhtml.fromstring(response.text)
    sel = ctx.options.get("table_selector", "table")
    tables = tree.cssselect(sel) if not sel.startswith("/") else tree.xpath(sel)
    for t in tables[: int(ctx.options.get("max_tables", 1))]:
        trs = t.xpath(".//tr")
        if not trs:
            continue
        hdr_i = int(ctx.options.get("header_row", 0))
        header = [_text(c) for c in trs[hdr_i].xpath("./th|./td")]
        rows = [[_text(c) for c in tr.xpath("./th|./td")] for tr in trs[hdr_i + 1:]]
        yield from rows_to_items(header, rows, ctx, response.url)
    yield from _follow_links(response, ctx)


@register("html_css")
def html_css(response, ctx: ParseContext):
    """Declarative CSS/XPath schema → observations or documents.

    extraction: {parser: html_css, item_selector: ".product", output: observation|document,
                 fields: {entity: {css: "h2::text"}, value: {css: ".price::text", regex: "([\\d.,]+)"}, ...},
                 defaults: {...}}
    """
    out_type = ctx.options.get("output", "observation")
    defaults = ctx.options.get("defaults", {})
    for node in response.css(ctx.options.get("item_selector", "body")):
        rec: dict[str, Any] = dict(defaults)
        for field, spec in ctx.options.get("fields", {}).items():
            val = node.css(spec["css"]).get() if "css" in spec else node.xpath(spec["xpath"]).get()
            if val is not None and spec.get("regex"):
                m = re.search(spec["regex"], val)
                val = m.group(1) if m else None
            if val is not None:
                rec[field] = val.strip()
        if out_type == "document":
            if rec.get("url"):
                rec["url"] = response.urljoin(rec["url"])
            yield doc(kind=rec.pop("kind", "report"), url=rec.pop("url", response.url),
                      title=rec.pop("title", response.url), raw=rec, **{k: rec.pop(k) for k in list(rec) if k in {"published_at", "summary"}})
        elif rec.get("entity") and rec.get("value") is not None:
            yield obs(kind=rec.pop("kind", "price"), entity=rec.pop("entity"), metric=rec.pop("metric", "value"),
                      value=rec.pop("value"), unit=rec.pop("unit", None), currency=rec.pop("currency", None),
                      country=rec.pop("country", None), period_date=rec.pop("period_date", None),
                      material_name=rec.pop("material", None), original={**rec, "url": response.url})
    yield from _follow_links(response, ctx)


@register("jsonld")
def jsonld(response, ctx: ParseContext):
    """schema.org JSON-LD (Product/Offer, Organization, Article) from public catalog pages."""
    for raw in response.xpath("//script[@type='application/ld+json']/text()").getall():
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        nodes = data if isinstance(data, list) else data.get("@graph", [data])
        for n in nodes:
            t = n.get("@type")
            t = t[0] if isinstance(t, list) else t
            if t == "Product":
                offers = n.get("offers") or {}
                offers = offers[0] if isinstance(offers, list) and offers else offers
                if offers.get("price") is not None:
                    yield obs(kind="price", entity=n.get("name", ""), material_name=n.get("name"),
                              metric="catalog_price", value=offers.get("price"), currency=offers.get("priceCurrency"),
                              unit=ctx.options.get("unit"),  # catalog prices are per item unless configured
                              original={"sku": n.get("sku"), "availability": offers.get("availability"), "url": response.url},
                              flags=["catalog_price"])
            elif t in {"Article", "NewsArticle", "Report", "TechArticle"}:
                yield doc(kind="report", url=response.url, title=n.get("headline") or n.get("name") or response.url,
                          published_at=n.get("datePublished"), raw={"jsonld_type": t})
    yield from _follow_links(response, ctx)


def _follow_links(response, ctx: ParseContext):
    """Link discovery for html sources: the spider applies scope/depth/dedupe rules."""
    if ctx.source.get("source_type") not in {"html", "sitemap"}:
        return
    for href in response.css("a::attr(href)").getall():
        yield Follow(response.urljoin(href))


@register("llm_extract")
def llm_extract(response, ctx: ParseContext):
    """AI-assisted extraction — only for sources where structured parsing is insufficient.

    Requires EMITHRAN_LLM_API_KEY (Anthropic Messages API). Output is validated against the target
    schema; every record is flagged ``ai_extracted`` and therefore never auto-verified.
    """
    key = os.environ.get("EMITHRAN_LLM_API_KEY")
    if not key:
        raise RuntimeError("llm_extract requires EMITHRAN_LLM_API_KEY; refusing to guess values")
    import httpx
    import trafilatura

    text = (trafilatura.extract(response.text) or "")[: int(ctx.options.get("max_chars", 12000))]
    schema = ctx.options.get("schema_hint", "entity, metric, value (number), unit, currency, country, period_date")
    prompt = (f"Extract only values explicitly present in the text. Return JSON list of objects with keys: {schema}. "
              f"Do not infer or estimate. If none, return [].\n\nTEXT:\n{text}")
    r = httpx.post("https://api.anthropic.com/v1/messages", timeout=60,
                   headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                   json={"model": ctx.options.get("model", os.environ.get("EMITHRAN_LLM_MODEL", "claude-sonnet-4-5")),
                         "max_tokens": 2000, "messages": [{"role": "user", "content": prompt}]})
    r.raise_for_status()
    body = "".join(b.get("text", "") for b in r.json().get("content", []))
    m = re.search(r"\[.*\]", body, re.S)
    for rec in json.loads(m.group(0)) if m else []:
        if not isinstance(rec, dict) or rec.get("value") in (None, "") or not rec.get("entity"):
            continue
        if str(rec["value"]).replace(".", "", 1).replace(",", "").strip().lstrip("-").isdigit() is False:
            continue
        yield obs(kind=ctx.options.get("kind", "price"), entity=str(rec["entity"]), metric=str(rec.get("metric", "value")),
                  value=rec["value"], unit=rec.get("unit"), currency=rec.get("currency"), country=rec.get("country"),
                  period_date=rec.get("period_date"), original={"llm_record": rec, "url": response.url},
                  flags=["ai_extracted"])
