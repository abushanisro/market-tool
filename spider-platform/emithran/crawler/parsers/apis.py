"""Source-specific parsers for documented APIs."""
from __future__ import annotations

import datetime as dt
import itertools
import json

from . import ParseContext, doc, jget, obs, register

# UN M49 numeric codes used by UN Comtrade → ISO3 (subset; unknown codes are kept as raw values)
M49 = {"699": "IND", "356": "IND", "842": "USA", "156": "CHN", "276": "DEU", "484": "MEX", "392": "JPN",
       "410": "KOR", "704": "VNM", "458": "MYS", "702": "SGP", "203": "CZE", "348": "HUN", "528": "NLD",
       "124": "CAN", "0": "WLD"}


def _json(response):
    return json.loads(response.text)


@register("frankfurter")
def frankfurter(response, ctx: ParseContext):
    d = _json(response)
    base = d.get("base", "EUR")
    rates = d.get("rates", {})
    if rates and all(isinstance(v, dict) for v in rates.values()):  # time series
        series = rates.items()
    else:
        series = [(d.get("date"), rates)]
    for day, rs in series:
        for cur, rate in (rs or {}).items():
            yield obs(kind="fx", entity=f"{base}/{cur}", metric="exchange_rate", value=rate,
                      unit=f"{cur} per {base}", currency=cur, period_date=day,
                      original={"base": base, "date": day, "currency": cur, "rate": rate})


@register("worldbank_indicator")
def worldbank_indicator(response, ctx: ParseContext):
    d = _json(response)
    if not isinstance(d, list) or len(d) < 2 or d[1] is None:
        msg = jget(d, "0.message.0.value") if isinstance(d, list) else None
        if msg:
            raise ValueError(f"World Bank API error: {msg}")
        return
    meta = d[0]
    for r in d[1]:
        if r.get("value") is None:
            continue  # missing values are not invented
        yield obs(kind="indicator", entity=r["indicator"]["value"], metric=r["indicator"]["id"],
                  value=r["value"], unit=r.get("unit") or ("%" if r["indicator"]["id"].endswith(".ZS") else "USD"),
                  currency="USD" if r["indicator"]["id"].endswith(".CD") else None,
                  country=r.get("countryiso3code") or r["country"]["id"], period_date=r["date"],
                  original={**r, "lastupdated": meta.get("lastupdated")})


@register("eurostat_jsonstat")
def eurostat_jsonstat(response, ctx: ParseContext):
    """Decode a JSON-stat 2.0 dataset (row-major flat index)."""
    d = _json(response)
    ids, sizes = d["id"], d["size"]
    values = d.get("value", {})
    if isinstance(values, list):
        values = {str(i): v for i, v in enumerate(values) if v is not None}
    cats = []
    for dim in ids:
        idx = d["dimension"][dim]["category"]["index"]
        order = sorted(idx, key=idx.get) if isinstance(idx, dict) else list(idx)
        cats.append(order)
    labels = {dim: d["dimension"][dim]["category"].get("label", {}) for dim in ids}
    metric = ctx.options.get("metric") or d.get("label", "value")
    status = d.get("status", {})
    for flat, combo in enumerate(itertools.product(*cats)):
        v = values.get(str(flat))
        if v is None:
            continue
        coord = dict(zip(ids, combo))
        geo = coord.get("geo")
        yield obs(kind="indicator", entity=d.get("label", "Eurostat"), metric=metric, value=v,
                  unit=labels.get("unit", {}).get(coord.get("unit"), coord.get("unit")),
                  country=geo, period_date=coord.get("time"),
                  original={"coordinates": coord, "status": status.get(str(flat)), "updated": d.get("updated")})
    _ = sizes


@register("comtrade")
def comtrade(response, ctx: ParseContext):
    d = _json(response)
    for r in d.get("data") or []:
        iso = r.get("reporterISO") or M49.get(str(r.get("reporterCode")), str(r.get("reporterCode")))
        label = f"HS {r.get('cmdCode')} {r.get('cmdDesc') or ''}".strip()
        flow = r.get("flowDesc") or r.get("flowCode")
        common = dict(entity=label, country=iso, period_date=str(r.get("period") or r.get("refYear")), original=r)
        if r.get("primaryValue") is not None:
            yield obs(kind="trade", metric=f"{flow} value", value=r["primaryValue"], unit="USD", currency="USD", **common)
        if r.get("netWgt"):
            yield obs(kind="trade", metric=f"{flow} net weight", value=r["netWgt"], unit="kg", **common)


@register("openalex")
def openalex(response, ctx: ParseContext):
    d = _json(response)
    for w in d.get("results", []):
        url = w.get("doi") or w.get("id")
        if not url:
            continue
        loc = (w.get("primary_location") or {}).get("source") or {}
        yield doc(kind="publication", url=url, title=w.get("display_name") or w.get("title") or "(untitled)",
                  published_at=w.get("publication_date"), doi=(w.get("doi") or "").replace("https://doi.org/", "") or None,
                  authors=[a["author"]["display_name"] for a in (w.get("authorships") or [])[:12] if a.get("author")],
                  summary=None, license=(w.get("primary_location") or {}).get("license"),
                  raw={"openalex_id": w.get("id"), "venue": loc.get("display_name"), "cited_by_count": w.get("cited_by_count"),
                       "type": w.get("type"), "topics": [t.get("display_name") for t in (w.get("topics") or [])[:5]]})


@register("crossref")
def crossref(response, ctx: ParseContext):
    d = _json(response)
    for it in jget(d, "message.items", []) or []:
        parts = jget(it, "published.date-parts.0") or jget(it, "issued.date-parts.0") or []
        pub = None
        if parts:
            pub = dt.date(int(parts[0]), int(parts[1]) if len(parts) > 1 else 1, int(parts[2]) if len(parts) > 2 else 1).isoformat()
        yield doc(kind="publication", url=it.get("URL") or f"https://doi.org/{it.get('DOI')}",
                  title=(it.get("title") or ["(untitled)"])[0], published_at=pub, doi=it.get("DOI"),
                  authors=[f"{a.get('given', '')} {a.get('family', '')}".strip() for a in (it.get("author") or [])[:12]],
                  license=jget(it, "license.0.URL"),
                  raw={"container": (it.get("container-title") or [None])[0], "type": it.get("type"),
                       "publisher": it.get("publisher")})
