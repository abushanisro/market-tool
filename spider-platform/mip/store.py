"""Observation persistence: normalization → quality → dedupe/revision → insert.

Shared by the taxonomy importer and the Scrapy pipeline so that every record, internal or
crawled, goes through identical rules.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from dateutil import parser as dateparser
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Category, Material, Observation, QualityIssue, Source
from .normalize import quality
from .normalize.countries import to_iso3
from .normalize.currency import FxConverter
from .normalize.materials import classify_family, extract_grade, material_key
from .normalize.units import parse_price_unit, to_float


def sha(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def parse_date(v: Any) -> dt.date | None:
    if v is None or v == "":
        return None
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    s = str(v).strip()
    m = re.fullmatch(r"(\d{4})M(\d{2})", s)  # World Bank / Eurostat monthly "2026M09"
    if m:
        return dt.date(int(m.group(1)), int(m.group(2)), 1)
    m = re.fullmatch(r"(\d{4})-(\d{2})", s)
    if m:
        return dt.date(int(m.group(1)), int(m.group(2)), 1)
    if re.fullmatch(r"\d{4}", s):
        return dt.date(int(s), 12, 31)  # annual value: period end
    try:
        return dateparser.parse(s).date()
    except (ValueError, OverflowError):
        return None


@dataclass
class ObsIn:
    kind: str
    entity: str
    metric: str
    value: Any
    unit: str | None = None
    currency: str | None = None
    period_date: Any = None
    country: str | None = None
    material_name: str | None = None
    material_type: str | None = None
    material_id: int | None = None
    category_slug: str | None = None
    source_id: int | None = None
    dataset_id: int | None = None
    source_url: str | None = None
    original: dict[str, Any] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    job_id: int | None = None
    series_hint: str | None = None


@dataclass
class StoreResult:
    outcome: str  # inserted | duplicate | revised | quarantined
    observation_id: int | None = None
    status: str | None = None


class ObservationStore:
    def __init__(self, session: Session, *, check_history: bool = True):
        self.s = session
        self.fx = FxConverter(session)
        self.check_history = check_history
        self._material_cache: dict[str, Material] = {}
        self._category_cache: dict[str, int | None] = {}
        self._source_cache: dict[int, Source] = {}
        self._seen_fp: set[str] = set()

    # ------------------------------------------------------------------ resolution helpers
    def resolve_material(self, name: str, material_type: str | None = None) -> Material:
        key = material_key(name)
        m = self._material_cache.get(key)
        if m is None:
            m = self.s.execute(select(Material).where(Material.key == key)).scalar_one_or_none()
        if m is None:
            m = Material(key=key, name=name.strip(), original_names=[name], family=classify_family(name, material_type),
                         material_type=material_type, grade=extract_grade(name), designations={}, properties={})
            self.s.add(m)
            self.s.flush()
        elif name not in (m.original_names or []):
            m.original_names = list(m.original_names or []) + [name]
        self._material_cache[key] = m
        return m

    def category_id(self, slug: str | None) -> int | None:
        if not slug:
            return None
        if slug not in self._category_cache:
            self._category_cache[slug] = self.s.execute(select(Category.id).where(Category.slug == slug)).scalar_one_or_none()
        return self._category_cache[slug]

    def source(self, source_id: int | None) -> Source | None:
        if source_id is None:
            return None
        if source_id not in self._source_cache:
            self._source_cache[source_id] = self.s.get(Source, source_id)
        return self._source_cache[source_id]

    # ------------------------------------------------------------------ main entry
    def add(self, o: ObsIn) -> StoreResult:
        flags = list(o.flags)
        value = to_float(o.value)
        period = parse_date(o.period_date)
        if period is None and o.kind != "property":
            flags.append("as_of_unknown")
        iso3 = to_iso3(o.country) if o.country else None
        if o.country and not iso3:
            flags.append("country_unmapped")

        material = None
        if o.material_id:
            material = self.s.get(Material, o.material_id)
        elif o.material_name:
            material = self.resolve_material(o.material_name, o.material_type)

        src = self.source(o.source_id)
        origin = f"src:{src.slug}" if src else f"ds:{o.dataset_id}"
        series_key = "|".join([origin, o.kind, (o.series_hint or o.entity).lower().strip(), o.metric.lower(), iso3 or "-"])[:300]
        fp = sha(f"{series_key}|{period}|{round(value, 8) if value is not None else o.value}")
        if fp in self._seen_fp:
            return StoreResult("duplicate")
        if self.s.execute(select(Observation.id).where(Observation.fingerprint == fp)).first():
            self._seen_fp.add(fp)
            return StoreResult("duplicate")

        # --- normalization -------------------------------------------------------------
        unit_known = True
        value_usd = None
        unit_norm = None
        currency = (o.currency or "").upper() or None
        usd_per_kg = None
        conv_meta: dict[str, Any] = {}
        if o.kind == "price" and value is not None:
            pu = parse_price_unit(o.unit)
            currency = currency or pu.currency
            if currency is None or pu.per_unit is None:
                unit_known = False
            elif pu.kg_factor is None:
                # Non-mass price (per m², per sqft, per piece): keep it, normalized per its own unit.
                usd, fx_flags, conv_meta = self.fx.to_usd(value * pu.scale, currency, period)
                flags += fx_flags + ["non_mass_unit"]
                value_usd, unit_norm = usd, f"USD/{pu.per_unit}"
            else:
                per_kg = value * pu.scale / pu.kg_factor
                usd, fx_flags, conv_meta = self.fx.to_usd(per_kg, currency, period)
                flags += fx_flags
                usd_per_kg = usd
                value_usd, unit_norm = usd, "USD/kg"
        elif o.kind in {"labor_rate", "machine_rate"} and value is not None:
            pu = parse_price_unit(o.unit)
            currency = currency or pu.currency or "USD"
            usd, fx_flags, conv_meta = self.fx.to_usd(value, currency, period)
            flags += fx_flags
            value_usd, unit_norm = usd, "USD/hr"
        elif o.kind == "fx":
            value_usd, unit_norm = value, o.unit

        # --- history / conflicts -------------------------------------------------------
        previous = None
        conflicts: list[float] = []
        revised_of: Observation | None = None
        if self.check_history:
            same_period = self.s.execute(
                select(Observation).where(Observation.series_key == series_key, Observation.period_date == period,
                                          Observation.status != "superseded")
            ).scalars().first()
            if same_period is not None and same_period.value != value:
                revised_of = same_period
                flags.append("revision")
            prev_q = select(Observation.value).where(Observation.series_key == series_key,
                                                     Observation.status.in_(["verified", "unverified"]))
            if period:
                prev_q = prev_q.where(Observation.period_date < period)
            previous = self.s.execute(prev_q.order_by(Observation.period_date.desc()).limit(1)).scalar_one_or_none()
            if material is not None and period and value_usd is not None and o.kind == "price":
                others = self.s.execute(
                    select(Observation.value_usd).where(
                        Observation.material_id == material.id, Observation.kind == "price",
                        Observation.period_date == period, Observation.country_iso3 == iso3,
                        Observation.series_key != series_key, Observation.status == "verified")
                ).scalars().all()
                conflicts = [v for v in others if v and abs(v - value_usd) / v > 0.10]

        verified_src = (src.verification_status == "verified") if src else True
        qr = quality.assess(
            kind=o.kind, value=value, value_usd_per_kg=usd_per_kg,
            family=material.family if material else None, unit_known=unit_known,
            period_date=period, freshness_days=None,  # staleness is evaluated on the latest value at query time
            previous_value=previous, conflicting_values=conflicts, source_verified=verified_src, base_flags=flags,
        )

        obs = Observation(
            kind=o.kind, series_key=series_key, fingerprint=fp, source_id=o.source_id, dataset_id=o.dataset_id,
            material_id=material.id if material else None, category_id=self.category_id(o.category_slug),
            country_iso3=iso3, entity=o.entity[:300], metric=o.metric[:160], value=value,
            value_text=None if value is not None else (str(o.value)[:2000] if o.value is not None else None),
            unit=o.unit, currency=currency, value_usd=value_usd, unit_normalized=unit_norm, period_date=period,
            source_url=o.source_url, original={**o.original, **({"conversion": conv_meta} if conv_meta else {})},
            status=qr.status, confidence=qr.confidence, quality_flags=sorted(set(qr.flags)), job_id=o.job_id,
        )
        self.s.add(obs)
        self.s.flush()
        self._seen_fp.add(fp)
        for sev, code, msg in qr.issues:
            self.s.add(QualityIssue(severity=sev, code=code, message=msg, source_id=o.source_id,
                                    dataset_id=o.dataset_id, observation_id=obs.id, job_id=o.job_id,
                                    context={"entity": o.entity, "metric": o.metric, "value": o.value,
                                             "unit": o.unit, "url": o.source_url}))
        if revised_of is not None:
            revised_of.status = "superseded"
            return StoreResult("revised", obs.id, obs.status)
        return StoreResult("quarantined" if obs.status == "quarantined" else "inserted", obs.id, obs.status)


def dumps(o: Any) -> str:
    return json.dumps(o, default=str, sort_keys=True)
