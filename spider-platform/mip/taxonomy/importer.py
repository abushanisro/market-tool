"""Import the existing manufacturing taxonomy (`memory/` folder) into the database.

* Every file is registered as a ``Dataset`` (schema discovered from the file itself) and every
  row is preserved verbatim in ``DatasetRow``.
* Role-specific normalizers then derive Materials, Material↔Category links, Machines,
  Processes, price/FX observations and Standards — always keeping the original values.
* MHR / LHR are recorded as *unresolved identifiers* with evidence; nothing is inferred.

Idempotent: a file whose checksum has not changed is skipped (``--force`` re-imports).
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import (
    Category, Country, Dataset, DatasetRow, Machine, Material, MaterialProcess, Observation, Process,
    QualityIssue, Standard, TreeNode, UnresolvedIdentifier,
)
from ..normalize.countries import COUNTRIES, to_iso3
from ..normalize.materials import classify_family, extract_grade, material_key, normalize_property_header
from ..normalize.units import to_float, unit_from_header
from ..store import ObsIn, ObservationStore
from .categories import FOLDER_MAP, PARENTS, classify_role, map_folder

log = logging.getLogger("mip.importer")
csv.field_size_limit(10_000_000)

UNRESOLVED_CODES = ("MHR", "LHR")
TEXT_EXT = {".csv", ".json", ".md", ".txt"}
DOC_EXT = {".pdf", ".docx", ".png", ".jpg", ".jpeg"}

NAME_COLS = ["Name", "name", "Material Name", "material_name", "Material", "PrimaryID", "primaryId", "Primary ID (Name)"]
TYPE_COLS = ["Material Type", "Material Type Name", "materialType", "material_type", "Material Family", "Raw Material Type"]
DESIG_COLS = {"USA Name": "usa", "DIN Name": "din", "EN Name": "en", "GB Name": "gb", "JIS Name": "jis",
              "European Name": "european", "usaName": "usa", "europeanName": "european", "Source Name": "source_name",
              "sourceName": "source_name", "OtherID": "other_id"}
PRICE_RX = re.compile(r"(unit ?cost|cost per kg|usd_per_kg|unitcost_usd_kg|unitcostusdperkg|materialcostusdperkg|"
                      r"unit_cost_usd_per_kg|_usd_per_kg$)", re.I)
MACHINE_NAME_COLS = ["Name", "name", "Machine Name", "machineName", "machine_name", "Primary ID (Name)",
                     "Primary ID / Name", "entry.key"]
LABOR_RX = re.compile(r"^(labor rate|labor_rate|skill based labor rate|customworkcenterlaborrate|accounting\.labor_rate)", re.I)
DIRECT_RX = re.compile(r"^(direct overhead|direct_overhead|customworkcenterdirectoverheadrate)", re.I)
INDIRECT_RX = re.compile(r"^(indirect overhead|indirect_overhead|customworkcenterindirectoverheadrate)", re.I)
PROCESS_COLS = ["Process Name", "process_name", "Process", "processName", "Name", "name"]

# aPriori digital factory release recorded in digital_factory_tree.csv root ("aP2025R1_SP03_F01_(2026-03)")
RELEASE_RX = re.compile(r"\((\d{4})-(\d{2})\)\)?$")


def checksum(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def first_col(cols: Iterable[str], candidates: list[str]) -> str | None:
    s = {c.strip(): c for c in cols}
    for c in candidates:
        if c in s:
            return s[c]
    return None


def path_country(rel: str, row: dict[str, Any] | None = None) -> tuple[str | None, str]:
    """Country from explicit columns or folder/file name; otherwise the aPriori USA digital factory."""
    if row:
        for k in ("Region", "region", "location", "Location", "Manufacturer Location", "s", "Country", "country"):
            if row.get(k):
                iso = to_iso3(str(row[k]))
                if iso:
                    return iso, "column"
    low = rel.lower()
    for token, iso in (("india", "IND"), ("mexico", "MEX"), ("germany", "DEU"), ("china", "CHN"), ("usa", "USA")):
        if re.search(rf"(^|[/_ \-]){token}([/_ \-.]|$)", low) or f"{token} data" in low:
            return iso, "path"
    return "USA", "digital_factory_default"


class TaxonomyImporter:
    def __init__(self, session: Session, root: Path, *, force: bool = False):
        self.s = session
        self.root = Path(root)
        self.force = force
        self.store = ObservationStore(session, check_history=False)
        self.categories: dict[str, Category] = {}
        self.release_date: dt.date | None = None
        self.stats: dict[str, int] = defaultdict(int)
        self.evidence: dict[str, list[dict[str, str]]] = defaultdict(list)
        self.occurrences: dict[str, int] = defaultdict(int)

    # ================================================================== public
    def run(self) -> dict[str, int]:
        if not self.root.exists():
            raise FileNotFoundError(f"Taxonomy root not found: {self.root}")
        self._seed_countries()
        self._seed_categories()
        self._import_tree()
        self._import_exchange_rates()
        files = sorted(p for p in self.root.rglob("*") if p.is_file() and not p.name.startswith((".", "~$")))
        for path in files:
            try:
                self._import_file(path)
            except Exception as exc:  # isolate failures per file
                log.exception("import failed for %s", path)
                self.stats["files_failed"] += 1
                self.s.add(QualityIssue(severity="error", code="import_failed", message=str(exc)[:2000],
                                        context={"file": str(path.relative_to(self.root))}))
            self.s.flush()
        self._finalize_identifiers()
        self._update_category_counts()
        self.s.flush()
        return dict(self.stats)

    # ================================================================== seeds
    def _seed_countries(self) -> None:
        existing = {c for (c,) in self.s.execute(select(Country.iso3))}
        for iso3, iso2, name, cur, region, is_region in COUNTRIES:
            if iso3 not in existing:
                self.s.add(Country(iso3=iso3, iso2=iso2, name=name, currency=cur, region=region, is_region=is_region))

    def _seed_categories(self) -> None:
        folders = sorted(p.name for p in self.root.iterdir() if p.is_dir())
        for folder in sorted(set(folders) | set(FOLDER_MAP)):
            slug, name, node, family = map_folder(folder)
            cat = self.s.execute(select(Category).where(Category.slug == slug)).scalar_one_or_none()
            if cat is None:
                cat = Category(slug=slug, name=name, family=family)
                self.s.add(cat)
            cat.original_folder = folder if folder in folders else cat.original_folder
            cat.tree_node_name = node
            self.categories[slug] = cat
        self.s.flush()
        for child, parent in PARENTS.items():
            if child in self.categories and parent in self.categories:
                self.categories[child].parent_id = self.categories[parent].id

    def _import_tree(self) -> None:
        p = self.root / "digital_factory_tree.csv"
        if not p.exists():
            return
        self.s.execute(delete(TreeNode))
        by_node = {c.tree_node_name: c.id for c in self.categories.values() if c.tree_node_name}
        with p.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                name = row["Name"].strip()
                if row["Level"].strip() == "0":
                    m = RELEASE_RX.search(name)
                    if m:
                        self.release_date = dt.date(int(m.group(1)), int(m.group(2)), 1)
                cat_id = by_node.get(name) if row["Node Type"] == "Folder" and row["Level"].strip() == "1" else \
                    by_node.get(row["Parent"].strip())
                self.s.add(TreeNode(level=int(row["Level"]), parent_name=row["Parent"] or None, name=name,
                                    node_type=row["Node Type"], category_id=cat_id))
        self.stats["tree_nodes"] += 1

    def _import_exchange_rates(self) -> None:
        p = self.root / "exchange_rate_table.csv"
        if not p.exists():
            return
        ds = self._register(p, "fx_table", None)
        if ds is None:
            return
        with p.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                when = None
                try:
                    when = dt.datetime.strptime(row["lastModifiedTime"].strip(), "%m/%d/%y %I:%M %p").date()
                except (ValueError, KeyError):
                    pass
                r = self.store.add(ObsIn(
                    kind="fx", entity=f"{row['baseCurrency']}/{row['currencyCode']}", metric="exchange_rate",
                    value=row["rate"], unit=f"{row['currencyCode']} per {row['baseCurrency']}",
                    currency=row["currencyCode"], period_date=when, dataset_id=ds.id, original=row,
                    flags=["fx_internal_table"] + (["fx_table_inactive"] if row.get("active") == "False" else []),
                ))
                self.stats[f"fx_{r.outcome}"] += 1

    # ================================================================== files
    def _register(self, path: Path, role: str, category: Category | None, columns: list[str] | None = None,
                  rel_suffix: str = "") -> Dataset | None:
        rel = str(path.relative_to(self.root)) + rel_suffix
        cs = checksum(path)
        ds = self.s.execute(select(Dataset).where(Dataset.rel_path == rel)).scalar_one_or_none()
        if ds is not None and ds.checksum == cs and not self.force:
            self.stats["files_unchanged"] += 1
            return None
        if ds is not None:  # changed file → replace derived data for it
            self.s.execute(delete(DatasetRow).where(DatasetRow.dataset_id == ds.id))
            self.s.execute(delete(Machine).where(Machine.dataset_id == ds.id))
            self.s.execute(delete(MaterialProcess).where(MaterialProcess.dataset_id == ds.id))
            self.s.execute(delete(QualityIssue).where(QualityIssue.dataset_id == ds.id))
            self.s.execute(delete(Observation).where(Observation.dataset_id == ds.id))
        else:
            ds = Dataset(rel_path=rel)
            self.s.add(ds)
        ds.category_id = category.id if category else None
        ds.file_type = path.suffix.lower().lstrip(".")
        ds.role = role
        ds.columns = columns or []
        ds.checksum = cs
        ds.size_bytes = path.stat().st_size
        ds.imported_at = dt.datetime.now(dt.timezone.utc)
        self.s.flush()
        self.stats["files_imported"] += 1
        return ds

    def _category_for(self, path: Path) -> Category | None:
        rel = path.relative_to(self.root)
        if len(rel.parts) < 2:
            return None
        slug = map_folder(rel.parts[0])[0]
        return self.categories.get(slug)

    def _import_file(self, path: Path) -> None:
        rel = str(path.relative_to(self.root))
        if rel in ("digital_factory_tree.csv", "exchange_rate_table.csv"):
            if rel == "digital_factory_tree.csv":
                self._register(path, "taxonomy_tree", None, ["Level", "Parent", "Name", "Node Type"])
            return
        ext = path.suffix.lower()
        if ext in TEXT_EXT:
            self._scan_identifiers(path, rel)
        cat = self._category_for(path)
        if ext == ".csv":
            self._import_csv(path, rel, cat)
        elif ext == ".json":
            self._import_json(path, rel, cat)
        elif ext == ".xlsx":
            self._import_xlsx(path, rel, cat)
        elif ext in {".md", ".txt"} | DOC_EXT:
            ds = self._register(path, "document", cat)
            if ds is not None and ext in {".md", ".txt"}:
                text = path.read_text(encoding="utf-8", errors="replace")
                ds.notes = text[:4000]
                if cat and cat.slug == "standards":
                    self._standards_from_text(text, ds)
        else:
            self._register(path, "other", cat)

    # ------------------------------------------------------------------ CSV
    def _import_csv(self, path: Path, rel: str, cat: Category | None) -> None:
        with path.open(encoding="utf-8-sig", errors="replace", newline="") as fh:
            reader = csv.DictReader(fh)
            columns = [c for c in (reader.fieldnames or [])]
            rows = [r for r in reader]
        self._import_table(path, rel, cat, columns, rows)

    def _import_table(self, path: Path, rel: str, cat: Category | None, columns: list[str], rows: list[dict],
                      rel_suffix: str = "") -> None:
        role = classify_role(rel, columns)
        ds = self._register(path, role, cat, columns, rel_suffix)
        if ds is None:
            return
        ds.row_count = len(rows)
        iso, how = path_country(rel)
        ds.country_iso3 = iso if how != "digital_factory_default" else None
        self.s.bulk_insert_mappings(DatasetRow, [
            {"dataset_id": ds.id, "row_index": i, "data": {k: v for k, v in r.items() if k is not None}}
            for i, r in enumerate(rows)
        ])
        self.stats["rows"] += len(rows)
        handler = {
            "materials": self._materials, "machines": self._machines, "country_machine_rates": self._machines,
            "processes": self._processes, "operations": self._processes, "standards": self._standards_table,
        }.get(role)
        if handler:
            handler(ds, cat, rel, columns, rows)

    # ------------------------------------------------------------------ materials
    def _materials(self, ds: Dataset, cat: Category | None, rel: str, columns: list[str], rows: list[dict]) -> None:
        name_col = first_col(columns, NAME_COLS)
        if not name_col:
            self.s.add(QualityIssue(severity="warning", code="no_name_column", dataset_id=ds.id,
                                    message="Material file without a recognizable name column", context={"file": rel}))
            return
        type_col = first_col(columns, TYPE_COLS)
        price_cols = [c for c in columns if PRICE_RX.search(c.replace(" ", "").lower()) or PRICE_RX.search(c)]
        cut_col = first_col(columns, ["Cut Code", "cutCode"])
        cap_cols = [c for c in columns if c.startswith("Can ") or ".can" in c or c.startswith("processCompatibility.")]
        period = self.release_date if "indian data" not in rel.lower() and "/india/" not in rel.lower() else None
        for i, row in enumerate(rows):
            raw_name = (row.get(name_col) or "").strip()
            if not raw_name or raw_name.lower() in {"name", "manufacturabilityseverity"}:
                continue
            if not any(str(v).strip() for k, v in row.items() if k and k != name_col and v is not None):
                self.s.add(QualityIssue(severity="warning", code="incomplete_row", dataset_id=ds.id,
                                        message=f"Row {i} has a name but no other values — skipped",
                                        context={"file": rel, "row_index": i, "name": raw_name}))
                continue
            mtype = (row.get(type_col) or "").strip() if type_col else None
            mat = self._upsert_material(raw_name, mtype or None, row, columns, cut_col)
            caps = {c: row[c] for c in cap_cols if row.get(c) not in (None, "")}
            if cat is not None:
                self._link(mat, cat, ds, caps)
            iso, how = path_country(rel, row)
            for pc in price_cols:
                val = row.get(pc)
                if to_float(val) is None:
                    continue
                unit = unit_from_header(pc) or ("USD/kg" if re.search(r"usd_?per_?kg|usd_kg|UsdPerKg", pc, re.I) else None)
                form = re.sub(r"\s*(unit cost|cost per kg).*$", "", pc, flags=re.I).strip(" _.") or "base"
                form = form if form.lower() not in {"cost", "unitcost_usd_kg", "unit_cost_usd_per_kg"} else "base"
                flags = [] if how == "column" else [f"country_from_{how}"]
                if period:
                    flags.append("period_from_release_metadata")
                r = self.store.add(ObsIn(
                    kind="price", entity=raw_name, metric=f"unit_cost:{form.lower()}", value=val, unit=unit,
                    period_date=period, country=iso, material_id=mat.id, category_slug=cat.slug if cat else None,
                    dataset_id=ds.id, original={"row_index": i, "column": pc, "value": val, "file": rel},
                    flags=flags,
                ))
                self.stats[f"price_{r.outcome}"] += 1
        self.stats["material_files"] += 1

    def _upsert_material(self, raw_name: str, mtype: str | None, row: dict, columns: list[str],
                         cut_col: str | None) -> Material:
        mat = self.store.resolve_material(raw_name, mtype)
        if mtype and not mat.material_type:
            mat.material_type = mtype
        if mat.family == "unclassified":
            mat.family = classify_family(raw_name, mtype)
        mat.grade = mat.grade or extract_grade(raw_name)
        des = dict(mat.designations or {})
        for col, key in DESIG_COLS.items():
            v = row.get(col)
            if v and str(v).strip() and str(v).strip().lower() not in {"n/a", "na", "-"}:
                des.setdefault(key, str(v).strip())
        mat.designations = des
        if cut_col and row.get(cut_col):
            mat.cut_code = mat.cut_code or str(row[cut_col]).strip()
        props = dict(mat.properties or {})
        for c in columns:
            norm = normalize_property_header(c)
            if not norm:
                continue
            key, unit = norm
            raw = row.get(c)
            if raw in (None, ""):
                continue
            num = to_float(raw)
            if key not in props:
                props[key] = {"value": num if num is not None else str(raw), "unit": unit, "original_header": c}
            if key == "density" and num and not mat.density_kg_m3:
                mat.density_kg_m3 = num
        mat.properties = props
        return mat

    def _link(self, mat: Material, cat: Category, ds: Dataset, caps: dict) -> None:
        exists = self.s.execute(select(MaterialProcess.id).where(
            MaterialProcess.material_id == mat.id, MaterialProcess.category_id == cat.id,
            MaterialProcess.dataset_id == ds.id)).first()
        if not exists:
            self.s.add(MaterialProcess(material_id=mat.id, category_id=cat.id, dataset_id=ds.id, capabilities=caps))

    # ------------------------------------------------------------------ machines
    def _machines(self, ds: Dataset, cat: Category | None, rel: str, columns: list[str], rows: list[dict]) -> None:
        name_col = first_col(columns, MACHINE_NAME_COLS)
        if not name_col:
            return
        lab = next((c for c in columns if LABOR_RX.search(c.strip())), None)
        dov = next((c for c in columns if DIRECT_RX.search(c.strip())), None)
        iov = next((c for c in columns if INDIRECT_RX.search(c.strip())), None)
        proc_col = first_col(columns, ["Process Name", "Process", "process_name", "Commodity", "Process Goup"])
        out = []
        for row in rows:
            nm = (row.get(name_col) or "").strip()
            if not nm:
                continue
            iso, _ = path_country(rel, row)
            out.append({
                "category_id": cat.id if cat else None, "dataset_id": ds.id, "name": nm[:300],
                "process_name": (row.get(proc_col) or None) if proc_col else None, "country_iso3": iso,
                "labor_rate_usd_hr": to_float(row.get(lab)) if lab else None,
                "direct_overhead_usd_hr": to_float(row.get(dov)) if dov else None,
                "indirect_overhead_usd_hr": to_float(row.get(iov)) if iov else None,
                "attributes": {k: v for k, v in row.items() if k and v not in (None, "")},
            })
        if out:
            self.s.bulk_insert_mappings(Machine, out)
            self.stats["machines"] += len(out)

    # ------------------------------------------------------------------ processes
    def _processes(self, ds: Dataset, cat: Category | None, rel: str, columns: list[str], rows: list[dict]) -> None:
        col = first_col(columns, PROCESS_COLS)
        if not col or cat is None:
            return
        kind = "operation" if ds.role == "operations" else "process"
        dm = first_col(columns, ["Default Machine", "default_machine"])
        existing = {p.name: p for p in self.s.execute(select(Process).where(Process.category_id == cat.id)).scalars()}
        for row in rows:
            nm = (row.get(col) or "").strip()
            if not nm:
                continue
            p = existing.get(nm)
            if p is None:
                p = Process(category_id=cat.id, name=nm, normalized_name=re.sub(r"[^a-z0-9]+", " ", nm.lower()).strip(),
                            attributes={"kinds": [kind], "datasets": [ds.rel_path]})
                self.s.add(p)
                existing[nm] = p
            else:
                attrs = dict(p.attributes or {})
                attrs["kinds"] = sorted(set(attrs.get("kinds", [])) | {kind})
                attrs["datasets"] = sorted(set(attrs.get("datasets", [])) | {ds.rel_path})
                p.attributes = attrs
            if dm and row.get(dm):
                p.default_machine = row[dm]
        self.stats["process_rows"] += len(rows)

    # ------------------------------------------------------------------ standards
    def _standards_table(self, ds, cat, rel, columns, rows) -> None:
        name = Path(rel).stem
        m = re.search(r"iso\s?(\d{2,5})", name, re.I)
        if m:
            self._upsert_standard(f"ISO {m.group(1)}", "ISO", ds,
                                  title=f"Tolerance table imported from {Path(rel).name}",
                                  raw={"columns": columns, "rows": len(rows)})

    def _standards_from_text(self, text: str, ds: Dataset) -> None:
        for m in re.finditer(r"\b(ISO|ASTM|DIN|EN|JIS|IS|BS|SAE|ANSI|IPC)\s?([A-Z]?\d[\w\-:.]*)", text):
            self._upsert_standard(f"{m.group(1)} {m.group(2)}".strip(" .:"), m.group(1), ds)

    def _upsert_standard(self, code, body, ds, title=None, raw=None) -> None:
        st = self.s.execute(select(Standard).where(Standard.code == code, Standard.body == body)).scalar_one_or_none()
        if st is None:
            self.s.add(Standard(code=code, body=body, title=title, dataset_id=ds.id, raw=raw or {}, categories=[]))
            self.s.flush()
            self.stats["standards"] += 1

    # ------------------------------------------------------------------ JSON
    def _import_json(self, path: Path, rel: str, cat: Category | None) -> None:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        records: list[dict] = []
        meta: dict[str, Any] = {}
        if isinstance(data, list):
            records = [r for r in data if isinstance(r, dict)]
        elif isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, list) and v and isinstance(v[0], dict) and not records:
                    records = v
                else:
                    meta[k] = v if not isinstance(v, (dict, list)) else f"<{type(v).__name__}>"
        cols = sorted({k for r in records for k in r.keys()})
        role = "database"
        ds = self._register(path, role, cat, cols)
        if ds is None:
            return
        ds.row_count = len(records)
        ds.notes = json.dumps(meta, default=str)[:2000] if meta else None
        self.s.bulk_insert_mappings(DatasetRow, [{"dataset_id": ds.id, "row_index": i, "data": r}
                                                  for i, r in enumerate(records)])
        self.stats["rows"] += len(records)
        name = path.name.lower()
        if name.startswith("raw_material_database"):
            self._json_country_prices(ds, rel, records, name_key="Material Name", type_key="Material Type",
                                      price_rx=re.compile(r"^(.*) Cost \(USD\)$"), unit="USD/kg", period=None)
        elif name.startswith("raw-materials"):
            ex = (data.get("exportedAt") if isinstance(data, dict) else None) or \
                 (re.search(r"(\d{4}-\d{2}-\d{2})", name).group(1) if re.search(r"(\d{4}-\d{2}-\d{2})", name) else None)
            self._json_country_prices(ds, rel, records, name_key="material", type_key="materialType",
                                      price_rx=re.compile(r"^cost(France|Germany|WEurope|Usa|India|EEurope|China)$"),
                                      unit=None, period=ex)
        elif name.startswith("generic_materials_mexico"):
            for i, r in enumerate(records):
                mat = self.store.resolve_material(r["name"], r.get("material_type"))
                if r.get("density_kg_m3") and not mat.density_kg_m3:
                    mat.density_kg_m3 = r["density_kg_m3"]
                unit = {"per_kg": "USD/kg", "per_sqft": "USD/sqft"}.get(r.get("cost_units"), r.get("cost_units"))
                res = self.store.add(ObsIn(kind="price", entity=r["name"], metric="base_cost", value=r.get("base_cost"),
                                           unit=unit, country="MEX", material_id=mat.id, dataset_id=ds.id,
                                           original={"row_index": i, "file": rel, "cost_units": r.get("cost_units")}))
                self.stats[f"price_{res.outcome}"] += 1
        elif name.startswith(("hr-rates", "combined_all_countries")):
            out = []
            for r in records:
                nm = r.get("machineName") or r.get("Machine Name")
                if not nm:
                    continue
                iso = to_iso3(r.get("location") or r.get("s") or "")
                out.append({"category_id": cat.id if cat else None, "dataset_id": ds.id, "name": str(nm)[:300],
                            "process_name": r.get("Process Goup") or r.get("commodityCode"), "country_iso3": iso,
                            "labor_rate_usd_hr": to_float(r.get("Skill Based Labor Rate")),
                            "direct_overhead_usd_hr": to_float(r.get("Direct Overhead Rate")),
                            "indirect_overhead_usd_hr": to_float(r.get("Indirect Overhead rate")),
                            "attributes": {k: v for k, v in r.items() if v not in (None, "", {}) and k != "calculations"}})
            self.s.bulk_insert_mappings(Machine, out)
            self.stats["machines"] += len(out)
        elif name.startswith("process-database") and cat is not None:
            for r in records:
                nm = f"{r.get('processRoute')} / {r.get('operation')}"
                if not self.s.execute(select(Process.id).where(Process.category_id == cat.id, Process.name == nm)).first():
                    self.s.add(Process(category_id=cat.id, name=nm[:255], normalized_name=nm.lower()[:255],
                                       attributes={"kinds": ["process_mapping"], "processGroup": r.get("processGroup"),
                                                   "calculatorName": r.get("calculatorName")}))
                    self.s.flush()

    def _json_country_prices(self, ds, rel, records, *, name_key, type_key, price_rx, unit, period) -> None:
        for i, r in enumerate(records):
            nm = r.get(name_key)
            if not nm:
                continue
            mat = self.store.resolve_material(str(nm), r.get(type_key))
            if not mat.density_kg_m3:
                mat.density_kg_m3 = to_float(r.get("Density (kg/m³)") or r.get("densityKgM3"))
            props = dict(mat.properties or {})
            for k, v in r.items():
                norm = normalize_property_header(k)
                if norm and v not in (None, "", "N/A") and norm[0] not in props:
                    props[norm[0]] = {"value": to_float(v) if to_float(v) is not None else v, "unit": norm[1],
                                      "original_header": k}
            mat.properties = props
            cost_units = r.get("Cost Units")
            u = {None: unit, "Cost per KG": unit, "Cost per Sq Meter": "USD/m2"}.get(cost_units)
            for k, v in r.items():
                m = price_rx.match(k)
                if not m or to_float(v) is None:
                    continue
                region = {"WEurope": "W. Europe", "EEurope": "E. Europe", "Usa": "USA", "E. Europe": "Eastern Europe"}.get(
                    m.group(1), m.group(1))
                res = self.store.add(ObsIn(
                    kind="price", entity=str(nm), metric="unit_cost", value=v, unit=u, currency="USD",
                    period_date=period, country=region, material_id=mat.id, dataset_id=ds.id,
                    original={"row_index": i, "column": k, "value": v, "file": rel, "cost_units": cost_units},
                    flags=[] if u else ["unit_not_stated_in_source"],
                ))
                self.stats[f"price_{res.outcome}"] += 1

    # ------------------------------------------------------------------ XLSX
    def _import_xlsx(self, path: Path, rel: str, cat: Category | None) -> None:
        from openpyxl import load_workbook

        wb = load_workbook(path, read_only=True, data_only=True)
        for ws in wb.worksheets:
            it = ws.iter_rows(values_only=True)
            header = None
            rows: list[dict] = []
            for raw in it:
                if header is None:
                    if raw and sum(1 for c in raw if c not in (None, "")) >= 2:
                        header = [str(c).strip() if c is not None else f"col_{i}" for i, c in enumerate(raw)]
                    continue
                if raw is None or all(c in (None, "") for c in raw):
                    continue
                rows.append({h: (v if not isinstance(v, (dt.date, dt.datetime)) else v.isoformat())
                             for h, v in zip(header, raw)})
                if len(rows) >= 50_000:
                    break
            if header:
                self._import_table(path, rel + f"#{ws.title}", cat, header, rows, rel_suffix=f"#{ws.title}")
        wb.close()

    # ------------------------------------------------------------------ MHR / LHR
    def _scan_identifiers(self, path: Path, rel: str) -> None:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        for code in UNRESOLVED_CODES:
            for m in re.finditer(rf"(?<![A-Za-z]){code}(?![A-Za-z])|{code.lower()}(?=[A-Z_])", text):
                self.occurrences[code] += 1
                if len(self.evidence[code]) < 40:
                    a, b = max(0, m.start() - 70), min(len(text), m.end() + 70)
                    self.evidence[code].append({"file": rel, "context": re.sub(r"\s+", " ", text[a:b])})

    def _finalize_identifiers(self) -> None:
        for code in UNRESOLVED_CODES:
            ui = self.s.execute(select(UnresolvedIdentifier).where(UnresolvedIdentifier.code == code)).scalar_one_or_none()
            if ui is None:
                ui = UnresolvedIdentifier(code=code, status="unresolved")
                self.s.add(ui)
            if self.occurrences.get(code) or ui.occurrences == 0:
                ui.occurrences = self.occurrences.get(code, 0)
                ui.evidence = self.evidence.get(code, [])

    def _update_category_counts(self) -> None:
        for cat in self.categories.values():
            rows = self.s.execute(select(Dataset.row_count).where(Dataset.category_id == cat.id)).scalars().all()
            cat.dataset_count = len(rows)
            cat.row_count = int(sum(r or 0 for r in rows))
