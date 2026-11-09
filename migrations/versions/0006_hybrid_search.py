"""Add versioned keyword and vector chunks.

Revision ID: 0006_hybrid_search
Revises: 0005_document_parsing
Create Date: 2026-11-09
"""

import json
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision = "0006_hybrid_search"
down_revision = "0005_document_parsing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("locator", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple', search_text)", persisted=True),
            nullable=False,
        ),
        sa.Column("embedding", Vector(512)),
        sa.Column("embedding_model", sa.String(length=128)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "version_id"],
            ["document_versions.owner_id", "document_versions.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("version_id", "generation", "ordinal"),
        sa.CheckConstraint("generation > 0 AND ordinal >= 0 AND token_count > 0"),
    )
    op.create_index(
        "ix_chunks_owner_version_generation",
        "chunks",
        ["owner_id", "version_id", "generation"],
    )
    op.create_index("ix_chunks_search_vector", "chunks", ["search_vector"], postgresql_using="gin")

    bind = op.get_bind()
    now = datetime.now(UTC)
    versions = bind.execute(
        sa.text(
            """
            SELECT DISTINCT v.id AS version_id, v.owner_id, v.document_id
            FROM document_versions v
            JOIN documents d ON d.id = v.document_id AND d.owner_id = v.owner_id
            WHERE d.state = 'active'
              AND v.parse_state = 'ready'
              AND v.index_mode IS NULL
              AND (v.id = d.current_version_id OR v.id = d.latest_version_id)
              AND NOT EXISTS (
                SELECT 1 FROM tasks t
                WHERE t.version_id = v.id AND t.owner_id = v.owner_id
                  AND t.kind = 'parse' AND t.status IN ('queued', 'running', 'cancel_requested')
              )
            """
        )
    ).mappings()
    for row in versions:
        task_id = uuid4()
        bind.execute(
            sa.text("UPDATE document_versions SET parse_state='queued' WHERE id=:version_id"),
            {"version_id": row["version_id"]},
        )
        bind.execute(
            sa.text(
                """
                INSERT INTO tasks (id, owner_id, kind, resource_id, version_id, status, stage,
                                   created_at, updated_at)
                VALUES (:id, :owner_id, 'parse', :document_id, :version_id, 'queued',
                        'waiting_parser', :now, :now)
                """
            ),
            {
                "id": task_id,
                "owner_id": row["owner_id"],
                "document_id": row["document_id"],
                "version_id": row["version_id"],
                "now": now,
            },
        )
        bind.execute(
            sa.text(
                """
                INSERT INTO outbox (id, event_type, aggregate_id, dispatch_seq, payload,
                                    next_attempt_at, created_at)
                VALUES (:id, 'task.queued', :task_id, 1, CAST(:payload AS jsonb), :now, :now)
                """
            ),
            {
                "id": uuid4(),
                "task_id": task_id,
                "payload": json.dumps({"task_id": str(task_id)}),
                "now": now,
            },
        )


def downgrade() -> None:
    op.drop_index("ix_chunks_search_vector", table_name="chunks")
    op.drop_index("ix_chunks_owner_version_generation", table_name="chunks")
    op.drop_table("chunks")
