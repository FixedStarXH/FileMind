"""M1 identity and account models."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from filemind.database import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("quota_bytes >= 0 AND used_bytes >= 0 AND reserved_bytes >= 0"),
        CheckConstraint("used_bytes + reserved_bytes <= quota_bytes"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    quota_bytes: Mapped[int] = mapped_column(BigInteger)
    used_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    reserved_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    index_revision: Mapped[int] = mapped_column(BigInteger, default=0)
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Folder(Base):
    __tablename__ = "folders"
    __table_args__ = (
        ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["owner_id", "parent_id"],
            ["folders.owner_id", "folders.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("owner_id", "id"),
        Index(
            "uq_folders_owner_parent_name",
            "owner_id",
            "parent_id",
            "name",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    parent_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Tag(Base):
    __tablename__ = "tags"
    __table_args__ = (
        ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        UniqueConstraint("owner_id", "name"),
        UniqueConstraint("owner_id", "id"),
        CheckConstraint("color ~ '^#[0-9A-Fa-f]{6}$'"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    color: Mapped[str] = mapped_column(String(7), default="#6c7a70", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Blob(Base):
    __tablename__ = "blobs"
    __table_args__ = (
        ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        UniqueConstraint("owner_id", "id"),
        UniqueConstraint("owner_id", "sha256"),
        UniqueConstraint("owner_id", "storage_key"),
        CheckConstraint("size_bytes >= 0"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime: Mapped[str] = mapped_column(String(127), nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["owner_id", "folder_id"],
            ["folders.owner_id", "folders.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["id", "current_version_id"],
            ["document_versions.document_id", "document_versions.id"],
            name="fk_documents_current_version",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["id", "latest_version_id"],
            ["document_versions.document_id", "document_versions.id"],
            name="fk_documents_latest_version",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        UniqueConstraint("owner_id", "id"),
        CheckConstraint("state IN ('active', 'trashed', 'purging', 'purged')"),
        Index("ix_documents_owner_state_updated", "owner_id", "state", "updated_at"),
        Index("ix_documents_owner_folder_state", "owner_id", "folder_id", "state"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    folder_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    favorite: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    current_version_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    latest_version_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    metadata_revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    trashed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "document_id"],
            ["documents.owner_id", "documents.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["owner_id", "blob_id"], ["blobs.owner_id", "blobs.id"], ondelete="RESTRICT"
        ),
        UniqueConstraint("document_id", "version_no"),
        UniqueConstraint("document_id", "id"),
        UniqueConstraint("owner_id", "id"),
        CheckConstraint("version_no > 0"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    document_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    blob_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    parse_state: Mapped[str] = mapped_column(String(24), default="not_started", nullable=False)
    index_mode: Mapped[str | None] = mapped_column(String(24))
    active_generation: Mapped[int | None] = mapped_column(BigInteger)
    pipeline_version: Mapped[str | None] = mapped_column(String(64))
    page_count: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocumentTag(Base):
    __tablename__ = "document_tags"
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "document_id"],
            ["documents.owner_id", "documents.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["owner_id", "tag_id"], ["tags.owner_id", "tags.id"], ondelete="CASCADE"
        ),
    )

    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    document_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tag_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["owner_id", "resource_id"], ["documents.owner_id", "documents.id"], ondelete="CASCADE"
        ),
    )

    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    route: Mapped[str] = mapped_column(String(160), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    resource_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
