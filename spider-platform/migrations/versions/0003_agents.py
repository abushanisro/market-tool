"""agents: runs and findings

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-28 02:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("trigger", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("mode", sa.String(10), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("agents", sa.JSON(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text()),
    )
    op.create_index("ix_agent_runs_status", "agent_runs", ["status"])
    op.create_index("ix_agent_runs_started_at", "agent_runs", ["started_at"])
    op.create_table(
        "agent_findings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agent", sa.String(40), nullable=False),
        sa.Column("key", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("entities", sa.JSON(), nullable=False),
        sa.Column("forecast", sa.JSON()),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("analysis", sa.JSON(), nullable=False),
        sa.Column("contested", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("run_id", sa.Integer()),
        sa.UniqueConstraint("agent", "key"),
    )
    op.create_index("ix_agent_findings_agent", "agent_findings", ["agent"])
    op.create_index("ix_agent_findings_status", "agent_findings", ["status"])
    op.create_index("ix_agent_findings_last_seen", "agent_findings", ["last_seen"])
    op.create_index("ix_findings_status_sev", "agent_findings", ["status", "severity"])


def downgrade() -> None:
    op.drop_table("agent_findings")
    op.drop_table("agent_runs")
