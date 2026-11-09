"""Private task center APIs."""

import base64
import json
from datetime import UTC, datetime
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from filemind.auth import Auth, WriteAuth
from filemind.database import get_db
from filemind.models import BackgroundTask, Document, DocumentVersion
from filemind.task_contracts import enqueue_task, task_payload

router = APIRouter(prefix="/api/v1", tags=["tasks"])


def _encode_cursor(created_at: datetime, task_id: UUID) -> str:
    payload = json.dumps(
        {"created_at": created_at.isoformat(), "id": str(task_id)}, separators=(",", ":")
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(value: str) -> tuple[datetime, UUID]:
    try:
        padded = value + "=" * (-len(value) % 4)
        payload: object = json.loads(base64.urlsafe_b64decode(padded).decode())
        if not isinstance(payload, dict):
            raise ValueError
        decoded = cast(dict[str, str], payload)
        return datetime.fromisoformat(decoded["created_at"]), UUID(decoded["id"])
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise HTTPException(400, "CURSOR_INVALID") from exc


async def _owned_task(db: AsyncSession, owner_id: UUID, task_id: UUID) -> BackgroundTask:
    task = await db.scalar(
        select(BackgroundTask).where(
            BackgroundTask.id == task_id,
            BackgroundTask.owner_id == owner_id,
        )
    )
    if task is None:
        raise HTTPException(404, "RESOURCE_NOT_FOUND")
    return task


@router.get("/tasks")
async def list_tasks(
    request: Request,
    auth: Auth,
    db: Annotated[AsyncSession, Depends(get_db)],
    kind: str | None = Query(default=None, min_length=1, max_length=32),
    status: Literal["queued", "running", "cancel_requested", "succeeded", "failed", "cancelled"]
    | None = None,
    cursor: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
) -> dict[str, object]:
    statement = select(BackgroundTask).where(BackgroundTask.owner_id == auth[0].id)
    if kind:
        statement = statement.where(BackgroundTask.kind == kind)
    if status:
        statement = statement.where(BackgroundTask.status == status)
    if cursor:
        created_at, task_id = _decode_cursor(cursor)
        statement = statement.where(
            (BackgroundTask.created_at < created_at)
            | ((BackgroundTask.created_at == created_at) & (BackgroundTask.id < task_id))
        )
    tasks = list(
        (
            await db.scalars(
                statement.order_by(
                    BackgroundTask.created_at.desc(), BackgroundTask.id.desc()
                ).limit(limit + 1)
            )
        ).all()
    )
    has_next = len(tasks) > limit
    items = tasks[:limit]
    next_cursor = _encode_cursor(items[-1].created_at, items[-1].id) if has_next else None
    return {
        "data": {"items": [task_payload(task) for task in items], "next_cursor": next_cursor},
        "request_id": request.state.request_id,
    }


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: UUID,
    request: Request,
    auth: Auth,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, object]:
    task = await _owned_task(db, auth[0].id, task_id)
    return {"data": task_payload(task), "request_id": request.state.request_id}


@router.post("/tasks/{task_id}/cancel", status_code=202)
async def cancel_task(
    task_id: UUID,
    request: Request,
    auth: WriteAuth,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, object]:
    task = await db.scalar(
        select(BackgroundTask)
        .where(BackgroundTask.id == task_id, BackgroundTask.owner_id == auth[0].id)
        .with_for_update()
    )
    if task is None:
        raise HTTPException(404, "RESOURCE_NOT_FOUND")
    if task.kind == "document_purge":
        raise HTTPException(409, "TASK_NOT_CANCELLABLE")
    if task.status == "queued":
        task.status = "cancelled"
        task.stage = "cancelled"
        task.cancel_requested = True
        task.updated_at = datetime.now(UTC)
        if task.version_id:
            version = await db.scalar(
                select(DocumentVersion).where(
                    DocumentVersion.id == task.version_id,
                    DocumentVersion.owner_id == auth[0].id,
                )
            )
            if version is not None:
                version.parse_state = "cancelled"
    elif task.status == "running":
        task.status = "cancel_requested"
        task.cancel_requested = True
        task.updated_at = datetime.now(UTC)
    else:
        raise HTTPException(409, "TASK_CONFLICT")
    await db.commit()
    await db.refresh(task)
    return {"data": task_payload(task), "request_id": request.state.request_id}


@router.post("/tasks/{task_id}/retry", status_code=202)
async def retry_task(
    task_id: UUID,
    request: Request,
    auth: WriteAuth,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, object]:
    original = await db.scalar(
        select(BackgroundTask)
        .where(BackgroundTask.id == task_id, BackgroundTask.owner_id == auth[0].id)
        .with_for_update()
    )
    if original is None:
        raise HTTPException(404, "RESOURCE_NOT_FOUND")
    if original.status not in {"failed", "cancelled"}:
        raise HTTPException(409, "TASK_CONFLICT")
    if original.kind not in {"parse", "document_purge"} or original.resource_id is None:
        raise HTTPException(409, "TASK_NOT_RETRYABLE")

    previous_retry = await db.scalar(
        select(BackgroundTask).where(
            BackgroundTask.owner_id == auth[0].id,
            BackgroundTask.retry_of == original.id,
        )
    )
    if previous_retry is not None:
        return {
            "data": task_payload(previous_retry),
            "request_id": request.state.request_id,
        }

    document = await db.scalar(
        select(Document).where(
            Document.id == original.resource_id,
            Document.owner_id == auth[0].id,
        )
    )
    if original.kind == "document_purge":
        if document is None or document.state != "purging":
            raise HTTPException(409, "RESOURCE_NOT_RETRYABLE")
    else:
        if document is None or document.state != "active" or original.version_id is None:
            raise HTTPException(409, "RESOURCE_NOT_RETRYABLE")
        version = await db.scalar(
            select(DocumentVersion).where(
                DocumentVersion.id == original.version_id,
                DocumentVersion.document_id == document.id,
                DocumentVersion.owner_id == auth[0].id,
            )
        )
        if version is None:
            raise HTTPException(409, "RESOURCE_NOT_RETRYABLE")
        version.parse_state = "queued"
    task = enqueue_task(
        db,
        auth[0].id,
        original.kind,
        original.resource_id,
        version_id=original.version_id,
        retry_of=original.id,
    )
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        existing = await db.scalar(
            select(BackgroundTask).where(
                BackgroundTask.owner_id == auth[0].id,
                BackgroundTask.retry_of == original.id,
            )
        )
        if existing is None:
            raise
        task = existing
    await db.refresh(task)
    return {"data": task_payload(task), "request_id": request.state.request_id}
