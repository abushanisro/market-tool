"""Country / region normalization (names as they appear in the existing files and APIs)."""
from __future__ import annotations

import re

# iso3, iso2, name, currency, region, is_region
COUNTRIES: list[tuple[str, str | None, str, str | None, str, bool]] = [
    ("USA", "US", "United States", "USD", "North America", False),
    ("CAN", "CA", "Canada", "CAD", "North America", False),
    ("MEX", "MX", "Mexico", "MXN", "North America", False),
    ("BRA", "BR", "Brazil", "BRL", "South America", False),
    ("IND", "IN", "India", "INR", "South Asia", False),
    ("CHN", "CN", "China", "CNY", "East Asia", False),
    ("JPN", "JP", "Japan", "JPY", "East Asia", False),
    ("KOR", "KR", "South Korea", "KRW", "East Asia", False),
    ("TWN", "TW", "Taiwan", "TWD", "East Asia", False),
    ("HKG", "HK", "Hong Kong", "HKD", "East Asia", False),
    ("VNM", "VN", "Vietnam", "VND", "Southeast Asia", False),
    ("MYS", "MY", "Malaysia", "MYR", "Southeast Asia", False),
    ("SGP", "SG", "Singapore", "SGD", "Southeast Asia", False),
    ("THA", "TH", "Thailand", "THB", "Southeast Asia", False),
    ("IDN", "ID", "Indonesia", "IDR", "Southeast Asia", False),
    ("DEU", "DE", "Germany", "EUR", "Western Europe", False),
    ("FRA", "FR", "France", "EUR", "Western Europe", False),
    ("NLD", "NL", "Netherlands", "EUR", "Western Europe", False),
    ("BEL", "BE", "Belgium", "EUR", "Western Europe", False),
    ("ITA", "IT", "Italy", "EUR", "Western Europe", False),
    ("ESP", "ES", "Spain", "EUR", "Western Europe", False),
    ("GBR", "GB", "United Kingdom", "GBP", "Western Europe", False),
    ("CZE", "CZ", "Czech Republic", "CZK", "Eastern Europe", False),
    ("HUN", "HU", "Hungary", "HUF", "Eastern Europe", False),
    ("POL", "PL", "Poland", "PLN", "Eastern Europe", False),
    ("ROU", "RO", "Romania", "RON", "Eastern Europe", False),
    ("TUR", "TR", "Turkey", "TRY", "Europe/Asia", False),
    ("ZAF", "ZA", "South Africa", "ZAR", "Africa", False),
    ("AUS", "AU", "Australia", "AUD", "Oceania", False),
    # Regions used in the existing files (not countries) — use X-prefixed pseudo codes.
    ("XWE", None, "Western Europe (region)", "EUR", "Western Europe", True),
    ("XEE", None, "Eastern Europe (region)", "EUR", "Eastern Europe", True),
    ("WLD", None, "World", None, "World", True),
]

_ALIASES = {
    "us": "USA", "usa": "USA", "u s a": "USA", "united states": "USA", "united states of america": "USA",
    "america": "USA", "canada": "CAN", "mexico": "MEX", "brazil": "BRA",
    "india": "IND", "indian": "IND", "china": "CHN", "china data": "CHN", "prc": "CHN",
    "japan": "JPN", "south korea": "KOR", "korea": "KOR", "korea rep": "KOR", "korea republic of": "KOR",
    "taiwan": "TWN", "hong kong": "HKG", "vietnam": "VNM", "viet nam": "VNM", "malaysia": "MYS",
    "singapore": "SGP", "thailand": "THA", "indonesia": "IDN",
    "germany": "DEU", "france": "FRA", "netherlands": "NLD", "the netherlands": "NLD", "belgium": "BEL",
    "italy": "ITA", "spain": "ESP", "uk": "GBR", "united kingdom": "GBR", "great britain": "GBR",
    "czech republic": "CZE", "czechia": "CZE", "hungary": "HUN", "poland": "POL", "romania": "ROU",
    "turkey": "TUR", "turkiye": "TUR", "south africa": "ZAF", "australia": "AUS",
    "w europe": "XWE", "western europe": "XWE", "w. europe": "XWE", "eastern europe": "XEE", "e europe": "XEE",
    "world": "WLD",
}
_ISO2 = {c[1]: c[0] for c in COUNTRIES if c[1]}
_ISO3 = {c[0] for c in COUNTRIES}
CURRENCY_BY_ISO3 = {c[0]: c[3] for c in COUNTRIES}
NAME_BY_ISO3 = {c[0]: c[2] for c in COUNTRIES}


def _clean(s: str) -> str:
    s = s.lower().replace("_", " ").replace("-", " ")
    s = re.sub(r"\b(xml|data|cost|usd|\(usd\))\b", " ", s)
    s = re.sub(r"[^a-z. ]", " ", s)
    return re.sub(r"\s+", " ", s).strip(" .")


def to_iso3(value: str | None) -> str | None:
    """Map a country label/ISO code to ISO3. Returns None when unknown (never guesses)."""
    if not value:
        return None
    v = value.strip()
    if v.upper() in _ISO3:
        return v.upper()
    if v.upper() in _ISO2:
        return _ISO2[v.upper()]
    c = _clean(v)
    if c in _ALIASES:
        return _ALIASES[c]
    c2 = c.replace(".", "")
    return _ALIASES.get(c2)
