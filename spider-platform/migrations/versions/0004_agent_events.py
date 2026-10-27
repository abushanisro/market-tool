"""agent events: append-only history of flag changes

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-28 03:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_id", sa.Integer()),
        sa.Column("finding_id", sa.Integer()),
        sa.Column("agent", sa.String(40), nullable=False),
        sa.Column("event", sa.String(16), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("prev_severity", sa.String(10)),
    )
    for c in ("ts", "run_id", "finding_id", "agent"):
        op.create_index(f"ix_agent_events_{c}", "agent_events", [c])


def downgrade() -> None:
    op.drop_table("agent_events")
