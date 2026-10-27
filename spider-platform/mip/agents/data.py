"""Read-only data access for agents. Everything an agent says is derived from these queries."""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from statistics import median
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Country, Machine, Material, Observation, Source

INTERNAL_SLUG = "emithran-taxonomy"
# Pink Sheet series -> material families of the taxonomy that the benchmark is a proxy for.
COMMODITY_FAMILIES: dict[str, list[str]] = {
    "aluminum": ["aluminium"], "copper": ["copper", "brass_bronze"], "nickel": ["nickel", "superalloy", "stainless_steel"],
    "zinc": ["zinc", "galvanized_steel"], "lead": ["lead"], "tin": ["tin"],
    "iron-ore-cfr-spot": ["steel", "cast_iron", "tool_steel"], "rubber-rss3": ["rubber_elastomer"],
    "crude-oil-brent": ["thermoplastic", "thermoset", "additive_polymer"], "natural-gas-europe": ["thermoplastic"],
    "gold": ["precious_metal"], "silver": ["precious_metal"], "platinum": ["precious_metal"],
}


def source_id(s: Session, slug: str) -> int | None:
    return s.execute(select(Source.id).where(Source.slug == slug)).scalar_one_or_none()


def slugify(text: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def series(s: Session, slug: str, kind: str, *, metric: str | None = None) -> dict[tuple[str, str | None], list[tuple[dt.date, float]]]:
    """{(entity, country): [(period_date, value), ...]} sorted by date, one value per date (latest extracted wins)."""
    sid = source_id(s, slug)
    if sid is None:
        return {}
    q = select(Observation).where(Observation.source_id == sid, Observation.kind == kind, Observation.period_date.is_not(None),
                                  Observation.value.is_not(None), Observation.status.in_(["verified", "unverified"]))
    if metric:
        q = q.where(Observation.metric == metric)
    by: dict[tuple[str, str | None], dict[dt.date, tuple[int, float]]] = defaultdict(dict)
    for o in s.execute(q.order_by(Observation.id)).scalars():
        by[(o.entity, o.country_iso3)][o.period_date] = (o.id, o.value)
    return {k: sorted((d, v[1]) for d, v in m.items()) for k, m in by.items()}


def pct_change(points: list[tuple[dt.date, float]], days: int) -> tuple[float | None, tuple[dt.date, float] | None]:
    """Change from the observation nearest to ``days`` before the last one (never extrapolates past the available history)."""
    if len(points) < 2:
        return None, None
    last_d, last_v = points[-1]
    target = last_d - dt.timedelta(days=days)
    older = [p for p in points if p[0] <= target]
    if not older:
        return None, None
    base = older[-1]
    return ((last_v - base[1]) / base[1] if base[1] else None), base


def family_counts(s: Session) -> dict[str, int]:
    return dict(s.execute(select(Material.family, func.count()).group_by(Material.family)).all())


def machines_by_country(s: Session) -> dict[str, int]:
    return dict(s.execute(select(Machine.country_iso3, func.count()).where(Machine.country_iso3.is_not(None))
                          .group_by(Machine.country_iso3)).all())


def currency_countries(s: Session) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for iso3, cur in s.execute(select(Country.iso3, Country.currency).where(Country.currency.is_not(None), Country.is_region.is_(False))):
        out[cur].append(iso3)
    return out


def internal_price_snapshot(s: Session, families: list[str]) -> dict[str, Any] | None:
    """Median normalized USD/kg of the taxonomy's own verified prices for these families, with their dominant date."""
    sid = source_id(s, INTERNAL_SLUG)
    rows = s.execute(select(Observation.value_usd, Observation.period_date).join(Material, Material.id == Observation.material_id)
                     .where(Observation.kind == "price", Observation.unit_normalized == "USD/kg", Observation.status == "verified",
                            Observation.value_usd.is_not(None), Observation.period_date.is_not(None), Material.family.in_(families))).all()
    if not rows:
        return None
    dates = defaultdict(int)
    for _, d in rows:
        dates[d] += 1
    return {"median_usd_kg": median(v for v, _ in rows), "n": len(rows), "date": max(dates, key=dates.get)}
