import datetime as dt

from mip.normalize.countries import to_iso3
from mip.normalize.materials import classify_family, extract_grade, material_key, normalize_property_header
from mip.normalize.quality import assess
from mip.normalize.units import parse_price_unit, to_float, unit_from_header
from mip.store import parse_date


def test_units():
    assert unit_from_header("Unit Cost (USD / kg)") == "USD / kg"
    assert unit_from_header("Unit Cost (USD/kg) [ID]") == "USD/kg"
    u = parse_price_unit("($/mt)")
    assert u.currency == "USD" and u.kg_factor == 1000
    assert parse_price_unit("cents/kg").scale == 0.01
    assert parse_price_unit("USD/m2").kg_factor is None
    assert parse_price_unit("INR per tonne").currency == "INR"
    assert to_float("1,234.5") == 1234.5 and to_float("n/a") is None and to_float("") is None


def test_countries_never_guess():
    assert to_iso3("India") == "IND" and to_iso3("Viet Nam") == "VNM" and to_iso3("W. Europe") == "XWE"
    assert to_iso3("China_Data_XML_Data_") == "CHN"
    assert to_iso3("Atlantis") is None


def test_material_families():
    assert classify_family("Zinc-Aluminum ZA12", "Zinc-Aluminum") == "zinc"
    assert classify_family("Aluminum Bronze C954") == "brass_bronze"
    assert classify_family("Stainless Steel, AISI 304") == "stainless_steel"
    assert classify_family("Steel, Cold Worked, AISI 4130") == "steel"
    assert classify_family("2Ply-1ozCu-FR4") == "pcb_laminate"
    assert classify_family("Something unknown") == "unclassified"
    assert extract_grade("Titanium, Ti-6Al-4V") == "Ti-6Al-4V"
    assert material_key("Aluminium, ANSI 6061") == material_key("aluminum ansi 6061")
    assert normalize_property_header("UTS (MPa)") == ("uts", "MPa")


def test_dates():
    assert parse_date("2026M09") == dt.date(2026, 9, 1)
    assert parse_date("2025") == dt.date(2025, 12, 31)
    assert parse_date("2026-10-27") == dt.date(2026, 10, 27)
    assert parse_date("garbage") is None


def test_quality_rules():
    ok = assess(kind="price", value=3.0, value_usd_per_kg=3.0, family="aluminium")
    assert ok.status == "verified"
    bad = assess(kind="price", value=3000, value_usd_per_kg=3000, family="aluminium")
    assert bad.status == "quarantined" and "implausible_price" in bad.flags
    assert assess(kind="price", value=None).status == "quarantined"
    assert assess(kind="price", value=5, unit_known=False).status == "quarantined"
    jump = assess(kind="fx", value=100, previous_value=60)
    assert jump.status == "unverified" and "large_change" in jump.flags
    assert assess(kind="fx", value=1, source_verified=False).status == "unverified"
