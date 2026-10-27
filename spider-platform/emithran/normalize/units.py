"""Unit parsing and conversion for prices and physical quantities."""
from __future__ import annotations

import re
from dataclasses import dataclass

# mass units -> kg
MASS_TO_KG = {
    "kg": 1.0, "kgs": 1.0, "kilogram": 1.0, "g": 0.001, "gram": 0.001,
    "t": 1000.0, "mt": 1000.0, "tonne": 1000.0, "metric ton": 1000.0, "ton": 1000.0,
    "dmt": 1000.0, "dmtu": 10.0,  # dry metric ton unit (iron ore) = 1% Fe of a dmt → 10 kg (flag-only)
    "lb": 0.45359237, "lbs": 0.45359237, "pound": 0.45359237,
    "oz": 0.028349523125, "troy oz": 0.0311034768, "toz": 0.0311034768,
    "short ton": 907.18474, "long ton": 1016.0469088,
}

CURRENCY_SYMBOLS = {"$": "USD", "₹": "INR", "€": "EUR", "£": "GBP", "¥": "JPY", "₩": "KRW"}
KNOWN_CURRENCIES = {
    "USD", "EUR", "INR", "CNY", "JPY", "GBP", "MXN", "CAD", "BRL", "KRW", "TWD", "HKD", "CZK", "HUF",
    "PLN", "RON", "VND", "MYR", "SGD", "THB", "IDR", "TRY", "ZAR", "AUD", "CHF", "SEK", "NOK", "DKK",
}


@dataclass(frozen=True)
class PriceUnit:
    currency: str | None
    per_unit: str | None  # original per-unit token
    kg_factor: float | None  # kg per one "per_unit"
    scale: float = 1.0  # e.g. cents -> 0.01

    @property
    def is_mass(self) -> bool:
        return self.kg_factor is not None


def parse_price_unit(text: str | None) -> PriceUnit:
    """Parse strings such as 'USD / kg', '($/mt)', 'USD/hr', 'cents/kg', 'INR per tonne'."""
    if not text:
        return PriceUnit(None, None, None)
    t = text.strip().strip("()").replace("per", "/")
    scale = 1.0
    if re.search(r"\bcents?\b|¢", t, re.I):
        scale, cur = 0.01, "USD"
    else:
        cur = None
        for sym, code in CURRENCY_SYMBOLS.items():
            if sym in t:
                cur = code
        m = re.search(r"\b([A-Z]{3})\b", t.upper())
        if m and m.group(1) in KNOWN_CURRENCIES:
            cur = m.group(1)
    per = None
    if "/" in t:
        per = t.split("/", 1)[1].strip().strip(")").lower()
        per = re.sub(r"\s+", " ", per)
    kg = MASS_TO_KG.get(per) if per else None
    return PriceUnit(cur, per, kg, scale)


def unit_from_header(header: str) -> str | None:
    """'Unit Cost (USD / kg)' -> 'USD / kg'; 'Labor Rate (USD/hr)' -> 'USD/hr'."""
    m = re.search(r"\(([^()]*)\)\s*(\[[^\]]*\])?\s*$", header.strip())
    return m.group(1).strip() if m else None


def to_float(value) -> float | None:
    """Robust numeric parse that keeps None for blanks/garbage (never invents values)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value == value else None  # NaN guard
    s = str(value).strip()
    if not s or s.lower() in {"na", "n/a", "nan", "none", "null", "-", "--", "—"}:
        return None
    s = s.replace(",", "").replace("$", "").replace("₹", "").replace("€", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def normalize_price_per_kg(value: float, unit: PriceUnit) -> float | None:
    """Return price per kg in the unit's currency, or None when the unit is not mass-based."""
    if unit.kg_factor is None or unit.kg_factor == 0:
        return None
    return value * unit.scale / unit.kg_factor
