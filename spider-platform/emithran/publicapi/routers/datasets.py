"""Datasets · Files — raw access to every imported memory/ file."""
from __future__ import annotations

import csv
import io
import mimetypes
from pathlib import Path as FsPath
from typing import Literal

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import String, cast, func, select
from sqlalchemy.orm import Session

from ...config import get_settings
from ...db import get_session
from ...models import Category, Dataset, DatasetRow
from ..auth import scoped
from ..common import jsonable, next_token, page_params, parse_rid, rid
from ..errors import invalid, not_found
from .ontologies import PAGE_SIZE, PAGE_TOKEN, ext

R = scoped("api:datasets-read")
router = APIRouter(prefix="/api/v2/datasets")
DRID = Path(..., description="Dataset RID (`ri.emithran.main.dataset.<id>`) or numeric id.", examples=["ri.emithran.main.dataset.42"])


def ds_json(d: Dataset, cat: Category | None = None) -> dict:
    return {"rid": rid("dataset", d.id), "name": FsPath(d.rel_path.split("#")[0]).name + (("#" + d.rel_path.split("#")[1]) if "#" in d.rel_path else ""),
            "path": d.rel_path, "fileType": d.file_type, "role": d.role, "category": cat.slug if cat else None,
            "countryIso3": d.country_iso3, "rowCount": d.row_count, "columnCount": len(d.columns or []),
            "sizeBytes": d.size_bytes, "checksum": {"algorithm": "SHA-256", "value": d.checksum},
            "importedAt": jsonable(d.imported_at)}


def load(s: Session, dataset_rid: str) -> Dataset:
    key = parse_rid(dataset_rid, "dataset")
    d = s.get(Dataset, int(key)) if key.isdigit() else None
    if d is None:
        raise not_found("DatasetNotFound", datasetRid=dataset_rid)
    return d


@router.get("", summary="List Datasets", openapi_extra=ext("Datasets", "Datasets", "api:datasets-read"),
            description="Lists datasets imported from memory/ (one per CSV/JSON file or XLSX sheet, plus documents). "
                        "Filter by category slug, inferred role, file type or a path substring.")
def list_datasets(category: str | None = Query(None, description="Category slug, e.g. `die-casting`."),
                  role: str | None = Query(None, description="materials, machines, processes, operations, lookup, variables, wage_grades, country_machine_rates, database, document …"),
                  fileType: str | None = Query(None, description="csv, json, xlsx, md, pdf …"),
                  pathContains: str | None = Query(None, description="Case-insensitive substring of the path."),
                  pageSize: int | None = PAGE_SIZE, pageToken: str | None = PAGE_TOKEN,
                  s: Session = Depends(get_session), _=Depends(R)):
    size, off = page_params(pageSize, pageToken)
    stmt = select(Dataset, Category).outerjoin(Category, Category.id == Dataset.category_id)
    if category:
        stmt = stmt.where(Category.slug == category)
    if role:
        stmt = stmt.where(Dataset.role == role)
    if fileType:
        stmt = stmt.where(Dataset.file_type == fileType.lower())
    if pathContains:
        stmt = stmt.where(func.lower(Dataset.rel_path).like(f"%{pathContains.lower()}%"))
    total = s.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = s.execute(stmt.order_by(Dataset.rel_path).limit(size).offset(off)).all()
    return {"nextPageToken": next_token(off, size, total, len(rows)), "totalCount": total, "data": [ds_json(d, c) for d, c in rows]}


@router.get("/{datasetRid}", summary="Get Dataset", openapi_extra=ext("Datasets", "Datasets", "api:datasets-read"),
            description="Gets a dataset's metadata: original path, inferred role, category, row count and checksum.")
def get_dataset(datasetRid: str = DRID, s: Session = Depends(get_session), _=Depends(R)):
    d = load(s, datasetRid)
    return ds_json(d, d.category)


def _infer(values: list) -> str:
    seen = [v for v in values if v not in (None, "")]
    if not seen:
        return "STRING"
    def ok(fn):
        try:
            for v in seen:
                fn(v)
            return True
        except (TypeError, ValueError):
            return False
    if all(isinstance(v, bool) or str(v).lower() in ("true", "false") for v in seen):
        return "BOOLEAN"
    if ok(lambda v: int(str(v))):
        return "LONG"
    if ok(lambda v: float(str(v).replace(",", ""))):
        return "DOUBLE"
    return "STRING"


@router.get("/{datasetRid}/schema", summary="Get Dataset Schema", openapi_extra=ext("Datasets", "Datasets", "api:datasets-read"),
            description="Returns the dataset's columns exactly as named in the file, with a type inferred from up to 500 rows "
                        "(`LONG`, `DOUBLE`, `BOOLEAN`, `STRING`). Inference never changes stored values.")
def get_schema(datasetRid: str = DRID, s: Session = Depends(get_session), _=Depends(R)):
    d = load(s, datasetRid)
    sample = [r.data for r in s.execute(select(DatasetRow).where(DatasetRow.dataset_id == d.id).order_by(DatasetRow.row_index).limit(500)).scalars()]
    return {"datasetRid": rid("dataset", d.id), "fieldSchemaList": [
        {"name": c, "type": _infer([r.get(c) for r in sample]), "nullable": any(r.get(c) in (None, "") for r in sample)}
        for c in (d.columns or [])]}


@router.get("/{datasetRid}/readTable", summary="Read Table Dataset", openapi_extra=ext("Datasets", "Datasets", "api:datasets-read"),
            description="Reads rows of a dataset as JSON (paged) or CSV (streamed). Values are returned exactly as imported. "
                        "`where` performs a case-insensitive substring match over the whole row.")
def read_table(datasetRid: str = DRID,
               format: Literal["JSON", "CSV"] = Query("JSON", description="`JSON` (paged) or `CSV` (streams up to rowLimit rows)."),
               columns: list[str] | None = Query(None, description="Repeatable. Columns to return; default all."),
               rowLimit: int | None = Query(None, ge=1, le=100_000, description="CSV only: maximum rows (default all)."),
               where: str | None = Query(None, description="Substring that must appear in the row."),
               pageSize: int | None = PAGE_SIZE, pageToken: str | None = PAGE_TOKEN,
               s: Session = Depends(get_session), _=Depends(R)):
    d = load(s, datasetRid)
    cols = columns or d.columns or []
    unknown = [c for c in cols if c not in (d.columns or [])]
    if unknown:
        raise invalid("ColumnsNotFound", datasetRid=datasetRid, columns=unknown)
    stmt = select(DatasetRow).where(DatasetRow.dataset_id == d.id)
    if where:
        stmt = stmt.where(cast(DatasetRow.data, String).ilike(f"%{where}%"))
    stmt = stmt.order_by(DatasetRow.row_index)
    if format == "CSV":
        lim = rowLimit or 1_000_000
        fetched = [r.data for r in s.execute(stmt.limit(lim)).scalars()]

        def gen():
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(cols)
            yield buf.getvalue()
            for data in fetched:
                buf.seek(0)
                buf.truncate()
                w.writerow([data.get(c, "") for c in cols])
                yield buf.getvalue()
        name = FsPath(d.rel_path.replace("#", "_")).stem + ".csv"
        return StreamingResponse(gen(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}"'})
    size, off = page_params(pageSize, pageToken)
    total = s.execute(select(func.count()).select_from(stmt.order_by(None).subquery())).scalar_one()
    rows = s.execute(stmt.limit(size).offset(off)).scalars().all()
    return {"nextPageToken": next_token(off, size, total, len(rows)), "totalCount": total, "columns": cols,
            "data": [{"rowIndex": r.row_index, "values": {c: r.data.get(c) for c in cols}} for r in rows]}


@router.get("/{datasetRid}/files/content", summary="Get File Content", openapi_extra=ext("Datasets", "Files", "api:datasets-read"),
            description="Downloads the original file from memory/ byte-for-byte (for an XLSX sheet dataset, the whole workbook). "
                        "The SHA-256 checksum from Get Dataset lets you verify the download.")
def file_content(datasetRid: str = DRID, s: Session = Depends(get_session), _=Depends(R)):
    d = load(s, datasetRid)
    root = get_settings().data_root.resolve()
    path = (root / d.rel_path.split("#")[0]).resolve()
    if root not in path.parents or not path.is_file():
        raise not_found("FileNotFound", datasetRid=datasetRid, path=d.rel_path)
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream", filename=path.name)
