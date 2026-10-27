"""Tag a headline with its manufacturing category, country and location, and decide whether it is manufacturing news at all.

Tags: ``cat:<taxonomy slug>``, ``country:<ISO3>`` (named in the headline, or implied by a manufacturing hub), ``loc:<place>``,
``pub:<ISO3>`` (country of the publisher, when the collector knows it). Pure rules, no model: the same headline always gets the
same tags, and every tag can be traced to the words that caused it (see ``explain``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from ..normalize.countries import COUNTRIES

TOPICS_FILE = Path(__file__).with_name("topics.yaml")


@dataclass
class Tagged:
    relevant: bool
    tags: list[str] = field(default_factory=list)
    why: dict[str, list[str]] = field(default_factory=dict)       # tag -> the words that triggered it

    @property
    def categories(self) -> list[str]:
        return [t[4:] for t in self.tags if t.startswith("cat:")]

    @property
    def countries(self) -> list[str]:
        return [t[8:] for t in self.tags if t.startswith("country:")]


def _phrase(p: str) -> re.Pattern:
    return re.compile(r"(?<![a-z0-9])" + re.escape(p.lower()).replace(r"\ ", r"[\s\-]+") + r"s?(?![a-z0-9])")


@lru_cache(maxsize=1)
def load_topics() -> dict:
    raw = yaml.safe_load(TOPICS_FILE.read_text())
    cats = {}
    for slug, c in raw["categories"].items():
        strong = [_phrase(k) for k in c["keywords"] if not k.startswith("~")]
        weak = [_phrase(k[1:]) for k in c["keywords"] if k.startswith("~")]
        cats[slug] = {"name": c["name"], "strong": strong, "weak": weak, "raw": [k for k in c["keywords"] if not k.startswith("~")]}
    names = {}
    for iso3, _iso2, name, _cur, _reg, is_region in COUNTRIES:
        if not is_region:
            names[iso3] = name
    names.update({"USA": "United States", "GBR": "United Kingdom", "KOR": "South Korea", "CZE": "Czech Republic", "NLD": "Netherlands"})
    country_re = {iso3: _phrase(n) for iso3, n in names.items()}
    extra = {"USA": [r"\bU\.S\.A?\b", r"\bUS\b", r"\bAmerican\b"], "GBR": [r"\bU\.K\.\b", r"\bUK\b", r"\bBritish\b"], "KOR": [r"\bSouth Korean\b"],
             "IND": [r"\bIndian\b"], "CHN": [r"\bChinese\b"], "JPN": [r"\bJapanese\b"], "DEU": [r"\bGerman\b"], "MEX": [r"\bMexican\b"],
             "VNM": [r"\bVietnamese\b"], "CAN": [r"\bCanadian\b"], "FRA": [r"\bFrench\b"], "ITA": [r"\bItalian\b"], "ESP": [r"\bSpanish\b"]}
    places = []
    for iso3, lst in (raw.get("places") or {}).items():
        for p in lst or []:
            places.append((iso3, p, _phrase(p)))
    return {"cats": cats, "generic": [g.lower() for g in raw["generic"]], "country_re": country_re,
            "country_cs": {k: [re.compile(x) for x in v] for k, v in extra.items()},      # case-sensitive: "US" must not match "us"
            "places": places, "countries": raw.get("countries", {})}


def classify(title: str, summary: str | None = None, *, trusted: bool = False, publisher_country: str | None = None) -> Tagged:
    """``trusted`` = the feed itself is a manufacturing publication, so every item is relevant (it is still tagged)."""
    t = load_topics()
    text = f"{title or ''} {summary or ''}".strip()
    low = text.lower()
    generic = [g for g in t["generic"] if g in low]
    out, why = [], {}
    for slug, c in t["cats"].items():
        hit = [m.group(0) for p in c["strong"] if (m := p.search(low))]
        if not hit and generic:
            hit = [m.group(0) for p in c["weak"] if (m := p.search(low))]
        if hit:
            out.append(f"cat:{slug}")
            why[f"cat:{slug}"] = hit
    countries: list[str] = []
    for iso3, rx in t["country_re"].items():
        if rx.search(low) or any(p.search(text) for p in t["country_cs"].get(iso3, [])):
            countries.append(iso3)
    for iso3, place, rx in t["places"]:
        if rx.search(low):
            out.append(f"loc:{place}")
            why[f"loc:{place}"] = [place]
            if iso3 not in countries:
                countries.append(iso3)
    for c in countries:
        out.append(f"country:{c}")
    if publisher_country:
        out.append(f"pub:{publisher_country}")
        if not countries:
            out.append(f"country:{publisher_country}")           # publisher location is the only country signal we have
    relevant = trusted or bool(out_cats := [x for x in out if x.startswith("cat:")]) or bool(generic)
    if generic and not any(x.startswith("cat:") for x in out):
        out.append("topic:manufacturing")
    return Tagged(relevant=relevant, tags=sorted(set(out)), why={**why, **({"generic": generic} if generic else {})})


def category_names() -> dict[str, str]:
    return {slug: c["name"] for slug, c in load_topics()["cats"].items()}


# Domains of manufacturing trade media are trusted even when a headline names no process ("FRECH Group sells FRECH ZPF GmbH").
_TRADE_DOMAIN = re.compile(r"foundry|casting|forg(e|ing)|machin|metalwork|fabricat|plastic|moulding|molding|manufactur|industr|automation|"
                           r"supplychain|supply-chain|tooling|welding|cnc|smt|pcb|additive|3dprint", re.I)


def trusted_domain(domain: str | None) -> bool:
    return bool(domain and _TRADE_DOMAIN.search(domain))
