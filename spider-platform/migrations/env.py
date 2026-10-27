"""Alembic environment — URL comes from MIP_DATABASE_URL (same as the service)."""
from alembic import context

from mip import models  # noqa: F401
from mip.config import get_settings
from mip.db import Base, engine

target_metadata = Base.metadata


def run_offline():
    context.configure(url=get_settings().database_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_online():
    with engine().connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata, render_as_batch=conn.dialect.name == "sqlite")
        with context.begin_transaction():
            context.run_migrations()


run_offline() if context.is_offline_mode() else run_online()
