"""Synchronous private document storage and lifecycle APIs (M2)."""

import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from filemind.auth import Auth, WriteAuth
from filemind.config import get_settings
from filemind.database import get_db
from filemind.http_contracts import (
    check_revision,
    copy_replay_body,
    idempotency_key,
    replay_idempotent,
    request_digest,
    save_idempotency,
)
from filemind.library_schemas import PermanentDelete
from filemind.models import Blob, Document, DocumentVersion, IdempotencyKey, User
from filemind.routes_library import document_payload, owned_document, owned_folder
from filemind.storage import StagedUpload, blob_path, publish_blob, stage_upload

router = APIRouter(prefix="/api/v1", tags=["documents"])
logger = logging.getLogger(__name__)


def version_payload(version: DocumentVersion, blob: Blob) -> dict[str, object]:
    return {
        "id": str(version.id),
        "version_no": version.version_no,
        "original_name": version.original_name,
        "parse_state": version.parse_state,
        "size_bytes": blob.size_bytes,
        "mime": blob.mime,
        "sha256": blob.sha256,
        "created_at": version.created_at.isoformat() if version.created_at else None,
    }


async def _stage(upload: UploadFile) -> StagedUpload:
    config = get_settings()
    return await stage_upload(upload, config.storage_root, config.max_upload_bytes)


async def _locked_user(db: AsyncSession, owner_id: UUID) -> User:
    user = await db.scalar(select(User).where(User.id == owner_id).with_for_update())
    if user is None or not user.is_active:
        raise HTTPException(401, "AUTH_REQUIRED")
    return user


async def _existing_blob(db: AsyncSession, owner_id: UUID, digest: str) -> Blob | None:
    blob = await db.scalar(
        select(Blob).where(Blob.owner_id == owner_id, Blob.sha256 == digest).with_for_update()
    )
    return blob


def _response(
    data: dict[str, object], request_id: str, code: int, headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=code,
        content={"data": data, "request_id": request_id},
        headers=headers,
    )


@router.post("/documents")
async def upload_document(
    request: Request,
    auth: WriteAuth,
    db: Annotated[AsyncSession, Depends(get_db)],
    upload: Annotated[UploadFile, File(alias="file")],
    folder_id: Annotated[UUID | None, Form()] = None,
    idempotency_header: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Response:
    owner_id = auth[0].id
    if folder_id is not None:
        await owned_folder(db, owner_id, folder_id)
    idem = idempotency_key(idempotency_header)
    staged: StagedUpload | None = None
    published_path: str | None = None
    try:
        staged = await _stage(upload)
        digest = request_digest(staged.sha256, staged.original_name, str(folder_id or ""))
        user = await _locked_user(db, owner_id)
        replay = await replay_idempotent(db, owner_id, "POST:/documents", idem, digest)
        if replay is not None:
            staged.path.unlink(missing_ok=True)
            body = copy_replay_body(replay, request.state.request_id)
            await db.commit()
            return JSONResponse(status_code=replay.response_status, content=body)

        duplicate_documents = list(
            (
                await db.scalars(
                    select(Document)
                    .join(
                        DocumentVersion,
                        DocumentVersion.id == Document.current_version_id,
                    )
                    .join(Blob, Blob.id == DocumentVersion.blob_id)
                    .where(
                        Document.owner_id == owner_id,
                        Document.state == "active",
                        Blob.owner_id == owner_id,
                        Blob.sha256 == staged.sha256,
                    )
                    .order_by(Document.created_at)
                    .limit(20)
                )
            ).all()
        )
        if duplicate_documents:
            staged.path.unlink(missing_ok=True)
            data: dict[str, object] = {
                "duplicate": True,
                "documents": [await document_payload(db, item) for item in duplicate_documents],
            }
            body = {"data": data, "request_id": request.state.request_id}
            save_idempotency(
                db,
                owner_id,
                "POST:/documents",
                idem,
                digest,
                200,
                body,
                duplicate_documents[0].id,
            )
            await db.commit()
            return JSONResponse(status_code=200, content=body)

        blob = await _existing_blob(db, owner_id, staged.sha256)
        if (
            blob is None
            and user.used_bytes + user.reserved_bytes + staged.size_bytes > user.quota_bytes
        ):
            raise HTTPException(409, "QUOTA_EXCEEDED")
        if blob is None:
            storage_key, final_path = publish_blob(staged, get_settings().storage_root, owner_id)
            published_path = str(final_path)
            blob = Blob(
                owner_id=owner_id,
                sha256=staged.sha256,
                storage_key=storage_key,
                size_bytes=staged.size_bytes,
                mime=staged.mime,
            )
            db.add(blob)
            user.used_bytes += staged.size_bytes
        else:
            staged.path.unlink(missing_ok=True)

        document_id = uuid4()
        version_id = uuid4()
        document = Document(
            id=document_id,
            owner_id=owner_id,
            folder_id=folder_id,
            title=staged.original_name,
            state="active",
            current_version_id=version_id,
            latest_version_id=version_id,
        )
        version = DocumentVersion(
            id=version_id,
            owner_id=owner_id,
            document_id=document_id,
            blob_id=blob.id if blob.id else uuid4(),
            version_no=1,
            original_name=staged.original_name,
            parse_state="not_started",
        )
        if blob.id is None:
            await db.flush()
        version.blob_id = blob.id
        db.add_all([document, version])
        await db.flush()
        await db.refresh(document)
        await db.refresh(version)
        await db.refresh(blob)
        data = {
            "duplicate": False,
            "document": await document_payload(db, document),
            "version": version_payload(version, blob),
        }
        body = {"data": data, "request_id": request.state.request_id}
        save_idempotency(db, owner_id, "POST:/documents", idem, digest, 201, body, document.id)
        await db.commit()
        return JSONResponse(status_code=201, content=body)
    except HTTPException:
        await db.rollback()
        if staged is not None:
            staged.path.unlink(missing_ok=True)
        if published_path is not None:
            Path(published_path).unlink(missing_ok=True)
        raise
    except Exception:
        await db.rollback()
        if staged is not None:
            staged.path.unlink(missing_ok=True)
        if published_path is not None:
            Path(published_path).unlink(missing_ok=True)
        raise
    finally:
        await upload.close()


@router.post("/documents/{document_id}/versions")
async def upload_version(
    document_id: UUID,
    request: Request,
    auth: WriteAuth,
    db: Annotated[AsyncSession, Depends(get_db)],
    upload: Annotated[UploadFile, File(alias="file")],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    idempotency_header: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Response:
    document = await owned_document(db, auth[0].id, document_id)
    if document.state != "active":
        raise HTTPException(409, "DOCUMENT_NOT_ACTIVE")
    idem = idempotency_key(idempotency_header)
    staged: StagedUpload | None = None
    published_path: str | None = None
    try:
        staged = await _stage(upload)
        digest = request_digest(staged.sha256, staged.original_name, str(document_id))
        user = await _locked_user(db, auth[0].id)
        locked_document = await db.scalar(
            select(Document)
            .where(Document.id == document_id, Document.owner_id == auth[0].id)
            .with_for_update()
        )
        if locked_document is None or locked_document.state != "active":
            raise HTTPException(404, "RESOURCE_NOT_FOUND")
        document = locked_document
        idem_route = f"POST:/documents/{document_id}/versions"
        replay = await replay_idempotent(db, auth[0].id, idem_route, idem, digest)
        if replay is not None:
            staged.path.unlink(missing_ok=True)
            body = copy_replay_body(replay, request.state.request_id)
            await db.commit()
            return JSONResponse(status_code=replay.response_status, content=body)
        check_revision(if_match, document.metadata_revision)
        blob = await _existing_blob(db, auth[0].id, staged.sha256)
        if (
            blob is None
            and user.used_bytes + user.reserved_bytes + staged.size_bytes > user.quota_bytes
        ):
            raise HTTPException(409, "QUOTA_EXCEEDED")
        if blob is None:
            storage_key, final_path = publish_blob(staged, get_settings().storage_root, auth[0].id)
            published_path = str(final_path)
            blob = Blob(
                owner_id=auth[0].id,
                sha256=staged.sha256,
                storage_key=storage_key,
                size_bytes=staged.size_bytes,
                mime=staged.mime,
            )
            db.add(blob)
            user.used_bytes += staged.size_bytes
            await db.flush()
        else:
            staged.path.unlink(missing_ok=True)
        last_version = await db.scalar(
            select(DocumentVersion.version_no)
            .where(DocumentVersion.document_id == document.id)
            .order_by(DocumentVersion.version_no.desc())
            .limit(1)
        )
        version = DocumentVersion(
            id=uuid4(),
            owner_id=auth[0].id,
            document_id=document.id,
            blob_id=blob.id,
            version_no=(last_version or 0) + 1,
            original_name=staged.original_name,
            parse_state="not_started",
        )
        db.add(version)
        document.latest_version_id = version.id
        document.metadata_revision += 1
        document.updated_at = datetime.now(UTC)
        await db.flush()
        await db.refresh(version)
        await db.refresh(blob)
        data = {
            "document": await document_payload(db, document),
            "version": version_payload(version, blob),
        }
        body = {"data": data, "request_id": request.state.request_id}
        save_idempotency(db, auth[0].id, idem_route, idem, digest, 201, body, document.id)
        await db.commit()
        return JSONResponse(
            status_code=201,
            content=body,
            headers={"ETag": f'"{document.metadata_revision}"'},
        )
    except HTTPException:
        await db.rollback()
        if staged is not None:
            staged.path.unlink(missing_ok=True)
        if published_path is not None:
            Path(published_path).unlink(missing_ok=True)
        raise
    except Exception:
        await db.rollback()
        if staged is not None:
            staged.path.unlink(missing_ok=True)
        if published_path is not None:
            Path(published_path).unlink(missing_ok=True)
        raise
    finally:
        await upload.close()


@router.get("/versions/{version_id}/download")
async def download_version(
    version_id: UUID,
    auth: Auth,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FileResponse:
    row = await db.execute(
        select(DocumentVersion, Blob, Document)
        .join(
            Blob, (Blob.id == DocumentVersion.blob_id) & (Blob.owner_id == DocumentVersion.owner_id)
        )
        .join(
            Document,
            (Document.id == DocumentVersion.document_id)
            & (Document.owner_id == DocumentVersion.owner_id),
        )
        .where(
            DocumentVersion.id == version_id,
            DocumentVersion.owner_id == auth[0].id,
            Document.state.in_(("active", "trashed")),
            Blob.state == "active",
        )
    )
    result = row.first()
    if result is None:
        raise HTTPException(404, "RESOURCE_NOT_FOUND")
    version, blob, _document = result
    path = blob_path(get_settings().storage_root, auth[0].id, blob.storage_key)
    if not path.is_file():
        raise HTTPException(410, "SOURCE_UNAVAILABLE")
    return FileResponse(
        path,
        media_type=blob.mime,
        filename=version.original_name,
        content_disposition_type="attachment",
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.delete("/documents/{document_id}/permanent", status_code=status.HTTP_204_NO_CONTENT)
async def permanently_delete_document(
    document_id: UUID,
    body: PermanentDelete,
    auth: WriteAuth,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    owner_id = auth[0].id
    user = await _locked_user(db, owner_id)
    document = await db.scalar(
        select(Document)
        .where(Document.id == document_id, Document.owner_id == owner_id)
        .with_for_update()
    )
    if document is None or document.state != "trashed":
        raise HTTPException(404, "RESOURCE_NOT_FOUND")
    if document.title != body.confirm_title:
        raise HTTPException(400, "CONFIRMATION_MISMATCH")

    versions = list(
        (
            await db.scalars(
                select(DocumentVersion).where(DocumentVersion.document_id == document.id)
            )
        ).all()
    )
    blob_ids = {version.blob_id for version in versions}
    staged_deletes: list[tuple[str, str]] = []
    blobs_to_remove: list[Blob] = []
    try:
        for blob_id in blob_ids:
            blob = await db.scalar(
                select(Blob).where(Blob.id == blob_id, Blob.owner_id == owner_id).with_for_update()
            )
            if blob is None:
                continue
            referenced_elsewhere = await db.scalar(
                select(
                    exists().where(
                        DocumentVersion.owner_id == owner_id,
                        DocumentVersion.blob_id == blob.id,
                        DocumentVersion.document_id != document.id,
                    )
                )
            )
            if referenced_elsewhere:
                continue
            path = blob_path(get_settings().storage_root, owner_id, blob.storage_key)
            if path.exists():
                staging_dir = path.parents[1] / ".staging"
                staging_dir.mkdir(parents=True, exist_ok=True)
                staged_file = staging_dir / f"purge-{uuid4().hex}.part"
                os.replace(path, staged_file)
                staged_deletes.append((str(staged_file), str(path)))
            blobs_to_remove.append(blob)

        await db.execute(
            delete(IdempotencyKey).where(
                IdempotencyKey.owner_id == owner_id,
                (IdempotencyKey.resource_id == document.id)
                | IdempotencyKey.response_body["data"]["documents"].contains(
                    [{"id": str(document.id)}]
                ),
            )
        )
        await db.delete(document)
        await db.flush()
        for blob in blobs_to_remove:
            user.used_bytes = max(0, user.used_bytes - blob.size_bytes)
            await db.delete(blob)
        await db.commit()
    except Exception:
        await db.rollback()
        for staged_file_name, original_file_name in staged_deletes:
            if os.path.exists(staged_file_name):
                os.replace(staged_file_name, original_file_name)
        raise

    for staged_file_name, _original_file_name in staged_deletes:
        try:
            os.unlink(staged_file_name)
        except OSError:
            logger.warning("A permanently deleted blob remains in staging for later cleanup")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
