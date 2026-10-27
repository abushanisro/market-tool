from fastapi.testclient import TestClient
from sqlalchemy import func, select

from conftest import FIX


def test_import_mini_taxonomy(seeded):
    from mip.db import session_scope
    from mip.models import Category, Dataset, Machine, Material, Observation, Process, UnresolvedIdentifier

    with session_scope() as s:
        cat = s.execute(select(Category).where(Category.slug == "die-casting")).scalar_one()
        assert cat.original_folder == "Die Casting" and cat.tree_node_name == "Casting - Die"
        al = s.execute(select(Material).where(Material.key == "aluminum a380")).scalar_one()
        assert al.family == "aluminium" and al.density_kg_m3 == 2740
        price = s.execute(select(Observation).where(Observation.material_id == al.id)).scalar_one()
        assert price.value_usd == 2.95 and price.status == "verified" and str(price.period_date) == "2026-03-01"
        mg = s.execute(select(Material).where(Material.key == "magnesium az91d")).scalar_one()
        assert s.execute(select(Observation.status).where(Observation.material_id == mg.id)).scalar_one() == "quarantined"
        fx = s.execute(select(Observation).where(Observation.kind == "fx", Observation.entity == "USD/INR")).scalar_one()
        assert fx.value == 64.3455 and "fx_internal_table" in fx.quality_flags
        assert s.execute(select(func.count()).select_from(Machine)).scalar_one() == 2
        india = s.execute(select(Machine).where(Machine.country_iso3 == "IND")).scalar_one()
        assert india.labor_rate_usd_hr == 4.2
        assert {p.name for p in s.execute(select(Process)).scalars()} == {"High Pressure Die Casting", "Trim"}
        mhr = s.execute(select(UnresolvedIdentifier).where(UnresolvedIdentifier.code == "MHR")).scalar_one()
        assert mhr.status == "unresolved" and mhr.occurrences == 2 and mhr.confirmed_definition is None
    # idempotent: unchanged files are skipped
    from mip.taxonomy.importer import TaxonomyImporter
    with session_scope() as s:
        stats = TaxonomyImporter(s, FIX / "mini_memory").run()
        assert stats.get("files_imported", 0) == 0 and stats["files_unchanged"] >= 5


def test_api_and_guards(seeded):
    from mip.api.main import app

    c = TestClient(app)
    ov = c.get("/api/overview").json()
    assert ov["taxonomy"]["materials"] == 3 and ov["records"]["by_origin"]["crawled_live"] == 0
    # blocked / planned sources can never be crawled
    assert c.post("/api/jobs", json={"source": "matweb"}).status_code == 400
    assert c.post("/api/jobs", json={"source": "imf-pcps"}).status_code == 400
    # registering a new source needs no code; invalid parser rejected
    bad = c.post("/api/sources", json={"slug": "x-src", "name": "X", "base_url": "https://x.org", "source_type": "html",
                                       "extraction": {"parser": "nope"}})
    assert bad.status_code == 422 or bad.status_code == 400
    ok = c.post("/api/sources", json={"slug": "steel-portal", "name": "Steel portal", "base_url": "https://example.org/prices",
                                      "source_type": "html", "start_urls": ["https://example.org/prices"],
                                      "extraction": {"parser": "html_table", "columns": {"entity": "Grade", "value": "Price"}}})
    assert ok.status_code == 200 and ok.json()["allowed_domains"] == ["example.org"] and ok.json()["status"] == "paused"
    assert c.patch("/api/sources/steel-portal", json={"status": "active"}).status_code == 400  # planned → cannot activate
    mats = c.get("/api/materials?category=die-casting").json()
    assert mats["total"] == 3
    al = next(m for m in mats["items"] if m["name"] == "Aluminum A380")
    alt = c.get(f"/api/materials/{al['id']}/alternatives?category=die-casting&same_family=false").json()
    assert all(x["family"] in {"zinc", "magnesium", "aluminium"} for x in alt["items"])
    assert c.post("/api/identifiers/MHR/confirm", json={"definition": "", "confirmed_by": ""}).status_code == 400
    for path in ["/api/taxonomy/categories", "/api/market/fx", "/api/market/matrix", "/api/market/labor", "/api/data/issues",
                 "/api/settings", "/api/alerts", "/api/sources", "/"]:
        assert c.get(path).status_code == 200, path


def test_process_label_comes_from_file_name_when_no_column():
    from mip.taxonomy.importer import process_from_filename as f

    assert f("Rapid prototyping/Machines/machines_laser_sintering.csv") == "Laser Sintering"
    assert f("Die Casting/Machine/high_pressure_die_casting_machines.csv") == "High Pressure Die Casting"
    assert f("Sheetmetal/machine/all_machines.csv") is None  # nothing meaningful left → stays unspecified, never invented


def test_every_imported_machine_has_a_process_group(seeded):
    from sqlalchemy import func, select

    from mip.db import session_scope
    from mip.models import Machine

    with session_scope() as s:
        total = s.execute(select(func.count()).select_from(Machine)).scalar_one()
        grouped = s.execute(select(func.count()).select_from(Machine).where(Machine.process_name.is_not(None))).scalar_one()
    assert total > 0 and grouped == total
