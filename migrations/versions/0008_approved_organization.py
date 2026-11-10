"""Persist organization proposals, approvals, execution records, and audit events.

Revision ID: 0008_approved_organization
Revises: 0007_agent_qa
Create Date: 2026-11-10
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_approved_organization"
down_revision = "0007_agent_qa"
branch_labels = None
depends_on = None


def _replace_check(table: str, column: str, expression: str) -> None:
    bind = op.get_bind()
    for constraint in sa.inspect(bind).get_check_constraints(table):
        if constraint.get("sqltext") and column in constraint["sqltext"]:
            op.drop_constraint(constraint["name"], table, type_="check")
    op.create_check_constraint(f"ck_{table}_{column}", table, expression)


def upgrade() -> None:
    _replace_check("agent_runs", "mode", "mode IN ('qa', 'organize')")
    _replace_check(
        "agent_runs",
        "status",
        "status IN ('queued', 'running', 'awaiting_approval', 'succeeded', 'failed', "
        "'cancelled', 'expired')",
    )
    op.alter_column("agent_runs", "conversation_id", nullable=True)

    op.create_table(
        "plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("actions", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(24), server_default="awaiting_approval", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "run_id"], ["agent_runs.owner_id", "agent_runs.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("owner_id", "id"),
        sa.UniqueConstraint("run_id", "revision"),
        sa.CheckConstraint("revision > 0"),
        sa.CheckConstraint(
            "status IN ('awaiting_approval', 'approved', 'rejected', 'stale', "
            "'completed', 'expired', 'cancelled', 'failed')"
        ),
    )
    op.create_index("ix_plans_owner_expiry", "plans", ["owner_id", "expires_at"])

    op.create_table(
        "approvals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("selected_action_ids", postgresql.JSONB(), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("execution_status", sa.String(24), server_default="queued", nullable=False),
        sa.Column("request_key", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_body", postgresql.JSONB(), nullable=False),
        sa.Column(
            "decided_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["owner_id", "plan_id"], ["plans.owner_id", "plans.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("owner_id", "id"),
        sa.UniqueConstraint("plan_id"),
        sa.CheckConstraint("decision IN ('approve', 'reject')"),
        sa.CheckConstraint(
            "execution_status IN ('queued', 'applied', 'failed', 'rejected', 'cancelled')"
        ),
    )

    op.create_table(
        "plan_action_executions",
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approval_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_id", sa.String(64), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_type", sa.String(24), nullable=False),
        sa.Column(
            "applied_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("approval_id", "action_id"),
        sa.ForeignKeyConstraint(
            ["owner_id", "approval_id"], ["approvals.owner_id", "approvals.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "run_id"], ["agent_runs.owner_id", "agent_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "document_id"], ["documents.owner_id", "documents.id"], ondelete="CASCADE"
        ),
    )

    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("resource_type", sa.String(40), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", sa.String(64)),
        sa.Column(
            "details", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="CASCADE"),
        sa.CheckConstraint("details = details - 'content' - 'quote' - 'text'"),
    )
    op.create_index(
        "ix_audit_events_owner_created", "audit_events", ["owner_id", "created_at", "id"]
    )


def downgrade() -> None:
    op.drop_index("ix_audit_events_owner_created", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_table("plan_action_executions")
    op.drop_table("approvals")
    op.drop_index("ix_plans_owner_expiry", table_name="plans")
    op.drop_table("plans")
    op.drop_constraint("ck_agent_runs_status", "agent_runs", type_="check")
    op.drop_constraint("ck_agent_runs_mode", "agent_runs", type_="check")
    op.alter_column("agent_runs", "conversation_id", nullable=False)
    op.create_check_constraint("ck_agent_runs_mode", "agent_runs", "mode IN ('qa')")
    op.create_check_constraint(
        "ck_agent_runs_status",
        "agent_runs",
        "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
    )
