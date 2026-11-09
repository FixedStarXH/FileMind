"""Add durable task and outbox records.

Revision ID: 0004_durable_tasks
Revises: 0003_document_lifecycle
Create Date: 2026-11-09
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_durable_tasks"
down_revision = "0003_document_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True)),
        sa.Column("version_id", postgresql.UUID(as_uuid=True)),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="queued"),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("progress_completed", sa.Integer()),
        sa.Column("progress_total", sa.Integer()),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(length=64)),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("retry_of", postgresql.UUID(as_uuid=True)),
        sa.Column("dispatch_seq", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("last_enqueued_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["retry_of"], ["tasks.id"], ondelete="SET NULL", deferrable=True),
        sa.UniqueConstraint("retry_of"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'cancel_requested', "
            "'succeeded', 'failed', 'cancelled')"
        ),
        sa.CheckConstraint("attempt >= 0 AND fencing_token >= 0 AND dispatch_seq > 0"),
        sa.CheckConstraint("progress_completed IS NULL OR progress_completed >= 0"),
        sa.CheckConstraint("progress_total IS NULL OR progress_total >= 0"),
    )
    op.create_index("ix_tasks_owner_id", "tasks", ["owner_id"])
    op.create_index("ix_tasks_owner_created", "tasks", ["owner_id", "created_at", "id"])
    op.create_index("ix_tasks_status_lease", "tasks", ["status", "lease_until"])
    op.create_index("ix_tasks_resource", "tasks", ["owner_id", "resource_id"])

    op.create_table(
        "outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dispatch_seq", sa.BigInteger(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("event_type", "aggregate_id", "dispatch_seq"),
        sa.CheckConstraint("dispatch_seq > 0 AND attempt >= 0"),
    )
    op.create_index("ix_outbox_ready", "outbox", ["next_attempt_at", "created_at"])


def downgrade() -> None:
    op.drop_table("outbox")
    op.drop_table("tasks")
