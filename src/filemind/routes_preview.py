"""Authenticated structured previews and inline PDF, image, and slide content."""

from collections.abc import Iterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from filemind.auth import Auth, WriteAuth
from filemind.config import get_settings
from filemind.database import get_db
from filemind.models import BackgroundTask, Blob, Document, DocumentVersion, ExtractedBlock
from filemind.presentation_render import rendered_pdf_path
from filemind.slide_preview import slide_image_png, slide_layout
from filemind.storage import blob_path
from filemind.task_contracts import enqueue_task, task_payload

router = APIRouter(prefix="/api/v1", tags=["preview"])
_INLINE_MIME = {"application/pdf", "image/png", "image/jpeg"}


@router.post("/versions/{version_id}/reprocess", status_code=202)
async def reprocess_version(
    version_id: UUID,
    request: Request,
    auth: WriteAuth,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, object]:
    row = await db.execute(
        select(DocumentVersion, Document)
        .join(
            Document,
            (Document.id == DocumentVersion.document_id)
            & (Document.owner_id == DocumentVersion.owner_id),
        )
        .where(
            DocumentVersion.id == version_id,
            DocumentVersion.owner_id == auth[0].id,
            Document.state == "active",
        )
        .with_for_update(of=DocumentVersion)
    )
    result = row.first()
    if result is None:
        raise HTTPException(404, "RESOURCE_NOT_FOUND")
    version, document = result
    active = await db.scalar(
        select(BackgroundTask.id).where(
            BackgroundTask.owner_id == auth[0].id,
            BackgroundTask.kind == "parse",
            BackgroundTask.version_id == version.id,
            BackgroundTask.status.in_(("queued", "running", "cancel_requested")),
        )
    )
    if active is not None:
        raise HTTPException(409, "TASK_CONFLICT")
    version.parse_state = "queued"
    version.error_code = None
    task = enqueue_task(db, auth[0].id, "parse", document.id, version_id=version.id)
    await db.commit()
    await db.refresh(task)
    return {"data": {"task": task_payload(task)}, "request_id": request.state.request_id}


async def _owned_version(
    db: AsyncSession, owner_id: UUID, version_id: UUID
) -> tuple[DocumentVersion, Blob, Document]:
    result = await db.execute(
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
            DocumentVersion.owner_id == owner_id,
            Document.state.in_(("active", "trashed")),
            Blob.state == "active",
        )
    )
    row = result.first()
    if row is None:
        raise HTTPException(404, "RESOURCE_NOT_FOUND")
    return row[0], row[1], row[2]


@router.get("/versions/{version_id}/preview")
async def preview_version(
    version_id: UUID,
    request: Request,
    auth: Auth,
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=1000),
) -> dict[str, object]:
    version, blob, _document = await _owned_version(db, auth[0].id, version_id)
    generation = version.active_generation
    statement = select(ExtractedBlock).where(
        ExtractedBlock.owner_id == auth[0].id,
        ExtractedBlock.version_id == version.id,
    )
    if generation is None:
        statement = statement.where(false())
    else:
        statement = statement.where(ExtractedBlock.generation == generation)
    if page is not None:
        statement = statement.where(
            or_(
                ExtractedBlock.locator["page"].as_integer() == page,
                ExtractedBlock.locator["slide"].as_integer() == page,
            )
        )
    rows = list(
        (
            await db.scalars(
                statement.order_by(ExtractedBlock.ordinal).offset(offset).limit(limit + 1)
            )
        ).all()
    )
    has_more = len(rows) > limit
    blocks = rows[:limit]
    kind = (
        "pdf"
        if blob.mime == "application/pdf"
        else "image"
        if blob.mime.startswith("image/")
        else "docx"
        if blob.mime.endswith("wordprocessingml.document")
        else "pptx"
        if blob.mime.endswith("presentationml.presentation")
        else "text"
        if blob.mime in {"text/plain", "text/markdown"}
        else "unknown"
    )
    rendered_available = (
        kind == "pptx"
        and rendered_pdf_path(get_settings().preview_root, auth[0].id, version.id).is_file()
    )
    low_confidence = any(
        block.ocr_confidence is not None and block.ocr_confidence < 0.55 for block in blocks
    )
    slide: dict[str, object] | None = None
    slide_warning = False
    if kind == "pptx" and not rendered_available and page is not None and offset == 0:
        path = blob_path(get_settings().storage_root, auth[0].id, blob.storage_key)
        if path.is_file():
            try:
                slide = await run_in_threadpool(slide_layout, path, page)
            except Exception:
                slide_warning = True
        else:
            slide_warning = True
    return {
        "data": {
            "kind": kind,
            "rendered_available": rendered_available,
            "parse_state": version.parse_state,
            "error_code": version.error_code,
            "page_count": version.page_count,
            "generation": generation,
            "warnings": (["OCR_LOW_CONFIDENCE"] if low_confidence else [])
            + (["SLIDE_MEDIA_UNAVAILABLE"] if slide_warning else []),
            "slide": slide,
            "blocks": [
                {
                    "id": str(block.id),
                    "ordinal": block.ordinal,
                    "kind": block.kind,
                    "text": block.text,
                    "locator": block.locator,
                    "ocr_confidence": block.ocr_confidence,
                }
                for block in blocks
            ],
            "offset": offset,
            "limit": limit,
            "has_more": has_more,
        },
        "request_id": request.state.request_id,
    }


@router.get("/versions/{version_id}/rendered.pdf")
async def rendered_presentation(
    version_id: UUID,
    auth: Auth,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FileResponse:
    _version, blob, _document = await _owned_version(db, auth[0].id, version_id)
    if not blob.mime.endswith("presentationml.presentation"):
        raise HTTPException(415, "INLINE_PREVIEW_UNSUPPORTED")
    path = rendered_pdf_path(get_settings().preview_root, auth[0].id, version_id)
    if not path.is_file():
        raise HTTPException(404, "RENDERED_PREVIEW_UNAVAILABLE")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename="presentation.pdf",
        content_disposition_type="inline",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/versions/{version_id}/slides/{slide_number}/images/{shape_id}")
async def preview_slide_image(
    version_id: UUID,
    slide_number: int,
    shape_id: int,
    auth: Auth,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    _version, blob, _document = await _owned_version(db, auth[0].id, version_id)
    if not blob.mime.endswith("presentationml.presentation"):
        raise HTTPException(415, "INLINE_PREVIEW_UNSUPPORTED")
    if slide_number < 1 or shape_id < 1:
        raise HTTPException(404, "RESOURCE_NOT_FOUND")
    path = blob_path(get_settings().storage_root, auth[0].id, blob.storage_key)
    if not path.is_file():
        raise HTTPException(410, "SOURCE_UNAVAILABLE")
    try:
        image = await run_in_threadpool(slide_image_png, path, slide_number, shape_id)
    except Exception as exc:
        raise HTTPException(422, "SLIDE_PREVIEW_UNAVAILABLE") from exc
    if image is None:
        raise HTTPException(404, "RESOURCE_NOT_FOUND")
    return Response(
        content=image,
        media_type="image/png",
        headers={
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


def _byte_range(value: str | None, size: int) -> tuple[int, int] | None:
    if value is None:
        return None
    if not value.startswith("bytes=") or "," in value:
        raise HTTPException(
            416, "RANGE_NOT_SATISFIABLE", headers={"Content-Range": f"bytes */{size}"}
        )
    start_value, separator, end_value = value[6:].partition("-")
    if not separator:
        raise HTTPException(
            416, "RANGE_NOT_SATISFIABLE", headers={"Content-Range": f"bytes */{size}"}
        )
    try:
        if not start_value:
            suffix_length = int(end_value)
            if suffix_length <= 0:
                raise ValueError
            start = max(0, size - suffix_length)
            end = size - 1
        else:
            start = int(start_value)
            end = min(int(end_value), size - 1) if end_value else size - 1
    except ValueError as exc:
        raise HTTPException(
            416, "RANGE_NOT_SATISFIABLE", headers={"Content-Range": f"bytes */{size}"}
        ) from exc
    if start < 0 or start >= size or end < start:
        raise HTTPException(
            416, "RANGE_NOT_SATISFIABLE", headers={"Content-Range": f"bytes */{size}"}
        )
    return start, end


@router.get("/versions/{version_id}/content")
async def content_version(
    version_id: UUID,
    auth: Auth,
    db: Annotated[AsyncSession, Depends(get_db)],
    range_header: Annotated[str | None, Header(alias="Range")] = None,
) -> Response:
    _version, blob, _document = await _owned_version(db, auth[0].id, version_id)
    if blob.mime not in _INLINE_MIME:
        raise HTTPException(415, "INLINE_PREVIEW_UNSUPPORTED")
    path = blob_path(get_settings().storage_root, auth[0].id, blob.storage_key)
    if not path.is_file():
        raise HTTPException(410, "SOURCE_UNAVAILABLE")
    size = path.stat().st_size
    content_range = _byte_range(range_header, size)
    start, end = content_range if content_range is not None else (0, size - 1)
    status = 206 if content_range is not None else 200

    def stream() -> Iterator[bytes]:
        remaining = end - start + 1
        with path.open("rb") as source:
            source.seek(start)
            while remaining:
                chunk = source.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        "Content-Disposition": "inline",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
    }
    if content_range is not None:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(stream(), status_code=status, media_type=blob.mime, headers=headers)
