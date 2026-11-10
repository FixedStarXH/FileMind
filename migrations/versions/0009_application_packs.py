"""Add application-pack planning, ZIP artifacts, and quota reservations.

Revision ID: 0009_application_packs
Revises: 0008_approved_organization
Create Date: 2026-11-11
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009_application_packs"
down_revision = "0008_approved_organization"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_agent_runs_mode", "agent_runs", type_="check")
    op.create_check_constraint(
        "ck_agent_runs_mode", "agent_runs", "mode IN ('qa', 'organize', 'application_pack')"
    )
    op.add_column(
        "plans",
        sa.Column(
            "missing_items",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.create_table(
        "export_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("storage_key", sa.String(length=64), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("state", sa.String(length=24), server_default="queued", nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "manifest",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("size_bytes IS NULL OR size_bytes >= 0"),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'ready', 'failed', 'expired', 'invalidated', 'cancelled')"
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["owner_id", "run_id"], ["agent_runs.owner_id", "agent_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "id"),
        sa.UniqueConstraint("owner_id", "run_id"),
        sa.UniqueConstraint("task_id"),
    )
    op.create_index("ix_exports_owner_created", "export_artifacts", ["owner_id", "created_at", "id"])
    op.create_index("ix_exports_expiry", "export_artifacts", ["state", "expires_at"])
    op.create_table(
        "export_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("export_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_id", "document_id"], ["documents.owner_id", "documents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "export_id"],
            ["export_artifacts.owner_id", "export_artifacts.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "version_id"],
            ["document_versions.owner_id", "document_versions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("export_id", "version_id"),
    )
    op.create_index("ix_export_items_owner_document", "export_items", ["owner_id", "document_id"])
    op.create_table(
        "quota_reservations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("export_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bytes", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(length=16), server_default="reserved", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("bytes > 0"),
        sa.CheckConstraint("state IN ('reserved', 'consumed', 'released')"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["owner_id", "export_id"],
            ["export_artifacts.owner_id", "export_artifacts.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("export_id"),
    )
    op.create_index("ix_quota_reservations_owner_state", "quota_reservations", ["owner_id", "state"])


def downgrade() -> None:
    op.drop_index("ix_quota_reservations_owner_state", table_name="quota_reservations")
    op.drop_table("quota_reservations")
    op.drop_index("ix_export_items_owner_document", table_name="export_items")
    op.drop_table("export_items")
    op.drop_index("ix_exports_expiry", table_name="export_artifacts")
    op.drop_index("ix_exports_owner_created", table_name="export_artifacts")
    op.drop_table("export_artifacts")
    op.drop_column("plans", "missing_items")
    op.drop_constraint("ck_agent_runs_mode", "agent_runs", type_="check")
    op.create_check_constraint("ck_agent_runs_mode", "agent_runs", "mode IN ('qa', 'organize')")
