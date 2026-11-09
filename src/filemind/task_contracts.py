"""Task creation and response helpers shared by API routes."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from filemind.models import BackgroundTask, OutboxEvent


def enqueue_task(
    db: AsyncSession,
    owner_id: UUID,
    kind: str,
    resource_id: UUID | None,
    *,
    version_id: UUID | None = None,
    stage: str | None = None,
    retry_of: UUID | None = None,
    available_at: datetime | None = None,
) -> BackgroundTask:
    now = datetime.now(UTC)
    task = BackgroundTask(
        id=uuid4(),
        owner_id=owner_id,
        kind=kind,
        resource_id=resource_id,
        version_id=version_id,
        status="queued",
        stage=stage or ("waiting_parser" if kind == "parse" else "queued"),
        retry_of=retry_of,
        dispatch_seq=1,
        created_at=now,
        updated_at=now,
    )
    db.add(task)
    db.add(
        OutboxEvent(
            event_type="task.queued",
            aggregate_id=task.id,
            dispatch_seq=1,
            payload={"task_id": str(task.id)},
            next_attempt_at=available_at or datetime.now(UTC),
            created_at=now,
        )
    )
    return task


def task_payload(task: BackgroundTask) -> dict[str, Any]:
    progress = None
    if task.progress_completed is not None and task.progress_total is not None:
        progress = {"completed": task.progress_completed, "total": task.progress_total}
    return {
        "id": str(task.id),
        "kind": task.kind,
        "resource_id": str(task.resource_id) if task.resource_id else None,
        "version_id": str(task.version_id) if task.version_id else None,
        "status": task.status,
        "stage": task.stage,
        "progress": progress,
        "attempt": task.attempt,
        "error_code": task.error_code,
        "cancel_requested": task.cancel_requested,
        "retry_of": str(task.retry_of) if task.retry_of else None,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    }
