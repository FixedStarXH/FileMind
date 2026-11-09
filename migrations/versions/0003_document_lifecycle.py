"""Create private document library and content-addressed blob storage.

Revision ID: 0003_document_lifecycle
Revises: 0002_accounts
Create Date: 2026-11-09
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_document_lifecycle"
down_revision = "0002_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "folders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True)),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["owner_id", "parent_id"],
            ["folders.owner_id", "folders.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("owner_id", "id"),
    )
    op.create_index("ix_folders_owner_id", "folders", ["owner_id"])
    op.create_index(
        "uq_folders_owner_parent_name",
        "folders",
        ["owner_id", "parent_id", "name"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )

    op.create_table(
        "tags",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("color", sa.String(length=7), nullable=False, server_default="#6c7a70"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("owner_id", "name"),
        sa.UniqueConstraint("owner_id", "id"),
        sa.CheckConstraint("color ~ '^#[0-9A-Fa-f]{6}$'"),
    )
    op.create_index("ix_tags_owner_id", "tags", ["owner_id"])

    op.create_table(
        "blobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("mime", sa.String(length=127), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("owner_id", "id"),
        sa.UniqueConstraint("owner_id", "sha256"),
        sa.UniqueConstraint("owner_id", "storage_key"),
        sa.CheckConstraint("size_bytes >= 0"),
    )
    op.create_index("ix_blobs_owner_id", "blobs", ["owner_id"])

    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("folder_id", postgresql.UUID(as_uuid=True)),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("favorite", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True)),
        sa.Column("latest_version_id", postgresql.UUID(as_uuid=True)),
        sa.Column("metadata_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("trashed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["owner_id", "folder_id"], ["folders.owner_id", "folders.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("owner_id", "id"),
        sa.CheckConstraint("state IN ('active', 'trashed', 'purging', 'purged')"),
    )
    op.create_index("ix_documents_owner_id", "documents", ["owner_id"])
    op.create_index(
        "ix_documents_owner_state_updated", "documents", ["owner_id", "state", "updated_at"]
    )
    op.create_index(
        "ix_documents_owner_folder_state", "documents", ["owner_id", "folder_id", "state"]
    )

    op.create_table(
        "document_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("blob_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column(
            "parse_state", sa.String(length=24), nullable=False, server_default="not_started"
        ),
        sa.Column("index_mode", sa.String(length=24)),
        sa.Column("active_generation", sa.BigInteger()),
        sa.Column("pipeline_version", sa.String(length=64)),
        sa.Column("page_count", sa.Integer()),
        sa.Column("error_code", sa.String(length=64)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "document_id"], ["documents.owner_id", "documents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "blob_id"], ["blobs.owner_id", "blobs.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("document_id", "version_no"),
        sa.UniqueConstraint("document_id", "id"),
        sa.UniqueConstraint("owner_id", "id"),
        sa.CheckConstraint("version_no > 0"),
    )
    op.create_index("ix_document_versions_owner_id", "document_versions", ["owner_id"])
    op.create_index("ix_document_versions_document_id", "document_versions", ["document_id"])
    op.create_foreign_key(
        "fk_documents_current_version",
        "documents",
        "document_versions",
        ["id", "current_version_id"],
        ["document_id", "id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_documents_latest_version",
        "documents",
        "document_versions",
        ["id", "latest_version_id"],
        ["document_id", "id"],
        deferrable=True,
        initially="DEFERRED",
    )

    op.create_table(
        "document_tags",
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tag_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.ForeignKeyConstraint(
            ["owner_id", "document_id"], ["documents.owner_id", "documents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "tag_id"], ["tags.owner_id", "tags.id"], ondelete="CASCADE"
        ),
    )
    op.create_index("ix_document_tags_owner_tag", "document_tags", ["owner_id", "tag_id"])

    op.create_table(
        "idempotency_keys",
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("route", sa.String(length=160), primary_key=True),
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True)),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_body", postgresql.JSONB(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["owner_id", "resource_id"], ["documents.owner_id", "documents.id"], ondelete="CASCADE"
        ),
    )
    op.create_index("ix_idempotency_keys_expires_at", "idempotency_keys", ["expires_at"])


def downgrade() -> None:
    op.drop_table("idempotency_keys")
    op.drop_table("document_tags")
    op.drop_constraint("fk_documents_current_version", "documents", type_="foreignkey")
    op.drop_constraint("fk_documents_latest_version", "documents", type_="foreignkey")
    op.drop_table("document_versions")
    op.drop_table("documents")
    op.drop_table("blobs")
    op.drop_table("tags")
    op.drop_table("folders")
