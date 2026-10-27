import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Fresh SQLite database per test (Postgres is used in production; models are portable)."""
    url = f"sqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("EMITHRAN_DATABASE_URL", url)
    monkeypatch.setenv("EMITHRAN_DATA_ROOT", str(FIX / "mini_memory"))
    monkeypatch.setenv("EMITHRAN_REPLAY_DIR", str(FIX / "replay"))
    monkeypatch.setenv("EMITHRAN_JOBS_DIR", str(tmp_path / "jobs"))
    from emithran import config, db as dbmod

    # never let a developer's local .env (admin token, Go binary path, ...) leak into tests
    monkeypatch.setitem(config.Settings.model_config, "env_file", None)
    config.get_settings.cache_clear()
    dbmod.reset_engine()
    dbmod.init_db()
    yield url
    dbmod.reset_engine()
    config.get_settings.cache_clear()


@pytest.fixture()
def seeded(db):
    from emithran.db import session_scope
    from emithran.registry.service import seed_sources
    from emithran.taxonomy.importer import TaxonomyImporter

    with session_scope() as s:
        seed_sources(s)
        TaxonomyImporter(s, FIX / "mini_memory").run()
    return db
