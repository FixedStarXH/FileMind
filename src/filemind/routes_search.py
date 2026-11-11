"""Owner-scoped keyword, semantic, and reciprocal-rank-fusion search."""

import logging
import re
from collections import defaultdict
from typing import Annotated, Any, cast
from uuid import UUID

import jieba  # type: ignore[import-untyped]
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement
from starlette.concurrency import run_in_threadpool

from filemind.auth import Auth
from filemind.config import get_settings
from filemind.database import get_db
from filemind.embeddings import embed_texts
from filemind.models import Chunk, Document, DocumentTag, DocumentVersion, Folder, Tag
from filemind.search_schemas import SearchRequest

logger = logging.getLogger("filemind.search")
router = APIRouter(prefix="/api/v1", tags=["search"])
_RRF_K = 60
_CANDIDATE_LIMIT = 100


def _keyword_query(value: str) -> str:
    return " ".join(
        str(token).strip() for token in cast(Any, jieba.cut_for_search(value)) if str(token).strip()
    )


def _snippet(value: str, limit: int = 320) -> str:
    return re.sub(r"\s+", " ", value).strip()[:limit]


async def _scope_predicate(
    db: AsyncSession, owner_id: UUID, request: SearchRequest
) -> ColumnElement[bool] | None:
    scope = request.scope
    selectors: list[ColumnElement[bool]] = []
    if scope.all_documents:
        return None

    if scope.document_ids:
        owned = set(
            (
                await db.scalars(
                    select(Document.id).where(
                        Document.owner_id == owner_id,
                        Document.id.in_(scope.document_ids),
                    )
                )
            ).all()
        )
        if owned != set(scope.document_ids):
            raise HTTPException(404, "RESOURCE_NOT_FOUND")
        selectors.append(Document.id.in_(scope.document_ids))

    if scope.folder_ids:
        owned_folders = set(
            (
                await db.scalars(
                    select(Folder.id).where(
                        Folder.owner_id == owner_id,
                        Folder.id.in_(scope.folder_ids),
                    )
                )
            ).all()
        )
        if owned_folders != set(scope.folder_ids):
            raise HTTPException(404, "RESOURCE_NOT_FOUND")
        tree = (
            select(Folder.id.label("id"))
            .where(Folder.owner_id == owner_id, Folder.id.in_(scope.folder_ids))
            .cte(name="search_scope_folders", recursive=True)
        )
        tree = tree.union_all(
            select(Folder.id.label("id"))
            .join(tree, Folder.parent_id == tree.c.id)
            .where(Folder.owner_id == owner_id)
        )
        selectors.append(Document.folder_id.in_(select(tree.c.id)))

    if scope.tag_ids:
        owned_tags = set(
            (
                await db.scalars(
                    select(Tag.id).where(Tag.owner_id == owner_id, Tag.id.in_(scope.tag_ids))
                )
            ).all()
        )
        if owned_tags != set(scope.tag_ids):
            raise HTTPException(404, "RESOURCE_NOT_FOUND")
        selectors.append(
            exists().where(
                DocumentTag.owner_id == owner_id,
                DocumentTag.document_id == Document.id,
                DocumentTag.tag_id.in_(scope.tag_ids),
            )
        )

    if scope.inbox:
        selectors.append(Document.folder_id.is_(None))
    return or_(*selectors)


@router.post("/search")
async def search_documents(
    body: SearchRequest,
    request: Request,
    auth: Auth,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, object]:
    if body.mode == "semantic" and not get_settings().embeddings_enabled:
        raise HTTPException(503, "SEARCH_UNAVAILABLE")
    owner = auth[0]
    scope_filter = await _scope_predicate(db, owner.id, body)
    filters = [
        Chunk.owner_id == owner.id,
        DocumentVersion.owner_id == owner.id,
        DocumentVersion.active_generation == Chunk.generation,
        DocumentVersion.index_mode.in_(("hybrid", "keyword_only")),
        Document.current_version_id == Chunk.version_id,
        Document.owner_id == owner.id,
        Document.state == "active",
    ]
    if scope_filter is not None:
        filters.append(scope_filter)

    keyword_only_versions = (
        select(DocumentVersion.id)
        .join(
            Document,
            (Document.id == DocumentVersion.document_id)
            & (Document.owner_id == DocumentVersion.owner_id),
        )
        .where(
            DocumentVersion.owner_id == owner.id,
            DocumentVersion.index_mode == "keyword_only",
            Document.current_version_id == DocumentVersion.id,
            Document.state == "active",
        )
    )
    if scope_filter is not None:
        keyword_only_versions = keyword_only_versions.where(scope_filter)
    has_keyword_only_versions = bool(await db.scalar(select(exists(keyword_only_versions))))

    normalized = _keyword_query(body.query)
    keyword_rows: list[tuple[Chunk, UUID, str, float]] = []
    if body.mode in ("keyword", "hybrid") and normalized:
        query = func.plainto_tsquery("simple", normalized)
        rank_expression = func.ts_rank_cd(Chunk.search_vector, query)
        rows = await db.execute(
            select(Chunk, Document.id, Document.title, rank_expression.label("rank"))
            .join(
                DocumentVersion,
                (DocumentVersion.id == Chunk.version_id)
                & (DocumentVersion.owner_id == Chunk.owner_id),
            )
            .join(
                Document,
                (Document.id == DocumentVersion.document_id)
                & (Document.owner_id == DocumentVersion.owner_id),
            )
            .where(*filters, Chunk.search_vector.op("@@")(query))
            .order_by(rank_expression.desc(), Chunk.id)
            .limit(min(_CANDIDATE_LIMIT, body.limit * 4))
        )
        keyword_rows = [
            (chunk, document_id, title, float(score))
            for chunk, document_id, title, score in rows.all()
        ]

    query_vector: list[float] | None = None
    embedding_unavailable = body.mode == "hybrid" and not get_settings().embeddings_enabled
    if body.mode in ("semantic", "hybrid") and get_settings().embeddings_enabled:
        try:
            embeddings = await run_in_threadpool(embed_texts, [body.query], query=True)
            query_vector = embeddings[0]
        except Exception as exc:
            embedding_unavailable = True
            logger.warning(
                "query embedding unavailable request_id=%s error_type=%s",
                request.state.request_id,
                type(exc).__name__,
            )
            if body.mode == "semantic":
                raise HTTPException(503, "SEARCH_UNAVAILABLE") from None

    semantic_rows: list[tuple[Chunk, UUID, str, float]] = []
    if query_vector is not None:
        distance = Chunk.embedding.cosine_distance(query_vector)
        rows = await db.execute(
            select(Chunk, Document.id, Document.title, distance.label("distance"))
            .join(
                DocumentVersion,
                (DocumentVersion.id == Chunk.version_id)
                & (DocumentVersion.owner_id == Chunk.owner_id),
            )
            .join(
                Document,
                (Document.id == DocumentVersion.document_id)
                & (Document.owner_id == DocumentVersion.owner_id),
            )
            .where(*filters, Chunk.embedding.is_not(None), DocumentVersion.index_mode == "hybrid")
            .order_by(distance, Chunk.id)
            .limit(min(_CANDIDATE_LIMIT, body.limit * 4))
        )
        semantic_rows = [
            (chunk, document_id, title, max(0.0, 1.0 - float(distance_value)))
            for chunk, document_id, title, distance_value in rows.all()
        ]

    scores: dict[UUID, float] = defaultdict(float)
    records: dict[UUID, tuple[Chunk, UUID, str, float | None, float | None]] = {}
    for keyword_rank, (chunk, document_id, title, raw_score) in enumerate(keyword_rows, start=1):
        scores[chunk.id] += 1.0 / (_RRF_K + keyword_rank)
        records[chunk.id] = (chunk, document_id, title, raw_score, None)
    for semantic_rank, (chunk, document_id, title, raw_score) in enumerate(semantic_rows, start=1):
        scores[chunk.id] += 1.0 / (_RRF_K + semantic_rank)
        previous = records.get(chunk.id)
        records[chunk.id] = (
            chunk,
            document_id,
            title,
            previous[3] if previous else None,
            raw_score,
        )

    ordered = sorted(records, key=lambda chunk_id: (-scores[chunk_id], str(chunk_id)))[: body.limit]
    hits = []
    for chunk_id in ordered:
        chunk, document_id, title, _keyword_score, _semantic_score = records[chunk_id]
        hits.append(
            {
                "document_id": str(document_id),
                "version_id": str(chunk.version_id),
                "chunk_id": str(chunk.id),
                "title": title,
                "snippet": _snippet(chunk.text),
                "locator": chunk.locator,
                "score": scores[chunk_id],
            }
        )
    return {
        "data": {
            "hits": hits,
            "degraded": embedding_unavailable
            or (body.mode in ("semantic", "hybrid") and has_keyword_only_versions),
            "retrieval_version": owner.index_revision,
        },
        "request_id": request.state.request_id,
    }
