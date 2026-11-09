"""Persist conversations, cited QA runs, events, and LangGraph checkpoint schema.

Revision ID: 0007_agent_qa
Revises: 0006_hybrid_search
Create Date: 2026-11-09
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_agent_qa"
down_revision = "0006_hybrid_search"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("scope", postgresql.JSONB(), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("owner_id", "id"),
    )
    op.create_index(
        "ix_conversations_owner_updated", "conversations", ["owner_id", "updated_at", "id"]
    )

    op.create_table(
        "agent_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mode", sa.String(24), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("scope", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(24), server_default="queued", nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True)),
        sa.Column("graph_thread_id", sa.String(255), nullable=False, unique=True),
        sa.Column("token_usage", postgresql.JSONB()),
        sa.Column("budget", postgresql.JSONB(), nullable=False),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column("prompt_version", sa.String(80), nullable=False),
        sa.Column("retrieval_version", sa.BigInteger()),
        sa.Column("answer", sa.Text()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("next_event_seq", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["owner_id", "conversation_id"],
            ["conversations.owner_id", "conversations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("owner_id", "id"),
        sa.CheckConstraint("mode IN ('qa')"),
        sa.CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')"),
        sa.CheckConstraint("next_event_seq > 0"),
    )
    op.create_index("ix_agent_runs_owner_created", "agent_runs", ["owner_id", "created_at", "id"])
    op.create_index("ix_agent_runs_owner_status", "agent_runs", ["owner_id", "status"])

    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True)),
        sa.Column("redacted_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "conversation_id"],
            ["conversations.owner_id", "conversations.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("owner_id", "id"),
        sa.CheckConstraint("role IN ('user', 'assistant')"),
    )
    op.create_index(
        "ix_messages_conversation_created", "messages", ["conversation_id", "created_at", "id"]
    )

    op.create_table(
        "run_steps",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("seq", sa.BigInteger(), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_name", sa.String(80), nullable=False),
        sa.Column("safe_input", postgresql.JSONB(), nullable=False),
        sa.Column("safe_output", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "run_id"], ["agent_runs.owner_id", "agent_runs.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint("seq > 0 AND duration_ms >= 0"),
    )
    op.create_index("ix_run_steps_owner_run", "run_steps", ["owner_id", "run_id", "seq"])

    op.create_table(
        "citations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("citation_key", sa.String(8), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True)),
        sa.Column("locator", postgresql.JSONB(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), server_default="available", nullable=False),
        sa.Column("used", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "run_id"], ["agent_runs.owner_id", "agent_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "document_id"], ["documents.owner_id", "documents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "version_id"],
            ["document_versions.owner_id", "document_versions.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("run_id", "citation_key"),
        sa.CheckConstraint("status IN ('available', 'trashed', 'unavailable')"),
    )
    op.create_index("ix_citations_owner_run", "citations", ["owner_id", "run_id"])

    op.create_table(
        "stream_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("event_key", sa.String(160), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "run_id"], ["agent_runs.owner_id", "agent_runs.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("run_id", "seq"),
        sa.UniqueConstraint("run_id", "event_key"),
    )
    op.create_index("ix_stream_events_run_seq", "stream_events", ["run_id", "seq"])
    op.create_index("ix_stream_events_created", "stream_events", ["created_at"])

    op.execute("CREATE SCHEMA IF NOT EXISTS agent_state")


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS agent_state CASCADE")
    op.drop_index("ix_stream_events_created", table_name="stream_events")
    op.drop_index("ix_stream_events_run_seq", table_name="stream_events")
    op.drop_table("stream_events")
    op.drop_index("ix_citations_owner_run", table_name="citations")
    op.drop_table("citations")
    op.drop_index("ix_run_steps_owner_run", table_name="run_steps")
    op.drop_table("run_steps")
    op.drop_index("ix_messages_conversation_created", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_agent_runs_owner_status", table_name="agent_runs")
    op.drop_index("ix_agent_runs_owner_created", table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_index("ix_conversations_owner_updated", table_name="conversations")
    op.drop_table("conversations")
