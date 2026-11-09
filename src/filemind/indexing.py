"""Versioned chunking and Chinese keyword preparation."""

import logging
from collections.abc import Sequence
from typing import Any, cast
from uuid import UUID, uuid4

import jieba  # type: ignore[import-untyped]

from filemind.config import get_settings
from filemind.embeddings import embed_texts
from filemind.models import Chunk, ExtractedBlock

CHUNK_TOKEN_LIMIT = 220
CHUNK_TOKEN_OVERLAP = 35
INDEX_PIPELINE_VERSION = "filemind-index-1"
logger = logging.getLogger("filemind.indexing")


def _tokens(value: str) -> list[tuple[str, int, int]]:
    raw = cast(Any, jieba.tokenize(value, mode="default"))
    return [(str(word), int(start), int(end)) for word, start, end in raw]


def _search_text(value: str) -> str:
    return " ".join(
        str(token).strip() for token in cast(Any, jieba.cut_for_search(value)) if str(token).strip()
    )


def make_chunks(
    owner_id: UUID,
    version_id: UUID,
    generation: int,
    blocks: Sequence[ExtractedBlock],
) -> list[Chunk]:
    prepared: list[tuple[str, dict[str, object], int, str]] = []
    for block in blocks:
        tokens = _tokens(block.text)
        if not tokens:
            continue
        start_token = 0
        while start_token < len(tokens):
            end_token = min(len(tokens), start_token + CHUNK_TOKEN_LIMIT)
            char_start = tokens[start_token][1]
            char_end = tokens[end_token - 1][2]
            text = block.text[char_start:char_end].strip()
            if text:
                locator: dict[str, object] = {
                    "block_ordinal": block.ordinal,
                    "source": block.locator,
                    "char_start": char_start,
                    "char_end": char_end,
                }
                prepared.append((text, locator, end_token - start_token, _search_text(text)))
            if end_token == len(tokens):
                break
            start_token = end_token - CHUNK_TOKEN_OVERLAP

    vectors: list[list[float]] | None = None
    if prepared:
        try:
            vectors = embed_texts(text for text, _locator, _count, _search in prepared)
        except Exception as exc:
            logger.warning("BGE unavailable; using keyword index error_type=%s", type(exc).__name__)
            vectors = None

    model_id = f"{get_settings().embedding_model}:{get_settings().embedding_model_revision}"
    return [
        Chunk(
            id=uuid4(),
            owner_id=owner_id,
            version_id=version_id,
            generation=generation,
            ordinal=ordinal,
            text=text,
            locator=locator,
            token_count=token_count,
            search_text=search_text,
            embedding=vectors[ordinal] if vectors is not None else None,
            embedding_model=model_id if vectors is not None else None,
        )
        for ordinal, (text, locator, token_count, search_text) in enumerate(prepared)
    ]
