"""Service configuration.

All settings come from environment variables (prefix ``MIP_``) or a ``.env`` file.
Credentials are never stored in the database: sources reference an environment
variable *name* (e.g. ``MIP_KEY_COMTRADE``) and the Settings screen only reports
whether that variable is set.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MIP_", env_file=".env", extra="ignore")

    # Storage ---------------------------------------------------------------
    database_url: str = Field(
        default=f"sqlite:///{PROJECT_ROOT / 'var' / 'mip.db'}",
        description="SQLAlchemy URL. Production: postgresql+psycopg://user:pass@host/db",
    )
    redis_url: str | None = Field(default=None, description="Optional; enables distributed scheduler lock + job events")

    # Existing manufacturing taxonomy (the `memory/` folder) ------------------
    data_root: Path = Field(default=PROJECT_ROOT.parent / "memory")

    # Crawling --------------------------------------------------------------
    user_agent: str = "EmithranMIP-Spider/1.0 (+https://www.emithran.com; manufacturing market research)"
    contact_email: str | None = Field(default=None, description="Added to polite-pool APIs (Crossref/OpenAlex mailto)")
    jobs_dir: Path = Field(default=PROJECT_ROOT / "var" / "jobs")
    max_parallel_jobs: int = 2
    global_max_pages_per_job: int = 5000
    default_download_delay: float = 1.0
    default_concurrency: int = 2
    job_timeout_minutes: int = 60
    stale_heartbeat_seconds: int = 180
    auto_pause_after_failures: int = 5
    # Go fetch worker (sources with extraction.fetch_engine = "go"); unset → those sources fall back to Scrapy
    go_fetcher_bin: Path | None = Field(default=None, description="Path to the mip-fetch binary")
    go_fetcher_api: str = Field(default="http://127.0.0.1:8000", description="Control-plane URL the Go worker calls")
    # Replay mode: serve recorded responses for real URLs (tests / offline demo).
    replay_dir: Path | None = None

    # Worker ----------------------------------------------------------------
    embedded_worker: bool = Field(default=False, description="Run scheduler/dispatcher inside the API process (dev)")
    scheduler_tick_seconds: int = 30
    dispatcher_tick_seconds: int = 5

    # API -------------------------------------------------------------------
    admin_token: str | None = Field(default=None, description="If set, mutating API calls require X-Admin-Token")
    cors_origins: list[str] = ["*"]

    def credential_present(self, env_name: str | None) -> bool:
        return bool(env_name and os.environ.get(env_name))


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.jobs_dir.mkdir(parents=True, exist_ok=True)
    if s.database_url.startswith("sqlite:///"):
        Path(s.database_url.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True)
    return s
