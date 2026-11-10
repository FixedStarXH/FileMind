"""Owner-scoped ZIP export APIs."""

import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from filemind.auth import Auth, WriteAuth
from filemind.config import get_settings
from filemind.database import get_db
from filemind.export_runtime import artifact_path
from filemind.http_contracts import (
    copy_replay_body,
    idempotency_key,
    replay_idempotent,
    request_digest,
    save_idempotency,
)
from filemind.models import (
    AuditEvent,
    BackgroundTask,
    Blob,
    Document,
    DocumentVersion,
    ExportArtifact,
    ExportItem,
    QuotaReservation,
    User,
)
from filemind.task_contracts import enqueue_task, task_payload

router = APIRouter(prefix="/api/v1", tags=["exports"])
_ESTIMATE_OVERHEAD = 1_048_576
_ITEM_OVERHEAD = 4096


class ExportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_ids: list[UUID] = Field(min_length=1, max_length=100)

    @field_validator("version_ids")
    @classmethod
    def unique_versions(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("version ids must be unique")
        return value


def export_payload(artifact: ExportArtifact, task: BackgroundTask | None) -> dict[str, object]:
    return {
        "id": str(artifact.id),
        "run_id": str(artifact.run_id) if artifact.run_id else None,
        "task": task_payload(task) if task else None,
        "state": artifact.state,
        "size_bytes": artifact.size_bytes,
        "expires_at": artifact.expires_at.isoformat(),
        "error_code": artifact.error_code,
        "download_url": f"/api/v1/exports/{artifact.id}/download"
        if artifact.state == "ready"
        else None,
        "created_at": artifact.created_at.isoformat() if artifact.created_at else None,
    }


def _encode_cursor(created_at: datetime, artifact_id: UUID) -> str:
    raw = json.dumps(
        {"created_at": created_at.isoformat(), "id": str(artifact_id)}, separators=(",", ":")
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(value: str) -> tuple[datetime, UUID]:
    try:
        padded = value + "=" * (-len(value) % 4)
        parsed = json.loads(base64.urlsafe_b64decode(padded).decode())
        created_at = datetime.fromisoformat(parsed["created_at"])
        artifact_id = UUID(parsed["id"])
        if created_at.tzinfo is None:
            raise ValueError
        return created_at, artifact_id
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise HTTPException(400, "CURSOR_INVALID") from exc


async def invalidate_document_exports(db: AsyncSession, owner_id: UUID, document_id: UUID) -> int:
    artifacts = list(
        (
            await db.scalars(
                select(ExportArtifact)
                .join(ExportItem, ExportItem.export_id == ExportArtifact.id)
                .where(
                    ExportArtifact.owner_id == owner_id,
                    ExportArtifact.state.in_(("queued", "running", "ready")),
                    ExportItem.owner_id == owner_id,
                    ExportItem.document_id == document_id,
                )
            )
        ).all()
    )
    task_ids = [item.task_id for item in artifacts if item.task_id]
    tasks = (
        {
            task.id: task
            for task in (
                await db.scalars(
                    select(BackgroundTask)
                    .where(BackgroundTask.id.in_(task_ids))
                    .order_by(BackgroundTask.id)
                    .with_for_update()
                )
            ).all()
        }
        if task_ids
        else {}
    )
    artifact_ids = [item.id for item in artifacts]
    if artifact_ids:
        artifacts = list(
            (
                await db.scalars(
                    select(ExportArtifact)
                    .where(ExportArtifact.id.in_(artifact_ids))
                    .order_by(ExportArtifact.id)
                    .with_for_update()
                )
            ).all()
        )
    now = datetime.now(UTC)
    for artifact in artifacts:
        artifact.state = "invalidated"
        artifact.updated_at = now
        task = tasks.get(artifact.task_id) if artifact.task_id else None
        if task is not None and task.status == "queued":
            task.status = "cancelled"
            task.stage = "cancelled"
            task.cancel_requested = True
            task.updated_at = now
            await release_export_reservation(db, artifact)
        elif task is not None and task.status == "running":
            task.status = "cancel_requested"
            task.cancel_requested = True
            task.updated_at = now
    return len(artifacts)


async def release_export_reservation(db: AsyncSession, artifact: ExportArtifact) -> None:
    reservation = await db.scalar(
        select(QuotaReservation).where(QuotaReservation.export_id == artifact.id).with_for_update()
    )
    if reservation is None or reservation.state != "reserved":
        return
    user = await db.scalar(select(User).where(User.id == artifact.owner_id).with_for_update())
    if user is not None:
        user.reserved_bytes = max(0, user.reserved_bytes - reservation.bytes)
    reservation.state = "released"


async def _reserve_export(
    db: AsyncSession,
    owner: User,
    artifact: ExportArtifact,
    items: list[tuple[Document, DocumentVersion, Blob]],
    *,
    manifest: dict[str, object] | None = None,
) -> BackgroundTask:
    settings = get_settings()
    estimate = sum(blob.size_bytes for _document, _version, blob in items)
    estimate += _ESTIMATE_OVERHEAD + len(items) * _ITEM_OVERHEAD
    if estimate > settings.max_export_bytes:
        raise HTTPException(413, "EXPORT_TOO_LARGE")
    if owner.used_bytes + owner.reserved_bytes + estimate > owner.quota_bytes:
        raise HTTPException(409, "QUOTA_EXCEEDED")
    active = await db.scalar(
        select(func.count())
        .select_from(ExportArtifact)
        .where(ExportArtifact.owner_id == owner.id, ExportArtifact.state.in_(("queued", "running")))
    )
    if int(active or 0) >= settings.export_concurrency:
        raise HTTPException(429, "RATE_LIMITED", headers={"Retry-After": "30"})
    now = datetime.now(UTC)
    artifact.manifest = manifest or {"kind": "manual"}
    artifact.expires_at = now + timedelta(days=2)
    task = enqueue_task(db, owner.id, "export", artifact.id, stage="queued")
    artifact.task_id = task.id
    db.add(
        QuotaReservation(
            owner_id=owner.id,
            export_id=artifact.id,
            bytes=estimate,
            state="reserved",
            expires_at=now + timedelta(days=2),
        )
    )
    owner.reserved_bytes += estimate
    for document, version, blob in items:
        db.add(
            ExportItem(
                owner_id=owner.id,
                export_id=artifact.id,
                document_id=document.id,
                version_id=version.id,
                title=document.title,
                original_name=version.original_name,
                sha256=blob.sha256,
                size_bytes=blob.size_bytes,
            )
        )
    db.add(artifact)
    return task


@router.post("/exports", status_code=202, response_model=None)
async def create_export(
    body: ExportCreate,
    request: Request,
    auth: WriteAuth,
    db: Annotated[AsyncSession, Depends(get_db)],
    idempotency_header: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, object] | JSONResponse:
    owner = await db.scalar(select(User).where(User.id == auth[0].id).with_for_update())
    if owner is None:
        raise HTTPException(404, "RESOURCE_NOT_FOUND")
    key = idempotency_key(idempotency_header)
    versions_key = ",".join(sorted(str(item) for item in body.version_ids))
    digest = request_digest(versions_key)
    route = "POST:/exports"
    replay = await replay_idempotent(db, owner.id, route, key, digest)
    if replay is not None:
        response_body = copy_replay_body(replay, request.state.request_id)
        await db.commit()
        return JSONResponse(status_code=replay.response_status, content=response_body)
    rows = await db.execute(
        select(Document, DocumentVersion, Blob)
        .join(
            DocumentVersion,
            (DocumentVersion.document_id == Document.id)
            & (DocumentVersion.owner_id == Document.owner_id),
        )
        .join(
            Blob, (Blob.id == DocumentVersion.blob_id) & (Blob.owner_id == DocumentVersion.owner_id)
        )
        .where(
            Document.owner_id == owner.id,
            Document.id == DocumentVersion.document_id,
            DocumentVersion.id.in_(body.version_ids),
            Document.state == "active",
            Blob.state == "active",
            DocumentVersion.parse_state == "ready",
        )
        .order_by(Document.id)
        .with_for_update(of=Document)
    )
    items = [(row[0], row[1], row[2]) for row in rows.all()]
    if len(items) != len(body.version_ids):
        raise HTTPException(404, "RESOURCE_NOT_FOUND")
    artifact = ExportArtifact(
        id=uuid4(), owner_id=owner.id, run_id=None, state="queued", manifest={}
    )
    task = await _reserve_export(db, owner, artifact, items)
    data = {"export_id": str(artifact.id), "task": task_payload(task)}
    response_body = {"data": data, "request_id": request.state.request_id}
    save_idempotency(db, owner.id, route, key, digest, 202, response_body, items[0][0].id)
    db.add(
        AuditEvent(
            owner_id=owner.id,
            actor_id=owner.id,
            action="export.requested",
            resource_type="export",
            resource_id=artifact.id,
            request_id=request.state.request_id,
            details={"item_count": len(items), "kind": "manual"},
        )
    )
    await db.commit()
    return response_body


@router.get("/exports")
async def list_exports(
    request: Request,
    auth: Auth,
    db: Annotated[AsyncSession, Depends(get_db)],
    run_id: UUID | None = None,
    cursor: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, object]:
    statement = select(ExportArtifact).where(ExportArtifact.owner_id == auth[0].id)
    if run_id is not None:
        statement = statement.where(ExportArtifact.run_id == run_id)
    if cursor is not None:
        created_at, artifact_id = _decode_cursor(cursor)
        statement = statement.where(
            (ExportArtifact.created_at < created_at)
            | ((ExportArtifact.created_at == created_at) & (ExportArtifact.id < artifact_id))
        )
    artifacts = list(
        (
            await db.scalars(
                statement.order_by(
                    ExportArtifact.created_at.desc(), ExportArtifact.id.desc()
                ).limit(limit + 1)
            )
        ).all()
    )
    has_next = len(artifacts) > limit
    page = artifacts[:limit]
    task_ids = [item.task_id for item in page if item.task_id]
    tasks = (
        {
            item.id: item
            for item in (
                await db.scalars(select(BackgroundTask).where(BackgroundTask.id.in_(task_ids)))
            ).all()
        }
        if task_ids
        else {}
    )
    return {
        "data": {
            "items": [
                export_payload(item, tasks.get(item.task_id) if item.task_id else None)
                for item in page
            ],
            "next_cursor": _encode_cursor(page[-1].created_at, page[-1].id)
            if has_next and page
            else None,
        },
        "request_id": request.state.request_id,
    }


@router.get("/exports/{export_id}")
async def get_export(
    export_id: UUID,
    request: Request,
    auth: Auth,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, object]:
    artifact = await db.scalar(
        select(ExportArtifact).where(
            ExportArtifact.id == export_id, ExportArtifact.owner_id == auth[0].id
        )
    )
    if artifact is None:
        raise HTTPException(404, "RESOURCE_NOT_FOUND")
    task = await db.get(BackgroundTask, artifact.task_id) if artifact.task_id else None
    return {"data": export_payload(artifact, task), "request_id": request.state.request_id}


@router.get("/exports/{export_id}/download")
async def download_export(
    export_id: UUID,
    request: Request,
    auth: Auth,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FileResponse:
    artifact = await db.scalar(
        select(ExportArtifact)
        .where(ExportArtifact.id == export_id, ExportArtifact.owner_id == auth[0].id)
        .with_for_update()
    )
    if artifact is None:
        raise HTTPException(404, "RESOURCE_NOT_FOUND")
    now = datetime.now(UTC)
    if artifact.state == "ready" and artifact.expires_at <= now:
        artifact.state = "expired"
        artifact.updated_at = now
        await db.commit()
        raise HTTPException(410, "ARTIFACT_EXPIRED")
    if artifact.state in ("invalidated", "expired", "cancelled"):
        raise HTTPException(
            410, "SOURCE_UNAVAILABLE" if artifact.state == "invalidated" else "ARTIFACT_EXPIRED"
        )
    if artifact.state != "ready" or not artifact.storage_key:
        raise HTTPException(409, "EXPORT_NOT_READY")
    source_count = await db.scalar(
        select(func.count())
        .select_from(ExportItem)
        .join(
            Document,
            (Document.id == ExportItem.document_id) & (Document.owner_id == ExportItem.owner_id),
        )
        .where(
            ExportItem.export_id == artifact.id,
            ExportItem.owner_id == auth[0].id,
            Document.state == "active",
        )
    )
    item_count = await db.scalar(
        select(func.count()).select_from(ExportItem).where(ExportItem.export_id == artifact.id)
    )
    if not item_count or source_count != item_count:
        artifact.state = "invalidated"
        artifact.updated_at = now
        await db.commit()
        raise HTTPException(410, "SOURCE_UNAVAILABLE")
    path = artifact_path(artifact.owner_id, artifact.storage_key)
    if not path.is_file():
        raise HTTPException(410, "SOURCE_UNAVAILABLE")
    db.add(
        AuditEvent(
            owner_id=auth[0].id,
            actor_id=auth[0].id,
            action="export.downloaded",
            resource_type="export",
            resource_id=artifact.id,
            request_id=request.state.request_id,
            details={"size_bytes": artifact.size_bytes or 0},
        )
    )
    await db.commit()
    return FileResponse(
        path,
        media_type="application/zip",
        filename=f"FileMind-{artifact.id.hex[:12]}.zip",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )
