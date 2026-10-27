"""Material name / family / grade normalization.

Rules are deterministic and conservative: if nothing matches, family = "unclassified".
The original name is always preserved alongside the normalized key.
"""
from __future__ import annotations

import re

# (family, display, regex on "material_type + name") — first match wins, order matters.
FAMILY_RULES: list[tuple[str, str, str]] = [
    ("stainless_steel", "Stainless steel", r"stainless|\b(30[1-9]|31[0-9]|316l?|304l?|410|420|430|440[abc]?|17-4\s?ph|15-5\s?ph)\b(?!.*alumin)"),
    ("galvanized_steel", "Galvanized / coated steel", r"galv|\bdx5\d|zinc[- ]coated|\bgi\b"),
    ("cast_iron", "Cast iron", r"cast iron|ductile|gr[ae]y iron|malleable|\bcgi\b|\bjs \d|astm a48|astm a536|\bgjl\b|\bgjs\b"),
    ("superalloy", "Nickel / cobalt superalloy", r"super ?alloy|inconel|hastelloy|waspaloy|nickel alloy|cobalt|stellite|\bin ?718\b|\bin ?625\b|monel"),
    ("precious_metal", "Precious metal", r"\bgold\b|\bsilver\b|platinum|palladium"),
    ("nickel", "Nickel", r"^\s*\|?\s*nickel\b|\bnickel \(|\bnickel$"),
    ("tin", "Tin", r"\btin\b"),
    ("titanium", "Titanium", r"titanium|\bti-?6al|\bti ?gr"),
    ("zinc", "Zinc", r"\bzinc\b|zamak|\bza-?\d"),
    ("aluminium", "Aluminium", r"alumin(i)?um(?! bronze)|\baa ?\d{4}|\ba356|\ba380|\badc ?12|\ben ?aw"),
    ("magnesium", "Magnesium", r"magnesium|\baz91|\bam60"),
    ("brass_bronze", "Brass & bronze", r"brass|bronze|\bc3[0-9]{4}|gunmetal"),
    ("copper", "Copper", r"copper|\bc1[0-9]{4}|\bcu-?etp|\bofhc"),
    ("lead", "Lead", r"\blead\b"),
    ("tool_steel", "Tool steel", r"tool steel|\bh13\b|\bd2\b|\bp20\b|\bm2\b|maraging|high speed steel|\bhss\b"),
    ("steel", "Carbon & alloy steel", r"steel|\baisi ?\d{4}|\bsae ?\d{4}|\bastm a(36|27|148|216|352)\b|\bs235|\bs355|\bc45\b|\bdc0\d|\ben ?8\b"),
    ("pcb_laminate", "PCB laminate", r"fr-?4|laminate|\bcem-?\d|rogers|polyimide flex|\d+ply"),
    ("composite", "Composite", r"composite|prepreg|carbon fib|glass fib|woven|honeycomb|vinyl ester|epoxy.*(cf|gf)|\bcf\d|\bgf\d"),
    ("rubber_elastomer", "Rubber & elastomer", r"rubber|elastomer|silicone|\bepdm\b|\bnbr\b|\btp[eouv]\b|\btps\b|\btpa\b|\btpo\b|neoprene"),
    ("thermoset", "Thermoset", r"thermoset|phenolic|unsaturated polyester|\bbmc\b|\bsmc\b|melamine|\bepoxy\b|polyurethane"),
    ("additive_polymer", "AM photopolymer / powder", r"accura|visijet|duraform|vero|tango|protogen|castform|laserform|waterclear|prototherm|v-flash|objet"),
    ("wax", "Wax", r"\bwax\b"),
    ("thermoplastic", "Thermoplastic", r"\babs\b|nylon|polyamide|\bpa ?\d|polypropylene|\bpp\b|polyethylene|\bhdpe|\bldpe|\bpe\b|\bpet\b|\bpbt\b|polycarbonate|\bpc\b|polystyrene|\bps\b|acetal|\bpom\b|acrylic|pmma|\bpps\b|\bpvc\b|\bsan\b|\bppo\b|\bpes\b|\bpei\b|peek|polyaryletherketone|thermoplastic|\bpla\b|polyetheramide"),
    ("ceramic", "Ceramic", r"ceramic|alumina|zirconia|silica|\bsic\b"),
]
_COMPILED = [(f, d, re.compile(rx, re.I)) for f, d, rx in FAMILY_RULES]
FAMILY_DISPLAY = {f: d for f, d, _ in FAMILY_RULES} | {"unclassified": "Unclassified"}

METAL_FAMILIES = {
    "precious_metal", "nickel", "tin", "stainless_steel", "galvanized_steel", "cast_iron", "superalloy", "titanium", "aluminium", "magnesium",
    "zinc", "brass_bronze", "copper", "lead", "tool_steel", "steel",
}
POLYMER_FAMILIES = {"thermoplastic", "thermoset", "rubber_elastomer", "additive_polymer", "wax"}

GRADE_PATTERNS = [
    r"\b(?:AISI|SAE|ANSI|AA|UNS|ASTM|EN|DIN|JIS|GB|IS|BS)\s?[A-Z]?\s?[\w.\-/]+(?:\s?(?:Grade|Gr\.?)\s?[\w\-]+)?",
    r"\b\d{1,2}-\d{1,2}\s?PH\b",
    r"\bTi-6Al-4V\b",
    r"\b(?:ZA|AZ|AM|ADC|A)\s?-?\d{2,3}\b",
    r"\bZamak\s?\d\b",
    r"\b[1-8]\d{3}(?:-[TOH]\d+)?\b",
]


def classify_family(name: str | None, material_type: str | None = None) -> str:
    text = f"{material_type or ''} | {name or ''}"
    for fam, _, rx in _COMPILED:
        if rx.search(text):
            return fam
    return "unclassified"


def extract_grade(name: str | None) -> str | None:
    if not name:
        return None
    for p in GRADE_PATTERNS:
        m = re.search(p, name, re.I)
        if m:
            return m.group(0).strip(" ,")
    return None


def material_key(name: str) -> str:
    """Identity key: case/space/punctuation-insensitive; keeps grade digits intact."""
    k = name.lower().replace("aluminium", "aluminum").replace("galv.", "galvanized")
    k = re.sub(r"[^a-z0-9]+", " ", k)
    return re.sub(r"\s+", " ", k).strip()


# property header -> (normalized key, unit)
PROPERTY_ALIASES: dict[str, tuple[str, str]] = {
    "density": ("density", "kg/m3"), "density_kg_m3": ("density", "kg/m3"), "densitykgm3": ("density", "kg/m3"),
    "combined density": ("density", "kg/m3"),
    "uts": ("uts", "MPa"), "uts_mpa": ("uts", "MPa"), "ultimate tensile strength": ("uts", "MPa"),
    "tensile strength": ("uts", "MPa"),
    "tys": ("yield_strength", "MPa"), "tys_mpa": ("yield_strength", "MPa"), "yield strength": ("yield_strength", "MPa"),
    "tensile yield strength": ("yield_strength", "MPa"),
    "shear strength": ("shear_strength", "MPa"), "shearstrength_mpa": ("shear_strength", "MPa"),
    "youngs modulus": ("youngs_modulus", "MPa"), "youngsmodulus_mpa": ("youngs_modulus", "MPa"),
    "poissons ratio": ("poissons_ratio", "-"), "poissonsratio": ("poissons_ratio", "-"),
    "hardness": ("hardness", "as-reported"),
    "hardness system": ("hardness_system", "-"), "hardnesssystem": ("hardness_system", "-"),
    "thermal conductivity": ("thermal_conductivity", "W/m·K"),
    "melting temp": ("melting_temp", "°C"), "liquidus temp": ("liquidus_temp", "°C"),
    "solidus temp": ("solidus_temp", "°C"), "pour temp": ("pour_temp", "°C"),
    "mold temp": ("mold_temp", "°C"), "eject deflection temp": ("eject_deflection_temp", "°C"),
    "heat of fusion": ("heat_of_fusion", "J/kg"), "specific heat rt": ("specific_heat", "J/kg·K"),
    "specific heat at room temperature": ("specific_heat", "J/kg·K"), "specific heat capacity": ("specific_heat", "J/kg·K"),
    "machinability index": ("machinability_index", "-"), "machinabilityindex": ("machinability_index", "-"),
    "milling speed": ("milling_speed", "m/min"), "forging temperature": ("forging_temp", "°C"),
    "scrap cost percent": ("scrap_cost_pct", "%"), "scrappct": ("scrap_cost_pct", "%"),
    "clamping pressure": ("clamping_pressure", "MPa"),
}


def normalize_property_header(header: str) -> tuple[str, str] | None:
    h = re.sub(r"\(.*?\)", "", header).strip().lower()
    h = h.replace("'", "").replace("’", "")
    h = re.sub(r"\s+", " ", h)
    if h in PROPERTY_ALIASES:
        return PROPERTY_ALIASES[h]
    h2 = h.replace(" ", "").replace("_", "")
    for k, v in PROPERTY_ALIASES.items():
        if k.replace(" ", "").replace("_", "") == h2:
            return v
    return None
