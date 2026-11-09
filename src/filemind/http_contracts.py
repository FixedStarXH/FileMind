"""Shared HTTP precondition and idempotency helpers."""

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from filemind.models import IdempotencyKey

_REVISION = re.compile(r'^"?(\d+)"?$')


def check_revision(if_match: str | None, revision: int) -> None:
    if if_match is None:
        raise HTTPException(428, "REVISION_REQUIRED")
    match = _REVISION.fullmatch(if_match.strip())
    if match is None:
        raise HTTPException(400, "INVALID_REVISION")
    if int(match.group(1)) != revision:
        raise HTTPException(412, "REVISION_MISMATCH")


def idempotency_key(value: str | None) -> str:
    if value is None:
        raise HTTPException(422, "IDEMPOTENCY_KEY_REQUIRED")
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise HTTPException(422, "INVALID_IDEMPOTENCY_KEY") from exc


def request_digest(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        encoded = part.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


async def replay_idempotent(
    db: AsyncSession,
    owner_id: UUID,
    route: str,
    key: str,
    digest: str,
) -> IdempotencyKey | None:
    now = datetime.now(UTC)
    await db.execute(
        delete(IdempotencyKey).where(
            IdempotencyKey.owner_id == owner_id,
            IdempotencyKey.route == route,
            IdempotencyKey.expires_at <= now,
        )
    )
    record = await db.scalar(
        select(IdempotencyKey).where(
            IdempotencyKey.owner_id == owner_id,
            IdempotencyKey.route == route,
            IdempotencyKey.key == key,
        )
    )
    if record is None:
        return None
    if record.request_hash != digest:
        raise HTTPException(409, "IDEMPOTENCY_CONFLICT")
    return record


def save_idempotency(
    db: AsyncSession,
    owner_id: UUID,
    route: str,
    key: str,
    digest: str,
    response_status: int,
    response_body: dict[str, object],
    resource_id: UUID,
) -> None:
    db.add(
        IdempotencyKey(
            owner_id=owner_id,
            route=route,
            key=key,
            resource_id=resource_id,
            request_hash=digest,
            response_status=response_status,
            response_body=response_body,
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
    )


def copy_replay_body(record: IdempotencyKey, request_id: str) -> dict[str, object]:
    decoded: object = json.loads(json.dumps(record.response_body))
    if not isinstance(decoded, dict):
        raise ValueError("stored idempotency response must be a JSON object")
    body = cast(dict[str, object], decoded)
    body["request_id"] = request_id
    return body
