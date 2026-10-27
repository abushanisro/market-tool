"""Currency conversion backed by FX observations stored in the database.

Priority: crawled FX observations (kind='fx', verified) closest on/before the requested
date → imported exchange_rate_table.csv (flagged ``fx_fallback_internal_table``).
Conversion metadata is returned so every converted value carries its provenance.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Observation


@dataclass
class FxResult:
    rate: float  # units of `currency` per 1 USD
    as_of: dt.date | None
    source: str
    flags: list[str]


class FxConverter:
    def __init__(self, session: Session):
        self.s = session
        self._cache: dict[tuple[str, dt.date | None], FxResult | None] = {}

    def usd_rate(self, currency: str, on: dt.date | None = None) -> FxResult | None:
        currency = currency.upper()
        if currency == "USD":
            return FxResult(1.0, on, "identity", [])
        key = (currency, on)
        if key in self._cache:
            return self._cache[key]
        q = (
            select(Observation)
            .where(Observation.kind == "fx", Observation.entity == f"USD/{currency}",
                   Observation.status.in_(["verified", "unverified"]))
            .order_by(Observation.period_date.desc())
        )
        if on:
            q = q.where(Observation.period_date <= on)
        rows = self.s.execute(q.limit(5)).scalars().all()
        # prefer crawled sources over the internal table
        crawled = [r for r in rows if r.source_id is not None and "fx_internal_table" not in (r.quality_flags or [])]
        pick = crawled[0] if crawled else (rows[0] if rows else None)
        res = None
        if pick and pick.value:
            flags = []
            if pick.source_id is None or "fx_internal_table" in (pick.quality_flags or []):
                flags.append("fx_fallback_internal_table")
            if on and pick.period_date and (on - pick.period_date).days > 45:
                flags.append("fx_rate_stale")
            res = FxResult(pick.value, pick.period_date, pick.source_url or "internal", flags)
        self._cache[key] = res
        return res

    def to_usd(self, amount: float, currency: str, on: dt.date | None = None) -> tuple[float | None, list[str], dict]:
        r = self.usd_rate(currency, on)
        if r is None:
            return None, ["fx_rate_missing"], {}
        return amount / r.rate, r.flags, {"fx_rate": r.rate, "fx_as_of": str(r.as_of) if r.as_of else None, "fx_source": r.source}
