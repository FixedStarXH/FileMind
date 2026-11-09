"""Private, citation-first QA conversations and resumable run streams."""

import asyncio
import json
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Annotated, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from filemind.agent_events import append_event_async
from filemind.agent_schemas import ConversationCreate, RunCreate
from filemind.auth import Auth, WriteAuth
from filemind.config import get_settings
from filemind.database import get_db
from filemind.models import (
    AgentRun,
    BackgroundTask,
    Citation,
    Conversation,
    Document,
    Message,
    RunStep,
    StreamEvent,
    User,
)
from filemind.routes_search import _scope_predicate
from filemind.search_schemas import SearchRequest
from filemind.task_contracts import enqueue_task

router = APIRouter(prefix="/api/v1", tags=["assistant"])
_TERMINAL_RUN_STATES = {"succeeded", "failed", "cancelled"}


def _conversation_payload(conversation: Conversation) -> dict[str, object]:
    return {
        "id": str(conversation.id),
        "title": conversation.title,
        "scope": conversation.scope,
        "revision": conversation.revision,
        "created_at": conversation.created_at.isoformat(),
        "updated_at": conversation.updated_at.isoformat(),
    }


def _citation_payload(
    citation: Citation, *, title: str, state: str, current_version_id: UUID | None
) -> dict[str, object]:
    unavailable = state != "active"
    return {
        "id": citation.citation_key,
        "document_id": str(citation.document_id),
        "title": title,
        "version_id": str(citation.version_id),
        "chunk_id": str(citation.chunk_id) if citation.chunk_id else None,
        "locator": citation.locator,
        "quote": "" if unavailable else citation.quote,
        "status": "trashed"
        if state == "trashed"
        else "unavailable"
        if unavailable
        else "available",
        "historical_version": current_version_id != citation.version_id,
    }


def _parse_last_event_id(value: str | None) -> int:
    if value is None or value == "":
        return 0
    if not re.fullmatch(r"[0-9]{1,19}", value):
        raise HTTPException(400, "CURSOR_INVALID")
    return int(value)


@router.post("/conversations", status_code=status.HTTP_201_CREATED)
async def create_conversation(
    body: ConversationCreate,
    request: Request,
    auth: WriteAuth,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, object]:
    await _scope_predicate(
        db,
        auth[0].id,
        SearchRequest(query="scope-check", mode="keyword", scope=body.scope),
    )
    now = datetime.now(UTC)
    conversation = Conversation(
        id=uuid4(),
        owner_id=auth[0].id,
        title=body.title.strip(),
        scope=cast(dict[str, object], body.scope.model_dump(mode="json")),
        created_at=now,
        updated_at=now,
    )
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    return {"data": _conversation_payload(conversation), "request_id": request.state.request_id}


@router.get("/conversations")
async def list_conversations(
    request: Request,
    auth: Auth,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, object]:
    rows = await db.scalars(
        select(Conversation)
        .where(Conversation.owner_id == auth[0].id)
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        .limit(100)
    )
    return {
        "data": {"items": [_conversation_payload(item) for item in rows.all()]},
        "request_id": request.state.request_id,
    }


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: UUID,
    request: Request,
    auth: Auth,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, object]:
    conversation = await db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.owner_id == auth[0].id,
        )
    )
    if conversation is None:
        raise HTTPException(404, "RESOURCE_NOT_FOUND")
    messages = await db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id, Message.owner_id == auth[0].id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(12)
    )
    ordered = list(reversed(messages.all()))
    return {
        "data": {
            "conversation": _conversation_payload(conversation),
            "messages": [
                {
                    "id": str(message.id),
                    "role": message.role,
                    "content": "（相关来源已移除）" if message.redacted_at else message.content,
                    "run_id": str(message.run_id) if message.run_id else None,
                    "created_at": message.created_at.isoformat(),
                    "redacted": message.redacted_at is not None,
                }
                for message in ordered
            ],
        },
        "request_id": request.state.request_id,
    }


@router.post("/agent/runs", status_code=status.HTTP_202_ACCEPTED)
async def create_run(
    body: RunCreate,
    request: Request,
    auth: WriteAuth,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, object]:
    config = get_settings()
    owner = await db.scalar(select(User).where(User.id == auth[0].id).with_for_update())
    if owner is None:
        raise HTTPException(404, "RESOURCE_NOT_FOUND")
    if not owner.ai_enabled:
        raise HTTPException(403, "AI_DISABLED")
    if not config.has_deepseek_key:
        raise HTTPException(503, "AI_NOT_CONFIGURED")
    conversation = await db.scalar(
        select(Conversation)
        .where(Conversation.id == body.conversation_id, Conversation.owner_id == owner.id)
        .with_for_update()
    )
    if conversation is None:
        raise HTTPException(404, "RESOURCE_NOT_FOUND")
    await _scope_predicate(
        db,
        owner.id,
        SearchRequest(query=body.instruction[:2000], mode="keyword", scope=body.scope),
    )
    active_count = await db.scalar(
        select(func.count())
        .select_from(AgentRun)
        .where(AgentRun.owner_id == owner.id, AgentRun.status.in_(("queued", "running")))
    )
    if int(active_count or 0) >= config.agent_concurrency:
        raise HTTPException(429, "RATE_LIMITED", headers={"Retry-After": "30"})
    daily_count = await db.scalar(
        select(func.count())
        .select_from(AgentRun)
        .where(
            AgentRun.owner_id == owner.id,
            AgentRun.created_at >= datetime.now(UTC) - timedelta(days=1),
        )
    )
    if int(daily_count or 0) >= config.agent_daily_runs:
        raise HTTPException(429, "RATE_LIMITED", headers={"Retry-After": "3600"})

    now = datetime.now(UTC)
    run_id = uuid4()
    run = AgentRun(
        id=run_id,
        owner_id=owner.id,
        conversation_id=conversation.id,
        mode=body.mode,
        instruction=body.instruction.strip(),
        scope=cast(dict[str, object], body.scope.model_dump(mode="json")),
        status="queued",
        graph_thread_id=str(uuid4()),
        budget={"llm_calls": 0, "tool_calls": 0, "max_llm_calls": config.run_max_llm_calls},
        model=config.deepseek_model,
        prompt_version="filemind-qa-1",
        next_event_seq=2,
        created_at=now,
    )
    task = enqueue_task(db, owner.id, "agent_run", run_id, stage="queued")
    run.task_id = task.id
    db.add(run)
    db.add(
        Message(
            id=uuid4(),
            owner_id=owner.id,
            conversation_id=conversation.id,
            role="user",
            content=run.instruction,
            run_id=run.id,
            created_at=now,
        )
    )
    conversation.updated_at = now
    if conversation.title == "新的问答":
        conversation.title = run.instruction[:80]
    db.add(
        StreamEvent(
            id=uuid4(),
            owner_id=owner.id,
            run_id=run.id,
            seq=1,
            event_key="queued",
            event_type="run.step",
            payload={
                "run_id": str(run.id),
                "seq": 1,
                "stage": "queued",
                "message": "已加入问答队列",
            },
            created_at=now,
        )
    )
    await db.commit()
    return {
        "data": {
            "run_id": str(run.id),
            "status": run.status,
            "events_url": f"/agent/runs/{run.id}/events",
        },
        "request_id": request.state.request_id,
    }


async def _owned_run(db: AsyncSession, owner_id: UUID, run_id: UUID) -> AgentRun:
    run = await db.scalar(
        select(AgentRun).where(AgentRun.id == run_id, AgentRun.owner_id == owner_id)
    )
    if run is None:
        raise HTTPException(404, "RESOURCE_NOT_FOUND")
    return run


@router.get("/agent/runs/{run_id}")
async def get_run(
    run_id: UUID,
    request: Request,
    auth: Auth,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, object]:
    run = await _owned_run(db, auth[0].id, run_id)
    steps = await db.scalars(
        select(RunStep)
        .where(RunStep.run_id == run.id, RunStep.owner_id == run.owner_id)
        .order_by(RunStep.seq)
    )
    citations: list[dict[str, object]] = []
    if run.status == "succeeded":
        rows = await db.execute(
            select(Citation, Document.title, Document.state, Document.current_version_id)
            .join(
                Document,
                (Document.id == Citation.document_id) & (Document.owner_id == Citation.owner_id),
            )
            .where(
                Citation.run_id == run.id,
                Citation.owner_id == run.owner_id,
                Citation.used.is_(True),
            )
            .order_by(Citation.citation_key)
        )
        citations = [
            _citation_payload(c, title=title, state=state, current_version_id=current)
            for c, title, state, current in rows.all()
        ]
    return {
        "data": {
            "id": str(run.id),
            "mode": run.mode,
            "status": run.status,
            "scope_snapshot": run.scope,
            "steps": [
                {
                    "seq": step.seq,
                    "tool_name": step.tool_name,
                    "input": step.safe_input,
                    "output": step.safe_output,
                    "status": step.status,
                    "duration_ms": step.duration_ms,
                }
                for step in steps.all()
            ],
            "answer": run.answer,
            "citations": citations,
            "usage": run.token_usage,
            "budget": run.budget,
            "degraded": bool(run.budget.get("degraded_search", False)),
            "error": run.error_code,
            "retrieval_version": run.retrieval_version,
            "created_at": run.created_at.isoformat(),
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        },
        "request_id": request.state.request_id,
    }


@router.post("/agent/runs/{run_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_run(
    run_id: UUID,
    request: Request,
    auth: WriteAuth,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, object]:
    run = await db.scalar(
        select(AgentRun)
        .where(AgentRun.id == run_id, AgentRun.owner_id == auth[0].id)
        .with_for_update()
    )
    if run is None:
        raise HTTPException(404, "RESOURCE_NOT_FOUND")
    if run.status not in ("queued", "running") or run.task_id is None:
        raise HTTPException(409, "TASK_CONFLICT")
    task = await db.scalar(
        select(BackgroundTask)
        .where(BackgroundTask.id == run.task_id, BackgroundTask.owner_id == auth[0].id)
        .with_for_update()
    )
    if task is None:
        raise HTTPException(409, "TASK_CONFLICT")
    now = datetime.now(UTC)
    if task.status == "queued":
        task.status = "cancelled"
        task.stage = "cancelled"
        task.cancel_requested = True
        run.status = "cancelled"
        run.finished_at = now
        await append_event_async(db, run, "run.cancelled", "terminal", {"status": "cancelled"})
    elif task.status == "running":
        task.status = "cancel_requested"
        task.cancel_requested = True
        await append_event_async(
            db,
            run,
            "run.step",
            "cancel-requested",
            {"stage": "cancelling", "message": "正在停止本次问答"},
        )
    else:
        raise HTTPException(409, "TASK_CONFLICT")
    task.updated_at = now
    await db.commit()
    return {
        "data": {"run_id": str(run.id), "status": run.status},
        "request_id": request.state.request_id,
    }


@router.get("/agent/runs/{run_id}/events")
async def stream_run_events(
    run_id: UUID,
    request: Request,
    auth: Auth,
    db: Annotated[AsyncSession, Depends(get_db)],
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    run = await _owned_run(db, auth[0].id, run_id)
    cursor = _parse_last_event_id(last_event_id)
    if cursor >= run.next_event_seq:
        raise HTTPException(400, "CURSOR_INVALID")
    session_factory = async_sessionmaker(db.bind, expire_on_commit=False)
    cutoff = datetime.now(UTC) - timedelta(days=get_settings().event_retention_days)
    await db.execute(
        delete(StreamEvent).where(
            StreamEvent.owner_id == auth[0].id,
            StreamEvent.run_id == run_id,
            StreamEvent.created_at < cutoff,
        )
    )
    await db.commit()
    retained = await db.scalar(
        select(func.min(StreamEvent.seq)).where(
            StreamEvent.run_id == run_id, StreamEvent.owner_id == auth[0].id
        )
    )
    if (retained is not None and cursor < retained - 1) or (
        retained is None and run.next_event_seq > 1
    ):

        async def reset_stream() -> AsyncIterator[str]:
            payload = json.dumps({"run_id": str(run_id), "snapshot_url": f"/agent/runs/{run_id}"})
            yield f"id: {run.next_event_seq - 1}\nevent: stream.reset\n"
            yield f"data: {payload}\n\n"

        return StreamingResponse(
            reset_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    async def event_stream() -> AsyncIterator[str]:
        nonlocal cursor
        last_heartbeat = asyncio.get_running_loop().time()
        while True:
            if await request.is_disconnected():
                return
            async with session_factory() as stream_db:
                events = await stream_db.scalars(
                    select(StreamEvent)
                    .where(
                        StreamEvent.run_id == run_id,
                        StreamEvent.owner_id == auth[0].id,
                        StreamEvent.seq > cursor,
                    )
                    .order_by(StreamEvent.seq)
                    .limit(100)
                )
                batch = list(events.all())
                current_run = await stream_db.scalar(
                    select(AgentRun).where(AgentRun.id == run_id, AgentRun.owner_id == auth[0].id)
                )
            for event in batch:
                cursor = event.seq
                payload = json.dumps(event.payload, ensure_ascii=False, separators=(",", ":"))
                yield f"id: {event.seq}\nevent: {event.event_type}\n"
                yield f"data: {payload}\n\n"
            if current_run is None or (
                current_run.status in _TERMINAL_RUN_STATES
                and cursor >= current_run.next_event_seq - 1
            ):
                return
            now = asyncio.get_running_loop().time()
            if now - last_heartbeat >= 15:
                yield ": keep-alive\n\n"
                last_heartbeat = now
            await asyncio.sleep(1)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
