"""Data-quality rules applied to every observation before it is stored.

Outcome:
* ``quarantined`` — hard failure (missing value, non-positive/implausible price, unknown unit
  for a price that must be mass-based). Stored, but never shown as verified data.
* ``unverified`` — soft issues (large change vs. history, stale, conflict, FX fallback,
  source not verified).
* ``verified`` — passed all checks and the source itself is verified.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

# Plausible USD/kg bands per family. Deliberately wide (AM powders and specialty resins are
# legitimately 200-600 USD/kg); the goal is to catch unit/scale errors (per-tonne entered as per-kg).
PRICE_BOUNDS_USD_PER_KG: dict[str, tuple[float, float]] = {
    "steel": (0.05, 400), "stainless_steel": (0.3, 600), "galvanized_steel": (0.1, 60), "tool_steel": (0.5, 600),
    "cast_iron": (0.05, 40), "aluminium": (0.3, 150), "copper": (1.0, 150), "brass_bronze": (1.0, 150),
    "zinc": (0.5, 60), "magnesium": (0.5, 150), "lead": (0.3, 40), "titanium": (2.0, 800),
    "superalloy": (3.0, 1500), "thermoplastic": (0.2, 1000), "thermoset": (0.2, 600),
    "rubber_elastomer": (0.2, 300), "composite": (0.5, 2500), "additive_polymer": (1.0, 2500),
    "pcb_laminate": (0.5, 5000), "precious_metal": (100, 1e7), "nickel": (2, 200), "tin": (2, 300), "wax": (0.2, 200), "ceramic": (0.1, 2000), "unclassified": (0.001, 100000),
}
LARGE_CHANGE_PCT = 35.0


@dataclass
class QualityResult:
    status: str
    confidence: float
    flags: list[str] = field(default_factory=list)
    issues: list[tuple[str, str, str]] = field(default_factory=list)  # (severity, code, message)


def assess(
    *,
    kind: str,
    value: float | None,
    value_usd_per_kg: float | None = None,
    family: str | None = None,
    unit_known: bool = True,
    period_date: dt.date | None = None,
    freshness_days: int | None = None,
    previous_value: float | None = None,
    conflicting_values: list[float] | None = None,
    source_verified: bool = True,
    base_flags: list[str] | None = None,
) -> QualityResult:
    flags = list(base_flags or [])
    issues: list[tuple[str, str, str]] = []
    hard = False

    if value is None:
        hard = True
        issues.append(("error", "missing_value", "Observation has no numeric value"))
    elif kind in {"price", "fx", "labor_rate", "machine_rate"} and value <= 0:
        hard = True
        issues.append(("error", "non_positive", f"{kind} must be > 0 (got {value})"))

    if kind == "price" and not unit_known:
        hard = True
        issues.append(("error", "unit_unknown", "Price unit could not be normalized"))

    if kind == "price" and value_usd_per_kg is not None:
        lo, hi = PRICE_BOUNDS_USD_PER_KG.get(family or "unclassified", PRICE_BOUNDS_USD_PER_KG["unclassified"])
        if not (lo <= value_usd_per_kg <= hi):
            hard = True
            issues.append(("error", "implausible_price",
                           f"{value_usd_per_kg:.4g} USD/kg outside plausible band {lo}-{hi} for {family}"))

    if previous_value and value and previous_value > 0:
        chg = abs(value - previous_value) / previous_value * 100
        if chg >= LARGE_CHANGE_PCT:
            flags.append("large_change")
            issues.append(("warning", "large_change", f"{chg:.1f}% change vs previous observation"))

    if conflicting_values:
        flags.append("conflict")
        issues.append(("warning", "conflict", f"Other sources report {conflicting_values[:3]} for the same period"))

    if period_date and freshness_days is not None:
        age = (dt.date.today() - period_date).days
        if age > freshness_days:
            flags.append("stale")

    if not source_verified:
        flags.append("source_unverified")

    if any(f.startswith("fx_fallback") or f in {"fx_rate_missing", "as_of_unknown"} for f in flags):
        pass  # keep as soft flags

    if hard:
        return QualityResult("quarantined", 0.1, flags + [code for sev, code, _msg in issues if sev == "error"], issues)

    soft = [f for f in flags if f not in {"stale"}]
    confidence = 0.95 if source_verified else 0.6
    confidence -= 0.15 * len([f for f in soft if f in {"large_change", "conflict"}])
    confidence -= 0.1 * len([f for f in flags if f.startswith("fx_")])
    confidence -= 0.1 if "stale" in flags else 0
    confidence -= 0.1 if "as_of_unknown" in flags else 0
    status = "verified" if source_verified and not {"large_change", "conflict"} & set(flags) else "unverified"
    return QualityResult(status, round(max(0.05, min(confidence, 0.99)), 2), flags, issues)
