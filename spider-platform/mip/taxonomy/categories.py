"""Canonical mapping for the existing taxonomy folders.

Original folder names are preserved verbatim (``original_folder``) — including their
spelling ("Multi-Spindle Maching", "Stock Maching", "Plastic Modeling") — and mapped to a
normalized slug/name plus the matching node in ``digital_factory_tree.csv``.
"""
from __future__ import annotations

# original folder -> (slug, canonical name, digital_factory_tree node, family)
FOLDER_MAP: dict[str, tuple[str, str, str | None, str]] = {
    "2 Mold Molding": ("two-mold-molding", "2-Mold Molding", "2-Model Machining", "plastics"),
    "Additive Manufacturing": ("additive-manufacturing", "Additive Manufacturing", "Additive Manufacturing", "additive"),
    "Rapid prototyping": ("rapid-prototyping", "Rapid Prototyping", "Rapid Prototyping", "additive"),
    "Assembly": ("assembly", "Assembly", "Assembly", "assembly"),
    "Assembly molding": ("assembly-molding", "Assembly Molding", "Assembly Molding", "assembly"),
    "Assembly plastic molding": ("assembly-plastic-molding", "Assembly Plastic Molding", "Assembly Plastic Molding", "assembly"),
    "Part Assembly": ("part-assembly", "Part Assembly", "Part Assembly", "assembly"),
    "Bar and tube fab": ("bar-tube-fabrication", "Bar & Tube Fabrication", "Bar & Tube Fab", "forming"),
    "Casting": ("casting", "Casting", "Casting", "casting"),
    "Sand casting": ("sand-casting", "Sand Casting", "Casting - Sand", "casting"),
    "Die Casting": ("die-casting", "Die Casting", "Casting - Die", "casting"),
    "Casting Investment": ("investment-casting", "Investment Casting", "Casting - Investment", "casting"),
    "Forging": ("forging", "Forging", "Forging", "forming"),
    "Composites": ("composites", "Composites", "Composites", "composites"),
    "Powder metal": ("powder-metal", "Powder Metal", "Powder Metal", "powder"),
    "Plastic Modeling": ("plastic-molding", "Plastic Molding", "Plastic Molding", "plastics"),
    "Sheet Plastic": ("sheet-plastic", "Sheet Plastic (Thermoforming)", "Sheet Plastic", "plastics"),
    "Roto & blow molding": ("roto-blow-molding", "Roto & Blow Molding", "Roto & Blow Molding", "plastics"),
    "Electronics": ("electronics", "Electronics", None, "electronics"),
    "PCBA": ("pcba", "PCBA", "PCB", "electronics"),
    "Machining": ("machining", "Machining", "Machining", "machining"),
    "Stock Maching": ("stock-machining", "Stock Machining", "Stock Machining", "machining"),
    "Multi-Spindle Maching": ("multi-spindle-machining", "Multi-Spindle Machining", "Multi-Spindle Machining", "machining"),
    "Heat treatment": ("heat-treatment", "Heat Treatment", "Heat Treatment", "finishing"),
    "SurfaceTreatment": ("surface-treatment", "Surface Treatment", "Surface Treatment", "finishing"),
    "Secondary process": ("secondary-process", "Secondary Process", "Other Secondary Processes", "finishing"),
    "Sheetmetal": ("sheet-metal", "Sheet Metal", "Sheet Metal", "sheet_metal"),
    "Sheet metal roll forming": ("sheet-metal-roll-forming", "Sheet Metal Roll Forming", "Sheet Metal - Roll Forming", "sheet_metal"),
    "Sheet metal stretch forming": ("sheet-metal-stretch-forming", "Sheet Metal Stretch Forming", "Sheet Metal - Stretch Forming", "sheet_metal"),
    "Sheet metal transfer die": ("sheet-metal-transfer-die", "Sheet Metal Transfer Die", "Sheet Metal - Transfer Die", "sheet_metal"),
    "Sheetmetal Hydroforming": ("sheet-metal-hydroforming", "Sheet Metal Hydroforming", "Sheet Metal - Hydroforming", "sheet_metal"),
    "Standards": ("standards", "Standards", None, "reference"),
    "Countries data": ("countries-data", "Countries Data", None, "reference"),
    "database": ("database", "Database", None, "reference"),
}

PARENTS: dict[str, str] = {
    "sand-casting": "casting", "die-casting": "casting", "investment-casting": "casting",
    "sheet-metal-roll-forming": "sheet-metal", "sheet-metal-stretch-forming": "sheet-metal",
    "sheet-metal-transfer-die": "sheet-metal", "sheet-metal-hydroforming": "sheet-metal",
    "stock-machining": "machining", "multi-spindle-machining": "machining",
    "assembly-molding": "assembly", "assembly-plastic-molding": "assembly", "part-assembly": "assembly",
    "pcba": "electronics", "rapid-prototyping": "additive-manufacturing",
}

FAMILY_LABELS = {
    "casting": "Casting", "forming": "Forming & Fabrication", "sheet_metal": "Sheet Metal", "machining": "Machining",
    "plastics": "Plastics & Molding", "additive": "Additive & Prototyping", "assembly": "Assembly",
    "composites": "Composites", "powder": "Powder Metallurgy", "electronics": "Electronics",
    "finishing": "Heat, Surface & Secondary", "reference": "Reference Data",
}


def map_folder(folder: str) -> tuple[str, str, str | None, str]:
    if folder in FOLDER_MAP:
        return FOLDER_MAP[folder]
    slug = "".join(c if c.isalnum() else "-" for c in folder.lower()).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug, folder, None, "other"


def classify_role(rel_path: str, columns: list[str]) -> str:
    """Infer a dataset's role from its location and columns (folder names vary in spelling)."""
    p = rel_path.lower()
    parts = p.split("/")
    name = parts[-1]
    cols = {c.strip().lower() for c in columns}
    if "wage_grade" in name or "wagegrade" in name:
        return "wage_grades"
    if parts[0] == "countries data":
        return "country_machine_rates"
    if parts[0] == "standards":
        return "standards"
    if parts[0] == "database":
        return "database"
    if any(k in name for k in ("variable", "settings", "all_plant")):
        return "variables"
    if any(seg.startswith(("lookup",)) for seg in parts[1:-1]) or name.startswith(("tbl", "lookup")):
        return "lookup"
    if any(seg.startswith(("raw material", "raw materials", "rawmetrial", "materials", "metirials", "material"))
           for seg in parts[1:-1]) or ("material" in name and "stock" not in name and "handling" not in name):
        if cols & {"name", "material name", "material", "primaryid", "primaryid", "material_name"}:
            return "materials"
    if "raw_material" in name or "material_stock_cost" in name or "stock_costs" in name:
        return "materials"
    if any(seg.startswith(("machine", "machines")) for seg in parts[1:-1]) or name.startswith("machine") \
            or (name.endswith(("_machines.csv", "machines.csv")) and not name.startswith("process")):
        return "machines"
    if "operation" in name:
        return "operations"
    if "process" in name:
        return "processes"
    if any(c.startswith("labor rate") for c in cols):
        return "machines"
    return "reference"
