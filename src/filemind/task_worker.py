"""Lease-protected Celery task handlers."""

import logging
import os
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import delete, exists, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from filemind.config import get_settings
from filemind.indexing import INDEX_PIPELINE_VERSION, make_chunks
from filemind.models import (
    BackgroundTask,
    Blob,
    Chunk,
    Document,
    DocumentVersion,
    ExtractedBlock,
    IdempotencyKey,
    OutboxEvent,
    User,
)
from filemind.parsing import PIPELINE_VERSION, ParseFailure, extract_document
from filemind.storage import blob_path

logger = logging.getLogger("filemind.worker")


class TaskCancelled(Exception):
    pass


class LeaseLost(Exception):
    pass


@dataclass(frozen=True)
class ClaimedTask:
    id: UUID
    owner_id: UUID
    kind: str
    resource_id: UUID | None
    version_id: UUID | None
    attempt: int
    fencing_token: int


def _engine() -> Engine:
    url = get_settings().database_url
    if not url:
        raise RuntimeError("DATABASE_URL is required for task workers")
    from sqlalchemy import create_engine

    return create_engine(url, pool_pre_ping=True)


def _claim(engine: Engine, task_id: UUID) -> ClaimedTask | None:
    now = datetime.now(UTC)
    with Session(engine) as session, session.begin():
        task = session.scalar(
            select(BackgroundTask)
            .where(BackgroundTask.id == task_id)
            .with_for_update(skip_locked=True)
        )
        if task is None or task.status != "queued":
            return None
        if task.attempt >= 3:
            task.status = "failed"
            task.stage = "failed"
            task.error_code = task.error_code or "RETRY_LIMIT_REACHED"
            task.updated_at = now
            return None
        task.status = "running"
        task.stage = "starting"
        task.attempt += 1
        task.fencing_token += 1
        task.lease_until = now + timedelta(seconds=get_settings().task_lease_seconds)
        task.heartbeat_at = now
        task.updated_at = now
        session.flush()
        return ClaimedTask(
            id=task.id,
            owner_id=task.owner_id,
            kind=task.kind,
            resource_id=task.resource_id,
            version_id=task.version_id,
            attempt=task.attempt,
            fencing_token=task.fencing_token,
        )


def _heartbeat_loop(engine: Engine, claimed: ClaimedTask, stop: threading.Event) -> None:
    lost = False
    interval = max(1, min(30, get_settings().task_lease_seconds // 3))
    while not stop.wait(interval):
        now = datetime.now(UTC)
        with Session(engine) as session, session.begin():
            session.execute(
                update(BackgroundTask)
                .where(
                    BackgroundTask.id == claimed.id,
                    BackgroundTask.status.in_(("running", "cancel_requested")),
                    BackgroundTask.fencing_token == claimed.fencing_token,
                    BackgroundTask.lease_until > now,
                )
                .values(
                    heartbeat_at=now,
                    lease_until=now + timedelta(seconds=get_settings().task_lease_seconds),
                    updated_at=now,
                )
            )
            renewed = session.scalar(
                select(BackgroundTask.id).where(
                    BackgroundTask.id == claimed.id,
                    BackgroundTask.status.in_(("running", "cancel_requested")),
                    BackgroundTask.fencing_token == claimed.fencing_token,
                    BackgroundTask.lease_until > now,
                )
            )
            if renewed is None:
                lost = True
        if lost:
            logger.warning("task lease lost task_id=%s", claimed.id)
            return


def _is_current_lease(session: Session, claimed: ClaimedTask, now: datetime) -> bool:
    task = session.scalar(
        select(BackgroundTask)
        .where(
            BackgroundTask.id == claimed.id,
            BackgroundTask.status == "running",
            BackgroundTask.fencing_token == claimed.fencing_token,
            BackgroundTask.lease_until > now,
        )
        .with_for_update()
    )
    return task is not None


def _purge_document(engine: Engine, claimed: ClaimedTask) -> None:
    if claimed.resource_id is None:
        raise ValueError("task resource is required")
    storage_root = get_settings().storage_root
    staged_paths: list[tuple[Path, Path]] = []
    try:
        with Session(engine) as session, session.begin():
            now = datetime.now(UTC)
            if not _is_current_lease(session, claimed, now):
                raise RuntimeError("task lease lost")
            task = session.get(BackgroundTask, claimed.id)
            if task is None:
                raise RuntimeError("task not found")
            document = session.scalar(
                select(Document)
                .where(
                    Document.id == claimed.resource_id,
                    Document.owner_id == claimed.owner_id,
                )
                .with_for_update()
            )
            if document is None:
                task.status = "succeeded"
                task.stage = "completed"
                task.lease_until = None
                task.heartbeat_at = None
                task.updated_at = now
                return
            if document.state != "purging":
                raise ValueError("document is not purging")

            active_parse = session.scalar(
                select(
                    exists().where(
                        BackgroundTask.owner_id == claimed.owner_id,
                        BackgroundTask.kind == "parse",
                        BackgroundTask.resource_id == document.id,
                        BackgroundTask.status.in_(("running", "cancel_requested")),
                        BackgroundTask.lease_until > now,
                    )
                )
            )
            if active_parse:
                raise RuntimeError("document parse worker has not released its lease")

            user = session.scalar(select(User).where(User.id == claimed.owner_id).with_for_update())
            if user is None:
                raise RuntimeError("owner not found")
            versions = list(
                session.scalars(
                    select(DocumentVersion).where(
                        DocumentVersion.document_id == document.id,
                        DocumentVersion.owner_id == claimed.owner_id,
                    )
                )
            )
            blob_ids = {version.blob_id for version in versions}
            blobs_to_remove: list[Blob] = []
            for blob_id in blob_ids:
                blob = session.scalar(
                    select(Blob)
                    .where(Blob.id == blob_id, Blob.owner_id == claimed.owner_id)
                    .with_for_update()
                )
                if blob is None:
                    continue
                shared = session.scalar(
                    select(
                        exists().where(
                            DocumentVersion.owner_id == claimed.owner_id,
                            DocumentVersion.blob_id == blob.id,
                            DocumentVersion.document_id != document.id,
                        )
                    )
                )
                if shared:
                    continue
                source = blob_path(storage_root, claimed.owner_id, blob.storage_key)
                if source.exists():
                    staging_dir = source.parents[1] / ".staging"
                    staging_dir.mkdir(parents=True, exist_ok=True)
                    staged = staging_dir / f"purge-{uuid4().hex}.part"
                    os.replace(source, staged)
                    staged_paths.append((staged, source))
                blobs_to_remove.append(blob)

            session.execute(
                delete(IdempotencyKey).where(
                    IdempotencyKey.owner_id == claimed.owner_id,
                    (IdempotencyKey.resource_id == document.id)
                    | IdempotencyKey.response_body["data"]["documents"].contains(
                        [{"id": str(document.id)}]
                    ),
                )
            )
            session.delete(document)
            session.flush()
            for blob in blobs_to_remove:
                user.used_bytes = max(0, user.used_bytes - blob.size_bytes)
                session.delete(blob)

            task.status = "succeeded"
            task.stage = "completed"
            task.error_code = None
            task.lease_until = None
            task.heartbeat_at = None
            task.updated_at = now
    except Exception:
        for staged, source in staged_paths:
            if staged.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged, source)
        raise

    for staged, _source in staged_paths:
        try:
            staged.unlink(missing_ok=True)
        except OSError:
            logger.warning("purged blob remains in staging task_id=%s", claimed.id)


def _finish_generic(engine: Engine, claimed: ClaimedTask) -> None:
    now = datetime.now(UTC)
    with Session(engine) as session, session.begin():
        task = session.scalar(
            select(BackgroundTask)
            .where(
                BackgroundTask.id == claimed.id,
                BackgroundTask.status == "running",
                BackgroundTask.fencing_token == claimed.fencing_token,
                BackgroundTask.lease_until > now,
            )
            .with_for_update()
        )
        if task is None:
            return
        task.status = "succeeded"
        task.stage = "completed"
        task.lease_until = None
        task.heartbeat_at = None
        task.updated_at = now


def _parse_document(engine: Engine, claimed: ClaimedTask) -> None:
    if claimed.resource_id is None or claimed.version_id is None:
        raise ParseFailure("INVALID_TASK")
    settings = get_settings()
    with Session(engine) as session:
        row = session.execute(
            select(DocumentVersion, Blob, Document)
            .join(
                Blob,
                (Blob.id == DocumentVersion.blob_id) & (Blob.owner_id == DocumentVersion.owner_id),
            )
            .join(
                Document,
                (Document.id == DocumentVersion.document_id)
                & (Document.owner_id == DocumentVersion.owner_id),
            )
            .where(
                DocumentVersion.id == claimed.version_id,
                DocumentVersion.document_id == claimed.resource_id,
                DocumentVersion.owner_id == claimed.owner_id,
                Document.state == "active",
                Blob.state == "active",
            )
        ).first()
        if row is None:
            raise ParseFailure("RESOURCE_UNAVAILABLE")
        version, blob, _document = row
        generation = (version.active_generation or 0) + 1
        source = blob_path(settings.storage_root, claimed.owner_id, blob.storage_key)
        suffix = Path(version.original_name).suffix.casefold()

    def update_stage(stage: str, completed: int, total: int | None) -> None:
        now = datetime.now(UTC)
        cancelled = False
        with Session(engine) as session, session.begin():
            task = session.scalar(
                select(BackgroundTask)
                .where(
                    BackgroundTask.id == claimed.id,
                    BackgroundTask.status.in_(("running", "cancel_requested")),
                    BackgroundTask.fencing_token == claimed.fencing_token,
                    BackgroundTask.lease_until > now,
                )
                .with_for_update()
            )
            version_row = session.scalar(
                select(DocumentVersion)
                .where(
                    DocumentVersion.id == claimed.version_id,
                    DocumentVersion.owner_id == claimed.owner_id,
                )
                .with_for_update()
            )
            if task is None or version_row is None:
                raise LeaseLost("task lease lost")
            if task.cancel_requested or task.status == "cancel_requested":
                task.status = "cancelled"
                task.stage = "cancelled"
                task.lease_until = None
                task.heartbeat_at = None
                task.updated_at = now
                version_row.parse_state = "cancelled"
                cancelled = True
            else:
                task.stage = stage
                task.progress_completed = completed
                task.progress_total = total
                task.updated_at = now
                version_row.parse_state = stage if stage in {"ocr", "indexing"} else "extracting"
        if cancelled:
            raise TaskCancelled("task cancelled")

    # A first checkpoint both validates the lease and exposes a useful stage before parsing.
    update_stage("extracting", 0, None)
    blocks, page_count = extract_document(source, suffix, settings.max_pdf_pages, update_stage)
    update_stage("indexing", 0, None)
    extracted_rows = [
        ExtractedBlock(
            owner_id=claimed.owner_id,
            version_id=claimed.version_id,
            generation=generation,
            ordinal=ordinal,
            kind=block.kind,
            text=block.text,
            locator=block.locator,
            ocr_confidence=block.ocr_confidence,
        )
        for ordinal, block in enumerate(blocks)
    ]
    chunk_rows = make_chunks(claimed.owner_id, claimed.version_id, generation, extracted_rows)
    update_stage("indexing", len(chunk_rows), len(chunk_rows))
    now = datetime.now(UTC)
    with Session(engine) as session, session.begin():
        if not _is_current_lease(session, claimed, now):
            raise LeaseLost("task lease lost")
        task = session.get(BackgroundTask, claimed.id)
        version_row = session.scalar(
            select(DocumentVersion)
            .where(
                DocumentVersion.id == claimed.version_id,
                DocumentVersion.owner_id == claimed.owner_id,
            )
            .with_for_update()
        )
        document = session.scalar(
            select(Document)
            .where(Document.id == claimed.resource_id, Document.owner_id == claimed.owner_id)
            .with_for_update()
        )
        if task is None or task.cancel_requested or task.status != "running":
            if task is not None and task.status == "cancel_requested":
                task.status = "cancelled"
                task.stage = "cancelled"
                task.lease_until = None
                task.heartbeat_at = None
                task.updated_at = now
                if version_row is not None:
                    version_row.parse_state = "cancelled"
                return
            raise LeaseLost("task lease lost")
        if version_row is None or document is None or document.state != "active":
            raise ParseFailure("RESOURCE_UNAVAILABLE")
        session.execute(
            delete(ExtractedBlock).where(
                ExtractedBlock.owner_id == claimed.owner_id,
                ExtractedBlock.version_id == claimed.version_id,
                ExtractedBlock.generation == generation,
            )
        )
        session.execute(
            delete(Chunk).where(
                Chunk.owner_id == claimed.owner_id,
                Chunk.version_id == claimed.version_id,
                Chunk.generation == generation,
            )
        )
        session.add_all([*extracted_rows, *chunk_rows])
        version_row.page_count = page_count if page_count > 0 else None
        version_row.active_generation = generation
        version_row.pipeline_version = f"{PIPELINE_VERSION}+{INDEX_PIPELINE_VERSION}"
        version_row.parse_state = "ready"
        version_row.error_code = None
        version_row.index_mode = (
            "hybrid"
            if chunk_rows and all(chunk.embedding is not None for chunk in chunk_rows)
            else "keyword_only"
        )
        if document.latest_version_id == version_row.id:
            document.current_version_id = version_row.id
            document.updated_at = now
            user = session.scalar(select(User).where(User.id == claimed.owner_id).with_for_update())
            if user is None:
                raise ParseFailure("RESOURCE_UNAVAILABLE")
            user.index_revision += 1
        task.status = "succeeded"
        task.stage = "completed"
        task.progress_completed = len(chunk_rows)
        task.progress_total = len(chunk_rows)
        task.error_code = None
        task.lease_until = None
        task.heartbeat_at = None
        task.updated_at = now


def _finish_parse_failure(engine: Engine, claimed: ClaimedTask, code: str) -> None:
    now = datetime.now(UTC)
    with Session(engine) as session, session.begin():
        task = session.scalar(
            select(BackgroundTask)
            .where(
                BackgroundTask.id == claimed.id,
                BackgroundTask.status.in_(("running", "cancel_requested")),
                BackgroundTask.fencing_token == claimed.fencing_token,
            )
            .with_for_update()
        )
        if task is None:
            return
        if task.cancel_requested or task.status == "cancel_requested":
            task.status = "cancelled"
            task.stage = "cancelled"
            version_state = "cancelled"
        else:
            task.status = "failed"
            task.stage = "failed"
            task.error_code = code
            version_state = "failed"
        task.lease_until = None
        task.heartbeat_at = None
        task.updated_at = now
        if claimed.version_id:
            version = session.scalar(
                select(DocumentVersion)
                .where(
                    DocumentVersion.id == claimed.version_id,
                    DocumentVersion.owner_id == claimed.owner_id,
                )
                .with_for_update()
            )
            if version is not None:
                version.parse_state = version_state
                version.error_code = code if version_state == "failed" else None


def _fail_or_retry(engine: Engine, claimed: ClaimedTask) -> None:
    now = datetime.now(UTC)
    with Session(engine) as session, session.begin():
        task = session.scalar(
            select(BackgroundTask)
            .where(
                BackgroundTask.id == claimed.id,
                BackgroundTask.status == "running",
                BackgroundTask.fencing_token == claimed.fencing_token,
            )
            .with_for_update()
        )
        if task is None:
            return
        task.lease_until = None
        task.heartbeat_at = None
        task.error_code = "TASK_EXECUTION_FAILED"
        task.updated_at = now
        if task.attempt >= 3:
            task.status = "failed"
            task.stage = "failed"
            if claimed.version_id:
                version = session.scalar(
                    select(DocumentVersion)
                    .where(
                        DocumentVersion.id == claimed.version_id,
                        DocumentVersion.owner_id == claimed.owner_id,
                    )
                    .with_for_update()
                )
                if version is not None:
                    version.parse_state = "failed"
                    version.error_code = task.error_code
            return
        task.status = "queued"
        task.stage = "retry_wait"
        if claimed.version_id:
            version = session.scalar(
                select(DocumentVersion)
                .where(
                    DocumentVersion.id == claimed.version_id,
                    DocumentVersion.owner_id == claimed.owner_id,
                )
                .with_for_update()
            )
            if version is not None:
                version.parse_state = "queued"
        task.dispatch_seq += 1
        delay = min(60, 2**task.attempt)
        due = now + timedelta(seconds=delay)
        task.last_enqueued_at = due
        session.add(
            OutboxEvent(
                event_type="task.queued",
                aggregate_id=task.id,
                dispatch_seq=task.dispatch_seq,
                payload={"task_id": str(task.id)},
                next_attempt_at=due,
            )
        )


def execute_task(task_id: str) -> None:
    engine = _engine()
    try:
        claimed = _claim(engine, UUID(task_id))
        if claimed is None:
            return
        stop = threading.Event()
        heartbeat = threading.Thread(
            target=_heartbeat_loop,
            args=(engine, claimed, stop),
            name=f"task-heartbeat-{claimed.id}",
            daemon=True,
        )
        heartbeat.start()
        try:
            if claimed.kind == "document_purge":
                _purge_document(engine, claimed)
            elif claimed.kind == "parse":
                _parse_document(engine, claimed)
            else:
                raise ValueError("task handler is not available")
        except ParseFailure as exc:
            logger.warning("document parse failed task_id=%s code=%s", claimed.id, exc.code)
            _finish_parse_failure(engine, claimed, exc.code)
        except TaskCancelled:
            logger.info("task cancelled task_id=%s", claimed.id)
        except LeaseLost:
            logger.warning("task lease lost task_id=%s", claimed.id)
        except Exception:
            logger.error("task execution failed task_id=%s kind=%s", claimed.id, claimed.kind)
            _fail_or_retry(engine, claimed)
        finally:
            stop.set()
            heartbeat.join(timeout=2)
    finally:
        engine.dispose()
