"""M1 identity and account models."""

from datetime import datetime
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
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


class ExtractedBlock(Base):
    __tablename__ = "extracted_blocks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "version_id"],
            ["document_versions.owner_id", "document_versions.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("version_id", "generation", "ordinal"),
        CheckConstraint("generation > 0 AND ordinal >= 0"),
        Index("ix_extracted_blocks_owner_version", "owner_id", "version_id", "generation"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    locator: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    ocr_confidence: Mapped[float | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "version_id"],
            ["document_versions.owner_id", "document_versions.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("version_id", "generation", "ordinal"),
        CheckConstraint("generation > 0 AND ordinal >= 0 AND token_count > 0"),
        Index("ix_chunks_owner_version_generation", "owner_id", "version_id", "generation"),
        Index("ix_chunks_search_vector", "search_vector", postgresql_using="gin"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(String, nullable=False)
    locator: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    search_text: Mapped[str] = mapped_column(Text, nullable=False)
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR(),
        Computed("to_tsvector('simple', search_text)", persisted=True),
        nullable=False,
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(512))
    embedding_model: Mapped[str | None] = mapped_column(String(128))
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


class BackgroundTask(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["retry_of"], ["tasks.id"], ondelete="SET NULL", deferrable=True),
        UniqueConstraint("retry_of"),
        CheckConstraint(
            "status IN ('queued', 'running', 'cancel_requested', "
            "'succeeded', 'failed', 'cancelled')"
        ),
        CheckConstraint("attempt >= 0 AND fencing_token >= 0 AND dispatch_seq > 0"),
        CheckConstraint("progress_completed IS NULL OR progress_completed >= 0"),
        CheckConstraint("progress_total IS NULL OR progress_total >= 0"),
        Index("ix_tasks_owner_created", "owner_id", "created_at", "id"),
        Index("ix_tasks_status_lease", "status", "lease_until"),
        Index("ix_tasks_resource", "owner_id", "resource_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    version_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(24), default="queued", nullable=False)
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    progress_completed: Mapped[int | None] = mapped_column(Integer)
    progress_total: Mapped[int | None] = mapped_column(Integer)
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fencing_token: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    retry_of: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    dispatch_seq: Mapped[int] = mapped_column(BigInteger, default=1, nullable=False)
    last_enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OutboxEvent(Base):
    __tablename__ = "outbox"
    __table_args__ = (
        UniqueConstraint("event_type", "aggregate_id", "dispatch_seq"),
        CheckConstraint("dispatch_seq > 0 AND attempt >= 0"),
        Index("ix_outbox_ready", "next_attempt_at", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    dispatch_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        UniqueConstraint("owner_id", "id"),
        Index("ix_conversations_owner_updated", "owner_id", "updated_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    scope: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["owner_id", "conversation_id"],
            ["conversations.owner_id", "conversations.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="SET NULL"),
        UniqueConstraint("owner_id", "id"),
        UniqueConstraint("graph_thread_id"),
        CheckConstraint("mode IN ('qa', 'organize', 'application_pack')"),
        CheckConstraint(
            "status IN ('queued', 'running', 'awaiting_approval', 'succeeded', "
            "'failed', 'cancelled', 'expired')"
        ),
        CheckConstraint("next_event_seq > 0"),
        Index("ix_agent_runs_owner_created", "owner_id", "created_at", "id"),
        Index("ix_agent_runs_owner_status", "owner_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    conversation_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    mode: Mapped[str] = mapped_column(String(24), nullable=False)
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="queued", nullable=False)
    task_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    graph_thread_id: Mapped[str] = mapped_column(String(255), nullable=False)
    token_usage: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    budget: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(80), nullable=False)
    retrieval_version: Mapped[int | None] = mapped_column(BigInteger)
    answer: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(64))
    next_event_seq: Mapped[int] = mapped_column(BigInteger, default=1, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "conversation_id"],
            ["conversations.owner_id", "conversations.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("owner_id", "id"),
        CheckConstraint("role IN ('user', 'assistant')"),
        Index("ix_messages_conversation_created", "conversation_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    conversation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    redacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RunStep(Base):
    __tablename__ = "run_steps"
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "run_id"], ["agent_runs.owner_id", "agent_runs.id"], ondelete="CASCADE"
        ),
        CheckConstraint("seq > 0 AND duration_ms >= 0"),
        Index("ix_run_steps_owner_run", "owner_id", "run_id", "seq"),
    )

    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(80), nullable=False)
    safe_input: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    safe_output: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Citation(Base):
    __tablename__ = "citations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "run_id"], ["agent_runs.owner_id", "agent_runs.id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["owner_id", "document_id"], ["documents.owner_id", "documents.id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["owner_id", "version_id"],
            ["document_versions.owner_id", "document_versions.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("run_id", "citation_key"),
        CheckConstraint("status IN ('available', 'trashed', 'unavailable')"),
        Index("ix_citations_owner_run", "owner_id", "run_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    citation_key: Mapped[str] = mapped_column(String(8), nullable=False)
    document_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    chunk_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    locator: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="available", nullable=False)
    used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StreamEvent(Base):
    __tablename__ = "stream_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "run_id"], ["agent_runs.owner_id", "agent_runs.id"], ondelete="CASCADE"
        ),
        UniqueConstraint("run_id", "seq"),
        UniqueConstraint("run_id", "event_key"),
        Index("ix_stream_events_run_seq", "run_id", "seq"),
        Index("ix_stream_events_created", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_key: Mapped[str] = mapped_column(String(160), nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OrganizationPlan(Base):
    __tablename__ = "plans"
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "run_id"], ["agent_runs.owner_id", "agent_runs.id"], ondelete="CASCADE"
        ),
        UniqueConstraint("owner_id", "id"),
        UniqueConstraint("run_id", "revision"),
        CheckConstraint("revision > 0"),
        CheckConstraint(
            "status IN ('awaiting_approval', 'approved', 'rejected', 'stale', "
            "'completed', 'expired', 'cancelled', 'failed')"
        ),
        Index("ix_plans_owner_expiry", "owner_id", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    actions: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    missing_items: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="awaiting_approval", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "plan_id"], ["plans.owner_id", "plans.id"], ondelete="CASCADE"
        ),
        UniqueConstraint("owner_id", "id"),
        UniqueConstraint("plan_id"),
        CheckConstraint("decision IN ('approve', 'reject')"),
        CheckConstraint(
            "execution_status IN ('queued', 'applied', 'failed', 'rejected', 'cancelled')"
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    plan_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    selected_action_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    execution_status: Mapped[str] = mapped_column(String(24), default="queued", nullable=False)
    request_key: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlanActionExecution(Base):
    __tablename__ = "plan_action_executions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "approval_id"], ["approvals.owner_id", "approvals.id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["owner_id", "run_id"], ["agent_runs.owner_id", "agent_runs.id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["owner_id", "document_id"], ["documents.owner_id", "documents.id"], ondelete="CASCADE"
        ),
    )

    approval_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    action_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    document_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    action_type: Mapped[str] = mapped_column(String(24), nullable=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="CASCADE"),
        CheckConstraint("details = details - 'content' - 'quote' - 'text'"),
        Index("ix_audit_events_owner_created", "owner_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    actor_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(40), nullable=False)
    resource_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExportArtifact(Base):
    __tablename__ = "export_artifacts"
    __table_args__ = (
        ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["owner_id", "run_id"], ["agent_runs.owner_id", "agent_runs.id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="SET NULL"),
        UniqueConstraint("owner_id", "id"),
        UniqueConstraint("owner_id", "run_id"),
        UniqueConstraint("task_id"),
        CheckConstraint(
            "state IN ('queued', 'running', 'ready', 'failed', 'expired', "
            "'invalidated', 'cancelled')"
        ),
        CheckConstraint("size_bytes IS NULL OR size_bytes >= 0"),
        Index("ix_exports_owner_created", "owner_id", "created_at", "id"),
        Index("ix_exports_expiry", "state", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    run_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    task_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    storage_key: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(String(24), default="queued", nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    manifest: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExportItem(Base):
    __tablename__ = "export_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "export_id"],
            ["export_artifacts.owner_id", "export_artifacts.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["owner_id", "document_id"], ["documents.owner_id", "documents.id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["owner_id", "version_id"],
            ["document_versions.owner_id", "document_versions.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("export_id", "version_id"),
        Index("ix_export_items_owner_document", "owner_id", "document_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    export_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    document_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)


class QuotaReservation(Base):
    __tablename__ = "quota_reservations"
    __table_args__ = (
        ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["owner_id", "export_id"],
            ["export_artifacts.owner_id", "export_artifacts.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("export_id"),
        CheckConstraint("bytes > 0"),
        CheckConstraint("state IN ('reserved', 'consumed', 'released')"),
        Index("ix_quota_reservations_owner_state", "owner_id", "state"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    export_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="reserved", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
