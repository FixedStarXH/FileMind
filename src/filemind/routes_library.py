"""Private folders, tags and document metadata APIs."""

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from sqlalchemy import delete, exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from filemind.auth import Auth, WriteAuth
from filemind.database import get_db
from filemind.http_contracts import check_revision
from filemind.library_schemas import (
    DocumentPatch,
    FolderCreate,
    FolderPatch,
    TagCreate,
    TagPatch,
)
from filemind.models import Blob, Document, DocumentTag, DocumentVersion, Folder, Tag

router = APIRouter(prefix="/api/v1", tags=["library"])
_PAGE_LIMIT_DEFAULT = 20
_PAGE_LIMIT_MAX = 100
_TRASH_TTL_DAYS = 30


def folder_payload(folder: Folder) -> dict[str, object]:
    return {
        "id": str(folder.id),
        "name": folder.name,
        "parent_id": str(folder.parent_id) if folder.parent_id else None,
        "revision": folder.revision,
        "created_at": folder.created_at,
        "updated_at": folder.updated_at,
    }


def tag_payload(tag: Tag, document_count: int = 0) -> dict[str, object]:
    return {
        "id": str(tag.id),
        "name": tag.name,
        "color": tag.color,
        "document_count": document_count,
    }


async def owned_folder(db: AsyncSession, owner_id: UUID, folder_id: UUID) -> Folder:
    folder = await db.scalar(
        select(Folder).where(Folder.id == folder_id, Folder.owner_id == owner_id)
    )
    if folder is None:
        raise HTTPException(404, "RESOURCE_NOT_FOUND")
    return folder


async def owned_document(db: AsyncSession, owner_id: UUID, document_id: UUID) -> Document:
    document = await db.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.owner_id == owner_id,
            Document.state.in_(("active", "trashed")),
        )
    )
    if document is None:
        raise HTTPException(404, "RESOURCE_NOT_FOUND")
    return document


async def validate_folder_parent(
    db: AsyncSession,
    owner_id: UUID,
    parent_id: UUID | None,
    moving_folder_id: UUID | None = None,
) -> None:
    rows = list((await db.scalars(select(Folder).where(Folder.owner_id == owner_id))).all())
    by_id = {folder.id: folder for folder in rows}
    if parent_id is not None and parent_id not in by_id:
        raise HTTPException(404, "RESOURCE_NOT_FOUND")

    ancestor_ids: set[UUID] = set()
    cursor = parent_id
    parent_depth = 0
    while cursor is not None:
        if cursor in ancestor_ids or cursor not in by_id:
            raise HTTPException(409, "FOLDER_TREE_INVALID")
        if moving_folder_id is not None and cursor == moving_folder_id:
            raise HTTPException(409, "FOLDER_CYCLE")
        ancestor_ids.add(cursor)
        parent_depth += 1
        cursor = by_id[cursor].parent_id

    children: dict[UUID, list[UUID]] = {}
    for folder in rows:
        if folder.parent_id is not None:
            children.setdefault(folder.parent_id, []).append(folder.id)

    def subtree_height(root: UUID, seen: set[UUID]) -> int:
        if root in seen:
            raise HTTPException(409, "FOLDER_TREE_INVALID")
        next_seen = {*seen, root}
        descendants = children.get(root, [])
        if not descendants:
            return 1
        return 1 + max(subtree_height(child, next_seen) for child in descendants)

    height = subtree_height(moving_folder_id, set()) if moving_folder_id else 1
    if parent_depth + height > 10:
        raise HTTPException(409, "FOLDER_DEPTH_LIMIT")


async def document_tag_ids(db: AsyncSession, document_id: UUID) -> list[str]:
    values = await db.scalars(
        select(DocumentTag.tag_id)
        .where(DocumentTag.document_id == document_id)
        .order_by(DocumentTag.tag_id)
    )
    return [str(value) for value in values]


async def document_payload(db: AsyncSession, document: Document) -> dict[str, object]:
    version = None
    if document.latest_version_id is not None:
        version = await db.scalar(
            select(DocumentVersion).where(
                DocumentVersion.id == document.latest_version_id,
                DocumentVersion.owner_id == document.owner_id,
                DocumentVersion.document_id == document.id,
            )
        )
    blob = await db.get(Blob, version.blob_id) if version is not None else None
    return {
        "id": str(document.id),
        "title": document.title,
        "folder_id": str(document.folder_id) if document.folder_id else None,
        "tag_ids": await document_tag_ids(db, document.id),
        "favorite": document.favorite,
        "state": document.state,
        "metadata_revision": document.metadata_revision,
        "current_version_id": str(document.current_version_id)
        if document.current_version_id
        else None,
        "latest_version_id": str(document.latest_version_id)
        if document.latest_version_id
        else None,
        "original_name": version.original_name if version else None,
        "parse_state": version.parse_state if version else None,
        "error_code": version.error_code if version else None,
        "size_bytes": blob.size_bytes if blob else 0,
        "mime": blob.mime if blob else None,
        "created_at": document.created_at.isoformat() if document.created_at else None,
        "updated_at": document.updated_at.isoformat() if document.updated_at else None,
        "trashed_at": document.trashed_at.isoformat() if document.trashed_at else None,
        "restore_until": (
            (document.trashed_at + timedelta(days=_TRASH_TTL_DAYS)).isoformat()
            if document.trashed_at
            else None
        ),
    }


def _cursor_encode(payload: dict[str, str]) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _cursor_decode(cursor: str, sort: str, filter_hash: str) -> dict[str, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded: object = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError
        payload = cast(dict[str, str], decoded)
        if payload.get("sort") != sort or payload.get("filter_hash") != filter_hash:
            raise ValueError
        return payload
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(400, "CURSOR_INVALID") from exc


@router.get("/folders")
async def list_folders(
    auth: Auth,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, object]:
    folders = await db.scalars(
        select(Folder)
        .where(Folder.owner_id == auth[0].id)
        .order_by(Folder.parent_id.nulls_first(), func.lower(Folder.name), Folder.id)
    )
    return {"data": {"items": [folder_payload(folder) for folder in folders]}}


@router.post("/folders", status_code=status.HTTP_201_CREATED)
async def create_folder(
    body: FolderCreate,
    auth: WriteAuth,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, object]:
    owner_id = auth[0].id
    await validate_folder_parent(db, owner_id, body.parent_id)
    folder = Folder(owner_id=owner_id, name=body.name, parent_id=body.parent_id)
    db.add(folder)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "FOLDER_EXISTS") from exc
    await db.refresh(folder)
    return {"data": folder_payload(folder)}


@router.patch("/folders/{folder_id}")
async def update_folder(
    folder_id: UUID,
    body: FolderPatch,
    auth: WriteAuth,
    db: Annotated[AsyncSession, Depends(get_db)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> dict[str, object]:
    folder = await owned_folder(db, auth[0].id, folder_id)
    check_revision(if_match, folder.revision)
    values = body.model_fields_set
    target_parent = body.parent_id if "parent_id" in values else folder.parent_id
    if "parent_id" in values:
        await validate_folder_parent(db, auth[0].id, target_parent, folder.id)
        folder.parent_id = target_parent
    if body.name is not None:
        folder.name = body.name
    folder.revision += 1
    folder.updated_at = datetime.now(UTC)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "FOLDER_EXISTS") from exc
    return {"data": folder_payload(folder)}


@router.delete("/folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(
    folder_id: UUID,
    auth: WriteAuth,
    db: Annotated[AsyncSession, Depends(get_db)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> Response:
    folder = await owned_folder(db, auth[0].id, folder_id)
    check_revision(if_match, folder.revision)
    child_exists = await db.scalar(
        select(exists().where(Folder.owner_id == auth[0].id, Folder.parent_id == folder.id))
    )
    document_exists = await db.scalar(
        select(exists().where(Document.owner_id == auth[0].id, Document.folder_id == folder.id))
    )
    if child_exists or document_exists:
        raise HTTPException(409, "FOLDER_NOT_EMPTY")
    await db.delete(folder)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/tags")
async def list_tags(
    auth: Auth,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, object]:
    rows = await db.execute(
        select(Tag, func.count(DocumentTag.document_id))
        .outerjoin(
            DocumentTag,
            (DocumentTag.owner_id == Tag.owner_id) & (DocumentTag.tag_id == Tag.id),
        )
        .where(Tag.owner_id == auth[0].id)
        .group_by(Tag.id)
        .order_by(func.lower(Tag.name), Tag.id)
    )
    return {"data": {"items": [tag_payload(tag, count) for tag, count in rows]}}


@router.post("/tags", status_code=status.HTTP_201_CREATED)
async def create_tag(
    body: TagCreate,
    auth: WriteAuth,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, object]:
    tag = Tag(owner_id=auth[0].id, name=body.name, color=body.color)
    db.add(tag)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "TAG_EXISTS") from exc
    await db.refresh(tag)
    return {"data": tag_payload(tag)}


@router.patch("/tags/{tag_id}")
async def update_tag(
    tag_id: UUID,
    body: TagPatch,
    auth: WriteAuth,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, object]:
    tag = await db.scalar(select(Tag).where(Tag.id == tag_id, Tag.owner_id == auth[0].id))
    if tag is None:
        raise HTTPException(404, "RESOURCE_NOT_FOUND")
    if body.name is not None:
        tag.name = body.name
    if body.color is not None:
        tag.color = body.color
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "TAG_EXISTS") from exc
    return {"data": tag_payload(tag)}


@router.delete("/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag(
    tag_id: UUID,
    auth: WriteAuth,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    tag = await db.scalar(select(Tag).where(Tag.id == tag_id, Tag.owner_id == auth[0].id))
    if tag is None:
        raise HTTPException(404, "RESOURCE_NOT_FOUND")
    await db.delete(tag)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/documents")
async def list_documents(
    request: Request,
    auth: Auth,
    db: Annotated[AsyncSession, Depends(get_db)],
    folder_id: str | None = None,
    tag_ids: Annotated[list[UUID] | None, Query()] = None,
    favorite: bool | None = None,
    q: str | None = Query(default=None, max_length=300),
    state: Literal["active", "trashed"] = "active",
    sort: Literal["updated_desc", "name_asc", "size_desc"] = "updated_desc",
    cursor: str | None = None,
    limit: int = Query(default=_PAGE_LIMIT_DEFAULT, ge=1, le=_PAGE_LIMIT_MAX),
) -> dict[str, object]:
    owner_id = auth[0].id
    filters: list[ColumnElement[bool]] = [
        Document.owner_id == owner_id,
        Document.state == state,
    ]
    if folder_id is not None:
        if folder_id == "inbox":
            filters.append(Document.folder_id.is_(None))
        else:
            try:
                parsed_folder_id = UUID(folder_id)
            except ValueError as exc:
                raise HTTPException(422, "VALIDATION_ERROR") from exc
            filters.append(Document.folder_id == parsed_folder_id)
    if favorite is not None:
        filters.append(Document.favorite.is_(favorite))
    if tag_ids:
        filters.append(
            exists().where(
                DocumentTag.owner_id == owner_id,
                DocumentTag.document_id == Document.id,
                DocumentTag.tag_id.in_(tag_ids),
            )
        )
    query_text = (q or "").strip()
    if query_text:
        filters.append(
            or_(
                Document.title.ilike(f"%{query_text}%"),
                exists().where(
                    DocumentVersion.owner_id == owner_id,
                    DocumentVersion.document_id == Document.id,
                    DocumentVersion.original_name.ilike(f"%{query_text}%"),
                ),
            )
        )

    size_expression = (
        select(Blob.size_bytes)
        .join(DocumentVersion, DocumentVersion.blob_id == Blob.id)
        .where(
            DocumentVersion.id == Document.latest_version_id,
            DocumentVersion.owner_id == owner_id,
            Blob.owner_id == owner_id,
        )
        .scalar_subquery()
    )
    filter_data = json.dumps(
        [folder_id, sorted(str(item) for item in (tag_ids or [])), favorite, query_text, state],
        separators=(",", ":"),
    )
    filter_hash = hashlib.sha256(filter_data.encode("utf-8")).hexdigest()
    cursor_data = _cursor_decode(cursor, sort, filter_hash) if cursor else None

    statement = select(Document).where(*filters)
    if sort == "updated_desc":
        if cursor_data:
            cursor_time = datetime.fromisoformat(cursor_data["value"])
            cursor_id = UUID(cursor_data["id"])
            statement = statement.where(
                or_(
                    Document.updated_at < cursor_time,
                    (Document.updated_at == cursor_time) & (Document.id < cursor_id),
                )
            )
        statement = statement.order_by(Document.updated_at.desc(), Document.id.desc())
    elif sort == "name_asc":
        name_expression = func.lower(Document.title)
        if cursor_data:
            cursor_id = UUID(cursor_data["id"])
            statement = statement.where(
                or_(
                    name_expression > cursor_data["value"],
                    (name_expression == cursor_data["value"]) & (Document.id > cursor_id),
                )
            )
        statement = statement.order_by(name_expression, Document.id)
    else:
        size_key = func.coalesce(size_expression, 0)
        if cursor_data:
            cursor_id = UUID(cursor_data["id"])
            size_value = int(cursor_data["value"])
            statement = statement.where(
                or_(size_key < size_value, (size_key == size_value) & (Document.id < cursor_id))
            )
        statement = statement.order_by(size_key.desc(), Document.id.desc())

    documents = list((await db.scalars(statement.limit(limit + 1))).all())
    has_next = len(documents) > limit
    documents = documents[:limit]
    items = [await document_payload(db, document) for document in documents]
    next_cursor = None
    if has_next and documents:
        last = documents[-1]
        if sort == "updated_desc":
            cursor_value = last.updated_at.isoformat()
        elif sort == "name_asc":
            cursor_value = last.title.lower()
        else:
            cursor_value = str(
                await db.scalar(select(size_expression).where(Document.id == last.id)) or 0
            )
        next_cursor = _cursor_encode(
            {
                "sort": sort,
                "filter_hash": filter_hash,
                "value": cursor_value,
                "id": str(last.id),
            }
        )
    return {
        "data": {"items": items, "next_cursor": next_cursor},
        "request_id": request.state.request_id,
    }


@router.get("/documents/{document_id}")
async def get_document(
    document_id: UUID,
    request: Request,
    auth: Auth,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, object]:
    document = await owned_document(db, auth[0].id, document_id)
    payload = await document_payload(db, document)
    versions = await db.scalars(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document.id, DocumentVersion.owner_id == auth[0].id)
        .order_by(DocumentVersion.version_no.desc())
    )
    payload["versions"] = [
        {
            "id": str(version.id),
            "version_no": version.version_no,
            "original_name": version.original_name,
            "parse_state": version.parse_state,
            "error_code": version.error_code,
            "is_current": version.id == document.current_version_id,
            "is_latest": version.id == document.latest_version_id,
            "created_at": version.created_at.isoformat() if version.created_at else None,
        }
        for version in versions
    ]
    return {"data": payload, "request_id": request.state.request_id}


@router.patch("/documents/{document_id}")
async def update_document(
    document_id: UUID,
    body: DocumentPatch,
    request: Request,
    auth: WriteAuth,
    db: Annotated[AsyncSession, Depends(get_db)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> dict[str, object]:
    document = await owned_document(db, auth[0].id, document_id)
    if document.state != "active":
        raise HTTPException(409, "DOCUMENT_NOT_ACTIVE")
    check_revision(if_match, document.metadata_revision)
    fields = body.model_fields_set
    if body.title is not None:
        document.title = body.title
    if "folder_id" in fields:
        if body.folder_id is not None:
            await owned_folder(db, auth[0].id, body.folder_id)
        document.folder_id = body.folder_id
    if body.favorite is not None:
        document.favorite = body.favorite
    if "tag_ids" in fields:
        new_tag_ids = set(body.tag_ids or [])
        if new_tag_ids:
            owned_tags = set(
                (
                    await db.scalars(
                        select(Tag.id).where(Tag.owner_id == auth[0].id, Tag.id.in_(new_tag_ids))
                    )
                ).all()
            )
            if owned_tags != new_tag_ids:
                raise HTTPException(404, "RESOURCE_NOT_FOUND")
        await db.execute(delete(DocumentTag).where(DocumentTag.document_id == document.id))
        db.add_all(
            DocumentTag(owner_id=auth[0].id, document_id=document.id, tag_id=tag_id)
            for tag_id in new_tag_ids
        )
    document.metadata_revision += 1
    document.updated_at = datetime.now(UTC)
    await db.commit()
    return {
        "data": await document_payload(db, document),
        "request_id": request.state.request_id,
    }


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def trash_document(
    document_id: UUID,
    auth: WriteAuth,
    db: Annotated[AsyncSession, Depends(get_db)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> Response:
    document = await owned_document(db, auth[0].id, document_id)
    if document.state != "active":
        raise HTTPException(409, "DOCUMENT_NOT_ACTIVE")
    check_revision(if_match, document.metadata_revision)
    document.state = "trashed"
    document.trashed_at = datetime.now(UTC)
    document.metadata_revision += 1
    document.updated_at = datetime.now(UTC)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/documents/{document_id}/restore")
async def restore_document(
    document_id: UUID,
    request: Request,
    auth: WriteAuth,
    db: Annotated[AsyncSession, Depends(get_db)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> dict[str, object]:
    document = await owned_document(db, auth[0].id, document_id)
    if document.state != "trashed" or document.trashed_at is None:
        raise HTTPException(409, "DOCUMENT_NOT_TRASHED")
    check_revision(if_match, document.metadata_revision)
    if document.trashed_at + timedelta(days=_TRASH_TTL_DAYS) < datetime.now(UTC):
        raise HTTPException(410, "TRASH_EXPIRED")
    document.state = "active"
    document.trashed_at = None
    document.metadata_revision += 1
    document.updated_at = datetime.now(UTC)
    await db.commit()
    return {
        "data": await document_payload(db, document),
        "request_id": request.state.request_id,
    }


@router.get("/documents/{document_id}/versions")
async def list_versions(
    document_id: UUID,
    request: Request,
    auth: Auth,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, object]:
    document = await owned_document(db, auth[0].id, document_id)
    versions = await db.scalars(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document.id, DocumentVersion.owner_id == auth[0].id)
        .order_by(DocumentVersion.version_no.desc())
    )
    items = []
    for version in versions:
        blob = await db.get(Blob, version.blob_id)
        items.append(
            {
                "id": str(version.id),
                "version_no": version.version_no,
                "original_name": version.original_name,
                "parse_state": version.parse_state,
                "error_code": version.error_code,
                "size_bytes": blob.size_bytes if blob else 0,
                "mime": blob.mime if blob else None,
                "is_current": version.id == document.current_version_id,
                "is_latest": version.id == document.latest_version_id,
                "created_at": version.created_at.isoformat() if version.created_at else None,
            }
        )
    return {"data": {"items": items}, "request_id": request.state.request_id}
