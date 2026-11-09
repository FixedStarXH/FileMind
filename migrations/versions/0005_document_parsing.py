"""Persist extracted document blocks for previews and future indexing.

Revision ID: 0005_document_parsing
Revises: 0004_durable_tasks
Create Date: 2026-11-09
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_document_parsing"
down_revision = "0004_durable_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "extracted_blocks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("locator", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("ocr_confidence", sa.Float()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "version_id"],
            ["document_versions.owner_id", "document_versions.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("version_id", "generation", "ordinal"),
        sa.CheckConstraint("generation > 0 AND ordinal >= 0"),
    )
    op.create_index(
        "ix_extracted_blocks_owner_version",
        "extracted_blocks",
        ["owner_id", "version_id", "generation"],
    )


def downgrade() -> None:
    op.drop_index("ix_extracted_blocks_owner_version", table_name="extracted_blocks")
    op.drop_table("extracted_blocks")
