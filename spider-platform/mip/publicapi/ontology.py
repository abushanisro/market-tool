"""The `manufacturing` ontology: typed object views over every table derived from memory/.

Each object type maps API property names (camelCase) to columns. Scalar properties can be used in
``where`` / ``orderBy`` / ``groupBy``; ``struct`` properties are returned as-is (original values).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy import Select, String, and_, cast, func, not_, or_, select
from sqlalchemy.orm import Session

from ..models import (
    Category, Country, Dataset, Document, Machine, Material, MaterialProcess, Observation, Process, QualityIssue,
    Source, Standard, TreeNode, UnresolvedIdentifier,
)
from .common import jsonable, rid
from .errors import invalid, not_found

ONTOLOGY = {"apiName": "manufacturing", "displayName": "Emithran Manufacturing",
            "description": "Manufacturing taxonomy, materials, machines, processes, prices and market data imported from the "
                           "memory/ folder and crawled public sources.",
            "rid": "ri.emithran.main.ontology.manufacturing"}

SCALAR = {"string", "integer", "double", "boolean", "date", "timestamp"}


@dataclass
class Prop:
    api: str
    col: Any
    type: str
    description: str
    nullable: bool = True

    @property
    def filterable(self) -> bool:
        return self.type in SCALAR or self.type == "array"


@dataclass
class Link:
    api: str
    target: str
    cardinality: str  # ONE | MANY
    description: str
    query: Callable[[Any], Select]  # pk-row → select of target model


@dataclass
class ObjectType:
    api: str
    display: str
    plural: str
    description: str
    model: Any
    pk: str  # property apiName used as primary key
    rid_kind: str
    title: str  # title property
    props: list[Prop]
    links: list[Link] = field(default_factory=list)

    def prop(self, name: str) -> Prop:
        for p in self.props:
            if p.api == name:
                return p
        raise invalid("PropertiesNotFound", objectType=self.api, properties=[name])

    def metadata(self) -> dict[str, Any]:
        return {"apiName": self.api, "displayName": self.display, "pluralDisplayName": self.plural,
                "description": self.description, "primaryKey": self.pk, "titleProperty": self.title,
                "rid": rid("object-type", self.api), "status": "ACTIVE",
                "properties": {p.api: {"dataType": {"type": p.type}, "description": p.description,
                                       "filterable": p.filterable} for p in self.props},
                "links": [{"apiName": l.api, "objectTypeApiName": l.target, "cardinality": l.cardinality,
                           "description": l.description} for l in self.links]}


def P(api, col, type_, desc):
    return Prop(api, col, type_, desc)


OBJECT_TYPES: dict[str, ObjectType] = {}


def register(ot: ObjectType) -> None:
    OBJECT_TYPES[ot.api] = ot


register(ObjectType(
    "ManufacturingCategory", "Manufacturing category", "Manufacturing categories",
    "One per folder of the existing taxonomy. `originalFolder` keeps the folder name exactly as on disk.",
    Category, "slug", "category", "name", [
        P("slug", Category.slug, "string", "Normalized identifier, e.g. `die-casting`."),
        P("name", Category.name, "string", "Canonical display name."),
        P("originalFolder", Category.original_folder, "string", "Folder name exactly as in memory/ (spelling preserved)."),
        P("treeNodeName", Category.tree_node_name, "string", "Matching node in digital_factory_tree.csv, if any."),
        P("family", Category.family, "string", "Category family: casting, sheet_metal, machining, plastics …"),
        P("parentId", Category.parent_id, "integer", "Internal id of the parent category (e.g. sand-casting → casting)."),
        P("datasetCount", Category.dataset_count, "integer", "Number of imported files/sheets."),
        P("rowCount", Category.row_count, "integer", "Rows preserved across those files."),
    ], [
        Link("datasets", "Dataset", "MANY", "Files imported from this folder.", lambda r: select(Dataset).where(Dataset.category_id == r.id)),
        Link("materials", "Material", "MANY", "Materials listed in this category's material files.",
             lambda r: select(Material).where(Material.id.in_(
                 select(MaterialProcess.material_id).where(MaterialProcess.category_id == r.id)))),
        Link("machines", "Machine", "MANY", "Machines / work centers.", lambda r: select(Machine).where(Machine.category_id == r.id)),
        Link("processes", "Process", "MANY", "Processes and operations.", lambda r: select(Process).where(Process.category_id == r.id)),
        Link("subcategories", "ManufacturingCategory", "MANY", "Child categories.", lambda r: select(Category).where(Category.parent_id == r.id)),
    ]))

register(ObjectType(
    "TaxonomyNode", "Taxonomy node", "Taxonomy nodes", "Verbatim rows of digital_factory_tree.csv.",
    TreeNode, "id", "taxonomy-node", "name", [
        P("id", TreeNode.id, "integer", "Row number."), P("level", TreeNode.level, "integer", "Tree depth (0 = root)."),
        P("parentName", TreeNode.parent_name, "string", "Parent node name."), P("name", TreeNode.name, "string", "Node name."),
        P("nodeType", TreeNode.node_type, "string", "Folder or Document."),
        P("categoryId", TreeNode.category_id, "integer", "Mapped manufacturing category id, if any."),
    ]))

register(ObjectType(
    "Dataset", "Dataset", "Datasets",
    "An imported file (or spreadsheet tab) from memory/. Rows are readable through the Datasets API.",
    Dataset, "id", "dataset", "relPath", [
        P("id", Dataset.id, "integer", "Dataset id (also in its RID)."),
        P("relPath", Dataset.rel_path, "string", "Path relative to memory/, `#Sheet` suffix for XLSX tabs."),
        P("fileType", Dataset.file_type, "string", "csv, json, xlsx, md, pdf …"),
        P("role", Dataset.role, "string", "Inferred role: materials, machines, processes, operations, lookup, variables …"),
        P("countryIso3", Dataset.country_iso3, "string", "Country inferred from the path (Indian data → IND)."),
        P("columns", Dataset.columns, "array", "Column names exactly as in the file."),
        P("rowCount", Dataset.row_count, "integer", "Rows imported."),
        P("checksum", Dataset.checksum, "string", "SHA-256 of the file."),
        P("sizeBytes", Dataset.size_bytes, "integer", "File size."),
        P("importedAt", Dataset.imported_at, "timestamp", "Last import time."),
        P("categoryId", Dataset.category_id, "integer", "Owning category id."),
    ], [
        Link("category", "ManufacturingCategory", "ONE", "Folder category.", lambda r: select(Category).where(Category.id == r.category_id)),
        Link("observations", "Observation", "MANY", "Observations derived from this file.", lambda r: select(Observation).where(Observation.dataset_id == r.id)),
    ]))

register(ObjectType(
    "Material", "Material", "Materials",
    "Deduplicated raw material / grade with designations and technical properties as reported by the source files.",
    Material, "id", "material", "name", [
        P("id", Material.id, "integer", "Material id."), P("name", Material.name, "string", "Display name (original casing)."),
        P("key", Material.key, "string", "Normalized identity key."),
        P("family", Material.family, "string", "Normalized family: steel, stainless_steel, aluminium, thermoplastic …"),
        P("materialType", Material.material_type, "string", "Material Type as written in the source."),
        P("grade", Material.grade, "string", "Grade parsed from the name (e.g. AISI 304, ANSI 6061)."),
        P("cutCode", Material.cut_code, "string", "Cut code from the source."),
        P("densityKgM3", Material.density_kg_m3, "double", "Density in kg/m³."),
        P("designations", Material.designations, "struct", "usa / din / en / gb / jis / european designations."),
        P("technicalProperties", Material.properties, "struct", "Normalized property → {value, unit, original_header}."),
        P("originalNames", Material.original_names, "array", "Every spelling seen across files."),
    ], [
        Link("categories", "ManufacturingCategory", "MANY", "Processes whose material lists include this material.",
             lambda r: select(Category).where(Category.id.in_(
                 select(MaterialProcess.category_id).where(MaterialProcess.material_id == r.id)))),
        Link("observations", "Observation", "MANY", "Price and property observations.", lambda r: select(Observation).where(Observation.material_id == r.id)),
    ]))

register(ObjectType(
    "Machine", "Machine", "Machines",
    "Machine / work-center record with labour and overhead rates. `attributes` holds every original column, "
    "including identifiers such as MHR/LHR fields under their original names (not interpreted).",
    Machine, "id", "machine", "name", [
        P("id", Machine.id, "integer", "Machine id."), P("name", Machine.name, "string", "Machine / work center name."),
        P("processName", Machine.process_name, "string", "Process / commodity group from the source."),
        P("countryIso3", Machine.country_iso3, "string", "Country or region code (XWE, XEE for regions)."),
        P("laborRateUsdHr", Machine.labor_rate_usd_hr, "double", "Labour rate, USD/h, as given."),
        P("directOverheadUsdHr", Machine.direct_overhead_usd_hr, "double", "Direct overhead, USD/h."),
        P("indirectOverheadUsdHr", Machine.indirect_overhead_usd_hr, "double", "Indirect overhead, USD/h."),
        P("categoryId", Machine.category_id, "integer", "Category id."), P("datasetId", Machine.dataset_id, "integer", "Source dataset id."),
        P("attributes", Machine.attributes, "struct", "All original columns and values."),
    ], [
        Link("category", "ManufacturingCategory", "ONE", "Category.", lambda r: select(Category).where(Category.id == r.category_id)),
        Link("dataset", "Dataset", "ONE", "Source file.", lambda r: select(Dataset).where(Dataset.id == r.dataset_id)),
    ]))

register(ObjectType(
    "Process", "Process", "Processes", "Process or operation listed in a category's process files.",
    Process, "id", "process", "name", [
        P("id", Process.id, "integer", "Process id."), P("name", Process.name, "string", "Process name as written."),
        P("normalizedName", Process.normalized_name, "string", "Lower-case normalized name."),
        P("defaultMachine", Process.default_machine, "string", "Default machine, if given."),
        P("categoryId", Process.category_id, "integer", "Category id."),
        P("attributes", Process.attributes, "struct", "kinds (process/operation), datasets."),
    ], [Link("category", "ManufacturingCategory", "ONE", "Category.", lambda r: select(Category).where(Category.id == r.category_id))]))

register(ObjectType(
    "Observation", "Observation", "Observations",
    "One normalized fact (price, FX rate, indicator, trade value …) with original value/unit, provenance and quality status.",
    Observation, "id", "observation", "entity", [
        P("id", Observation.id, "integer", "Observation id."),
        P("kind", Observation.kind, "string", "price | fx | indicator | trade | labor_rate | machine_rate | property"),
        P("entity", Observation.entity, "string", "Original entity label (material name, currency pair …)."),
        P("metric", Observation.metric, "string", "Metric, e.g. unit_cost:base, exchange_rate."),
        P("value", Observation.value, "double", "Original numeric value."), P("valueText", Observation.value_text, "string", "Original non-numeric value."),
        P("unit", Observation.unit, "string", "Original unit."), P("currency", Observation.currency, "string", "Original currency."),
        P("valueUsd", Observation.value_usd, "double", "Normalized value (USD per normalized unit)."),
        P("unitNormalized", Observation.unit_normalized, "string", "USD/kg, USD/hr, USD/m2 …"),
        P("countryIso3", Observation.country_iso3, "string", "Country / region."),
        P("periodDate", Observation.period_date, "date", "Date the value refers to (null if the source does not say)."),
        P("observedAt", Observation.observed_at, "timestamp", "Extraction timestamp."),
        P("status", Observation.status, "string", "verified | unverified | quarantined | superseded"),
        P("confidence", Observation.confidence, "double", "0–1 confidence after quality rules."),
        P("qualityFlags", Observation.quality_flags, "array", "e.g. replayed, as_of_unknown, fx_fallback_internal_table."),
        P("seriesKey", Observation.series_key, "string", "Stable key for history."),
        P("sourceId", Observation.source_id, "integer", "Crawled source id (null = internal taxonomy)."),
        P("datasetId", Observation.dataset_id, "integer", "Dataset id for internal records."),
        P("materialId", Observation.material_id, "integer", "Material id."),
        P("categoryId", Observation.category_id, "integer", "Category id."),
        P("sourceUrl", Observation.source_url, "string", "Exact URL fetched."),
        P("original", Observation.original, "struct", "Original record / cell reference and conversion details."),
    ], [
        Link("material", "Material", "ONE", "Material.", lambda r: select(Material).where(Material.id == r.material_id)),
        Link("dataset", "Dataset", "ONE", "Source file.", lambda r: select(Dataset).where(Dataset.id == r.dataset_id)),
        Link("source", "Source", "ONE", "Crawled source.", lambda r: select(Source).where(Source.id == r.source_id)),
        Link("qualityIssues", "QualityIssue", "MANY", "Validation findings.", lambda r: select(QualityIssue).where(QualityIssue.observation_id == r.id)),
    ]))

register(ObjectType(
    "Country", "Country", "Countries", "Countries and regions used across the data (regions use X-prefixed codes).",
    Country, "iso3", "country", "name", [
        P("iso3", Country.iso3, "string", "ISO 3166-1 alpha-3 (or XWE / XEE / WLD)."), P("iso2", Country.iso2, "string", "Alpha-2."),
        P("name", Country.name, "string", "Name."), P("currency", Country.currency, "string", "ISO 4217 currency."),
        P("region", Country.region, "string", "Region."), P("isRegion", Country.is_region, "boolean", "True for aggregate regions."),
    ], [
        Link("machines", "Machine", "MANY", "Machine records for the country.", lambda r: select(Machine).where(Machine.country_iso3 == r.iso3)),
        Link("observations", "Observation", "MANY", "Observations for the country.", lambda r: select(Observation).where(Observation.country_iso3 == r.iso3)),
    ]))

register(ObjectType(
    "Source", "Source", "Sources", "Registered external data source with licensing and verification metadata.",
    Source, "slug", "source", "name", [
        P("slug", Source.slug, "string", "Identifier."), P("name", Source.name, "string", "Name."),
        P("baseUrl", Source.base_url, "string", "Base URL."), P("sourceType", Source.source_type, "string", "api | html | sitemap | rss | csv | xlsx | pdf | internal"),
        P("websiteCategory", Source.website_category, "string", "government, intergovernmental, research …"),
        P("dataKinds", Source.data_kinds, "array", "Kinds of data produced."), P("categories", Source.categories, "array", "Category slugs ('*' = all)."),
        P("countries", Source.countries, "array", "Countries covered."), P("license", Source.license, "string", "Licence."),
        P("commercialUse", Source.commercial_use, "string", "yes | no | conditional | unknown"),
        P("attribution", Source.attribution, "string", "Required attribution text."),
        P("verificationStatus", Source.verification_status, "string", "verified | url_verified | documented | key_required | planned | blocked_by_terms"),
        P("verifiedAt", Source.verified_at, "date", "Verification date."), P("updateFrequency", Source.update_frequency, "string", "Publisher update frequency."),
        P("reliability", Source.reliability, "double", "0–1 reliability score."), P("status", Source.status, "string", "active | paused | disabled"),
        P("lastSuccessAt", Source.last_success_at, "timestamp", "Last successful build."), P("totalRecords", Source.total_records, "integer", "Records stored."),
    ], [Link("observations", "Observation", "MANY", "Observations from this source.", lambda r: select(Observation).where(Observation.source_id == r.id)),
        Link("documents", "Document", "MANY", "Documents from this source.", lambda r: select(Document).where(Document.source_id == r.id))]))

register(ObjectType(
    "Standard", "Standard", "Standards", "Standards metadata from taxonomy files and standards sources.",
    Standard, "id", "standard", "code", [
        P("id", Standard.id, "integer", "Id."), P("code", Standard.code, "string", "e.g. ISO 286."), P("body", Standard.body, "string", "ISO, ASTM, BIS …"),
        P("title", Standard.title, "string", "Title."), P("status", Standard.status, "string", "Publication status."), P("url", Standard.url, "string", "Link."),
    ]))

register(ObjectType(
    "Document", "Document", "Documents", "Publications, news and reports (metadata; text only where licensing allows).",
    Document, "id", "document", "title", [
        P("id", Document.id, "integer", "Id."), P("kind", Document.kind, "string", "publication | news | report"),
        P("title", Document.title, "string", "Title."), P("url", Document.url, "string", "Canonical URL."),
        P("publishedAt", Document.published_at, "timestamp", "Publication time."), P("authors", Document.authors, "array", "Authors."),
        P("doi", Document.doi, "string", "DOI."), P("license", Document.license, "string", "Licence."), P("sourceId", Document.source_id, "integer", "Source id."),
    ]))

register(ObjectType(
    "UnresolvedIdentifier", "Unresolved identifier", "Unresolved identifiers",
    "Internal identifiers (MHR, LHR) kept uninterpreted until an owner confirms a definition.",
    UnresolvedIdentifier, "code", "identifier", "code", [
        P("code", UnresolvedIdentifier.code, "string", "MHR or LHR."), P("status", UnresolvedIdentifier.status, "string", "unresolved | confirmed"),
        P("occurrences", UnresolvedIdentifier.occurrences, "integer", "Occurrences across memory/."),
        P("confirmedDefinition", UnresolvedIdentifier.confirmed_definition, "string", "Definition, once confirmed."),
        P("evidence", UnresolvedIdentifier.evidence, "struct", "File + context snippets."),
    ]))

register(ObjectType(
    "QualityIssue", "Quality issue", "Quality issues", "Validation and import findings.",
    QualityIssue, "id", "quality-issue", "code", [
        P("id", QualityIssue.id, "integer", "Id."), P("severity", QualityIssue.severity, "string", "info | warning | error"),
        P("code", QualityIssue.code, "string", "e.g. unit_unknown, implausible_price."), P("message", QualityIssue.message, "string", "Message."),
        P("createdAt", QualityIssue.created_at, "timestamp", "When found."), P("datasetId", QualityIssue.dataset_id, "integer", "Dataset id."),
        P("observationId", QualityIssue.observation_id, "integer", "Observation id."), P("sourceId", QualityIssue.source_id, "integer", "Source id."),
        P("context", QualityIssue.context, "struct", "Details."),
    ]))


def get_type(name: str) -> ObjectType:
    ot = OBJECT_TYPES.get(name)
    if ot is None:
        raise not_found("ObjectTypeNotFound", objectType=name)
    return ot


def check_ontology(name: str) -> None:
    if name not in (ONTOLOGY["apiName"], ONTOLOGY["rid"]):
        raise not_found("OntologyNotFound", ontology=name)


def serialize(ot: ObjectType, row: Any, select_props: list[str] | None = None) -> dict[str, Any]:
    props = [ot.prop(p) for p in select_props] if select_props else ot.props
    pk_val = getattr(row, ot.prop(ot.pk).col.key)
    out: dict[str, Any] = {"__apiName": ot.api, "__primaryKey": pk_val,
                           "__rid": rid(ot.rid_kind, getattr(row, "id", pk_val)), "__title": getattr(row, ot.prop(ot.title).col.key)}
    for p in props:
        out[p.api] = jsonable(getattr(row, p.col.key))
    return out


def pk_filter(ot: ObjectType, pk: str):
    p = ot.prop(ot.pk)
    if p.type == "integer":
        try:
            return p.col == int(pk)
        except ValueError:
            raise not_found("ObjectNotFound", objectType=ot.api, primaryKey=pk)
    return p.col == pk


def get_object(s: Session, ot: ObjectType, pk: str):
    row = s.execute(select(ot.model).where(pk_filter(ot, pk))).scalar_one_or_none()
    if row is None:
        raise not_found("ObjectNotFound", objectType=ot.api, primaryKey=pk)
    return row


# ------------------------------------------------------------------ query DSL
COMPARE = {"eq": lambda c, v: c == v, "gt": lambda c, v: c > v, "gte": lambda c, v: c >= v,
           "lt": lambda c, v: c < v, "lte": lambda c, v: c <= v}


def compile_where(ot: ObjectType, q: dict | None, depth: int = 0):
    if q is None:
        return None
    if depth > 12 or not isinstance(q, dict) or "type" not in q:
        raise invalid("InvalidQuery", reason="each clause needs a 'type'", clause=q)
    t = q["type"]
    if t in ("and", "or"):
        parts = [compile_where(ot, x, depth + 1) for x in q.get("value", [])]
        return (and_ if t == "and" else or_)(*parts) if parts else None
    if t == "not":
        return not_(compile_where(ot, q.get("value"), depth + 1))
    p = ot.prop(q.get("field", ""))
    if not p.filterable:
        raise invalid("PropertyNotFilterable", objectType=ot.api, property=p.api)
    v = q.get("value")
    if t in COMPARE:
        if p.type == "array":
            raise invalid("InvalidQuery", reason=f"use 'contains' for array property {p.api}")
        return COMPARE[t](p.col, v)
    if t == "isNull":
        return p.col.is_(None) if v in (True, None) else p.col.is_not(None)
    if t == "in":
        if not isinstance(v, list):
            raise invalid("InvalidQuery", reason="'in' expects a list value")
        return p.col.in_(v)
    if t == "contains":  # array property contains a value
        return cast(p.col, String).like(f'%{_json_scalar(v)}%')
    if t == "startsWith":
        return func.lower(p.col).like(f"{str(v).lower()}%")
    if t in ("containsAnyTerm", "containsAllTerms"):
        terms = [x for x in str(v).split() if x]
        clauses = [func.lower(cast(p.col, String)).like(f"%{x.lower()}%") for x in terms]
        return (or_ if t == "containsAnyTerm" else and_)(*clauses) if clauses else None
    raise invalid("InvalidQuery", reason=f"unsupported query type {t!r}",
                  supported=["eq", "gt", "gte", "lt", "lte", "isNull", "in", "contains", "startsWith",
                             "containsAnyTerm", "containsAllTerms", "and", "or", "not"])


def _json_scalar(v) -> str:
    import json
    return json.dumps(v)


def apply_order(ot: ObjectType, stmt, order_by: dict | None):
    if not order_by:
        return stmt.order_by(ot.prop(ot.pk).col)
    fields = order_by.get("fields") or []
    cols = []
    for f in fields:
        p = ot.prop(f.get("field", ""))
        if p.type not in SCALAR:
            raise invalid("PropertyNotFilterable", objectType=ot.api, property=p.api)
        cols.append(p.col.desc().nulls_last() if f.get("direction", "asc") == "desc" else p.col.asc().nulls_last())
    return stmt.order_by(*cols, ot.prop(ot.pk).col)


AGGS = {"count": None, "min": func.min, "max": func.max, "avg": func.avg, "sum": func.sum,
        "approximateDistinct": lambda c: func.count(func.distinct(c))}


def aggregate(s: Session, ot: ObjectType, body: dict) -> dict[str, Any]:
    aggs = body.get("aggregation") or []
    if not aggs or len(aggs) > 10:
        raise invalid("InvalidQuery", reason="provide 1–10 aggregation metrics")
    metrics, names = [], []
    for i, a in enumerate(aggs):
        t = a.get("type")
        if t not in AGGS:
            raise invalid("InvalidQuery", reason=f"unsupported aggregation {t!r}", supported=list(AGGS))
        name = a.get("name") or (t if t == "count" else f"{t}_{a.get('field')}")
        if t == "count":
            metrics.append(func.count())
        else:
            p = ot.prop(a.get("field", ""))
            if t in ("avg", "sum") and p.type not in ("integer", "double"):
                raise invalid("InvalidQuery", reason=f"{t} requires a numeric property", property=p.api)
            metrics.append(AGGS[t](p.col))
        names.append(name)
    groups, gnames = [], []
    for g in body.get("groupBy") or []:
        p = ot.prop(g.get("field", ""))
        if p.type not in SCALAR:
            raise invalid("PropertyNotFilterable", objectType=ot.api, property=p.api)
        if g.get("type", "exact") == "exact":
            groups.append(p.col)
        elif g["type"] == "fixedWidth":
            w = g.get("fixedWidth")
            if not isinstance(w, (int, float)) or w <= 0 or p.type not in ("integer", "double"):
                raise invalid("InvalidQuery", reason="fixedWidth needs a positive width on a numeric property")
            groups.append(func.floor(p.col / w) * w)
        else:
            raise invalid("InvalidQuery", reason=f"unsupported groupBy type {g.get('type')!r}", supported=["exact", "fixedWidth"])
        gnames.append(p.api)
    stmt = select(*groups, *metrics).select_from(ot.model)
    w = compile_where(ot, body.get("where"))
    if w is not None:
        stmt = stmt.where(w)
    max_groups = min(int(max([g.get("maxGroupCount", 1000) for g in body.get("groupBy") or [{}]])), 10_000)
    if groups:
        stmt = stmt.group_by(*groups).order_by(metrics[0].desc()).limit(max_groups + 1)
    rows = s.execute(stmt).all()
    accuracy = "ACCURATE"
    if groups and len(rows) > max_groups:
        rows, accuracy = rows[:max_groups], "APPROXIMATE"
    data = []
    for r in rows:
        g = {gnames[i]: jsonable(r[i]) for i in range(len(groups))}
        m = [{"name": names[j], "value": (float(v) if isinstance(v, (int, float)) or hasattr(v, "__float__") else v) if v is not None else None}
             for j, v in enumerate(r[len(groups):])]
        data.append({"group": g, "metrics": m})
    return {"excludedItems": 0, "accuracy": accuracy, "data": data}
