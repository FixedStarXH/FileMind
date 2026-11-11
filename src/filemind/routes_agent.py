"""Private, citation-first QA conversations and resumable run streams."""

import asyncio
import base64
import binascii
import hashlib
import json
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Annotated, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from filemind.agent_events import append_event_async
from filemind.agent_schemas import ApprovalCreate, ConversationCreate, RunCreate
from filemind.auth import Auth, WriteAuth
from filemind.config import get_settings
from filemind.database import get_db
from filemind.http_contracts import idempotency_key
from filemind.models import (
    AgentRun,
    Approval,
    AuditEvent,
    BackgroundTask,
    Chunk,
    Citation,
    Conversation,
    Document,
    DocumentVersion,
    Message,
    OrganizationPlan,
    RunStep,
    StreamEvent,
    User,
)
from filemind.routes_search import _scope_predicate
from filemind.search_schemas import SearchRequest
from filemind.task_contracts import enqueue_task

router = APIRouter(prefix="/api/v1", tags=["assistant"])
_TERMINAL_RUN_STATES = {"succeeded", "failed", "cancelled", "expired"}


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
    if body.mode in {"organize", "application_pack"} and (
        body.scope.all_documents
        or not (
            body.scope.document_ids
            or body.scope.folder_ids
            or body.scope.tag_ids
            or body.scope.inbox
        )
    ):
        raise HTTPException(422, "FINITE_SCOPE_REQUIRED")
    conversation: Conversation | None = None
    if body.conversation_id is not None:
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
        .where(
            AgentRun.owner_id == owner.id,
            AgentRun.status.in_(("queued", "running", "awaiting_approval")),
        )
    )
    if int(active_count or 0) >= config.agent_concurrency:
        raise HTTPException(429, "RATE_LIMITED", headers={"Retry-After": "30"})
    if conversation is not None:
        conversation_active = await db.scalar(
            select(func.count())
            .select_from(AgentRun)
            .where(
                AgentRun.owner_id == owner.id,
                AgentRun.conversation_id == conversation.id,
                AgentRun.status.in_(("queued", "running", "awaiting_approval")),
            )
        )
        if int(conversation_active or 0):
            raise HTTPException(409, "CONVERSATION_BUSY")
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
    if conversation is None and body.mode != "application_pack":
        conversation = Conversation(
            id=uuid4(),
            owner_id=owner.id,
            title=body.instruction.strip()[:80] or "资料整理",
            scope=cast(dict[str, object], body.scope.model_dump(mode="json")),
            created_at=now,
            updated_at=now,
        )
        db.add(conversation)
        await db.flush()
    run_id = uuid4()
    run = AgentRun(
        id=run_id,
        owner_id=owner.id,
        conversation_id=conversation.id if conversation is not None else None,
        mode=body.mode,
        instruction=body.instruction.strip(),
        scope=cast(dict[str, object], body.scope.model_dump(mode="json")),
        status="queued",
        graph_thread_id=str(uuid4()),
        budget={"llm_calls": 0, "tool_calls": 0, "max_llm_calls": config.run_max_llm_calls},
        model=config.deepseek_model,
        prompt_version=(
            "filemind-qa-1"
            if body.mode == "qa"
            else "filemind-application-pack-1"
            if body.mode == "application_pack"
            else "filemind-organize-1"
        ),
        next_event_seq=2,
        created_at=now,
    )
    task = enqueue_task(db, owner.id, "agent_run", run_id, stage="queued")
    await db.flush()
    run.task_id = task.id
    db.add(run)
    if conversation is not None:
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
        if body.mode == "qa" and conversation.title == "新的问答":
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
                "message": (
                    "已加入问答队列"
                    if body.mode == "qa"
                    else "已加入资料包分析队列"
                    if body.mode == "application_pack"
                    else "已加入整理队列"
                ),
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
    plan_id = (
        await db.scalar(
            select(OrganizationPlan.id)
            .where(OrganizationPlan.run_id == run.id, OrganizationPlan.owner_id == run.owner_id)
            .order_by(OrganizationPlan.revision.desc())
            .limit(1)
        )
        if run.mode in {"organize", "application_pack"}
        else None
    )
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
            "plan_id": str(plan_id) if plan_id else None,
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


def _plan_payload(plan: OrganizationPlan) -> dict[str, object]:
    return {
        "id": str(plan.id),
        "run_id": str(plan.run_id),
        "revision": plan.revision,
        "plan_hash": plan.plan_hash,
        "status": plan.status,
        "expires_at": plan.expires_at.isoformat(),
        "actions": plan.actions,
        "missing_items": plan.missing_items,
    }


@router.get("/agent/runs/{run_id}/plan")
async def get_run_plan(
    run_id: UUID,
    request: Request,
    auth: Auth,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, object]:
    run = await _owned_run(db, auth[0].id, run_id)
    plan = await db.scalar(
        select(OrganizationPlan)
        .where(OrganizationPlan.run_id == run.id, OrganizationPlan.owner_id == run.owner_id)
        .order_by(OrganizationPlan.revision.desc())
        .limit(1)
    )
    if plan is None:
        raise HTTPException(404, "PLAN_NOT_READY")
    payload = _plan_payload(plan)
    if run.mode == "application_pack":
        action_items = cast(list[dict[str, object]], json.loads(json.dumps(plan.actions)))
        source_ids = {
            UUID(source_id)
            for action in action_items
            for source_id in cast(list[str], action.get("citation_ids", []))
        }
        source_rows = (
            (
                await db.execute(
                    select(Chunk, Document.id, Document.title)
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
                    .where(
                        Chunk.owner_id == auth[0].id,
                        Chunk.id.in_(source_ids),
                        Document.state == "active",
                        Document.current_version_id == Chunk.version_id,
                        DocumentVersion.active_generation == Chunk.generation,
                    )
                    .limit(400)
                )
            ).all()
            if source_ids
            else []
        )
        sources_by_id = {
            str(chunk.id): {
                "id": str(chunk.id),
                "document_id": str(document_id),
                "title": title,
                "locator": chunk.locator,
                "excerpt": chunk.text[:500],
            }
            for chunk, document_id, title in source_rows
        }
        for action in action_items:
            action["sources"] = [
                sources_by_id[source_id]
                for source_id in cast(list[str], action.get("citation_ids", []))
                if source_id in sources_by_id
            ]
        payload["actions"] = action_items
    return {"data": payload, "request_id": request.state.request_id}


def _audit_cursor(created_at: datetime, event_id: UUID) -> str:
    raw = f"{created_at.isoformat()}|{event_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _parse_audit_cursor(value: str) -> tuple[datetime, UUID]:
    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(value + padding).decode()
        created_at_text, event_id_text = decoded.split("|", 1)
        created_at = datetime.fromisoformat(created_at_text)
        event_id = UUID(event_id_text)
        if created_at.tzinfo is None:
            raise ValueError("cursor timestamp must include timezone")
        return created_at, event_id
    except (ValueError, UnicodeDecodeError, binascii.Error):
        raise HTTPException(400, "CURSOR_INVALID") from None


@router.get("/audit-events")
async def list_audit_events(
    request: Request,
    auth: Auth,
    db: Annotated[AsyncSession, Depends(get_db)],
    resource_id: UUID | None = None,
    cursor: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, object]:
    query = select(AuditEvent).where(AuditEvent.owner_id == auth[0].id)
    if resource_id is not None:
        query = query.where(AuditEvent.resource_id == resource_id)
    if cursor is not None:
        created_at, event_id = _parse_audit_cursor(cursor)
        query = query.where(
            or_(
                AuditEvent.created_at < created_at,
                (AuditEvent.created_at == created_at) & (AuditEvent.id < event_id),
            )
        )
    rows = list(
        (
            await db.scalars(
                query.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(limit + 1)
            )
        ).all()
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = _audit_cursor(page[-1].created_at, page[-1].id) if has_more and page else None
    return {
        "data": {
            "items": [
                {
                    "id": str(event.id),
                    "action": event.action,
                    "resource_type": event.resource_type,
                    "resource_id": str(event.resource_id),
                    "request_id": event.request_id,
                    "details": event.details,
                    "created_at": event.created_at.isoformat(),
                }
                for event in page
            ],
            "next_cursor": next_cursor,
        },
        "request_id": request.state.request_id,
    }


@router.post("/agent/runs/{run_id}/approval", status_code=status.HTTP_202_ACCEPTED)
async def submit_approval(
    run_id: UUID,
    body: ApprovalCreate,
    request: Request,
    auth: WriteAuth,
    db: Annotated[AsyncSession, Depends(get_db)],
    idempotency_header: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, object]:
    key = idempotency_key(idempotency_header)
    request_data = {
        "plan_id": str(body.plan_id),
        "plan_hash": body.plan_hash,
        "decision": body.decision,
        "selected_action_ids": sorted(body.selected_action_ids),
    }
    request_hash = hashlib.sha256(
        json.dumps(request_data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    run = await db.scalar(
        select(AgentRun)
        .where(AgentRun.id == run_id, AgentRun.owner_id == auth[0].id)
        .with_for_update()
    )
    if run is None or run.mode not in {"organize", "application_pack"}:
        raise HTTPException(404, "RESOURCE_NOT_FOUND")
    plan = await db.scalar(
        select(OrganizationPlan)
        .where(
            OrganizationPlan.id == body.plan_id,
            OrganizationPlan.run_id == run.id,
            OrganizationPlan.owner_id == auth[0].id,
        )
        .with_for_update()
    )
    if plan is None:
        raise HTTPException(404, "PLAN_NOT_READY")
    prior = await db.scalar(
        select(Approval)
        .where(Approval.plan_id == plan.id, Approval.owner_id == auth[0].id)
        .with_for_update()
    )
    if prior is not None:
        if prior.request_key == key and prior.request_hash == request_hash:
            return {
                "data": prior.response_body,
                "request_id": request.state.request_id,
            }
        raise HTTPException(409, "ALREADY_DECIDED")
    now = datetime.now(UTC)
    if plan.plan_hash != body.plan_hash or plan.status != "awaiting_approval":
        raise HTTPException(409, "STALE_PLAN")
    if plan.expires_at <= now:
        plan.status = "expired"
        run.status = "expired"
        run.error_code = "PLAN_EXPIRED"
        run.finished_at = now
        await append_event_async(
            db,
            run,
            "run.expired",
            "terminal",
            {"status": "expired", "error": "PLAN_EXPIRED"},
        )
        await db.commit()
        raise HTTPException(409, "PLAN_EXPIRED")
    known = {str(item.get("id")) for item in plan.actions}
    if any(item not in known for item in body.selected_action_ids):
        raise HTTPException(422, "ACTION_NOT_IN_PLAN")
    if body.decision == "approve":
        owner = await db.scalar(select(User).where(User.id == auth[0].id).with_for_update())
        if owner is None or not owner.ai_enabled:
            raise HTTPException(403, "AI_DISABLED")
        action_map = {str(item["id"]): item for item in plan.actions}
        document_ids = sorted(
            {UUID(str(action_map[item]["document_id"])) for item in body.selected_action_ids},
            key=str,
        )
        docs = list(
            await db.scalars(
                select(Document)
                .where(
                    Document.owner_id == auth[0].id,
                    Document.id.in_(document_ids),
                    Document.state == "active",
                )
                .order_by(Document.id)
                .with_for_update()
            )
        )
        by_id = {item.id: item for item in docs}
        stale = set(by_id) != set(document_ids)
        for action_id in body.selected_action_ids:
            action = action_map[action_id]
            document = by_id.get(UUID(str(action["document_id"])))
            if document is None or (
                document.metadata_revision != int(cast(int, action["expected_revision"]))
                or (str(document.current_version_id) if document.current_version_id else None)
                != action.get("version_id")
            ):
                stale = True
        if stale:
            plan.status = "stale"
            plan.updated_at = now
            run.status = "failed"
            run.error_code = "STALE_PLAN"
            run.finished_at = now
            await append_event_async(
                db,
                run,
                "run.failed",
                "terminal",
                {"status": "failed", "error": "STALE_PLAN"},
            )
            db.add(
                AuditEvent(
                    owner_id=auth[0].id,
                    actor_id=auth[0].id,
                    action="organization.plan.stale",
                    resource_type="plan",
                    resource_id=plan.id,
                    request_id=request.state.request_id,
                    details={"reason": "document_changed"},
                )
            )
            await db.commit()
            raise HTTPException(409, "STALE_PLAN")

    approval_id = uuid4()
    if body.decision == "reject":
        plan.status = "rejected"
        plan.updated_at = now
        run.status = "cancelled"
        run.finished_at = now
        execution_status = "rejected"
        response_data: dict[str, object] = {
            "run_id": str(run.id),
            "status": "cancelled",
            "approval_id": str(approval_id),
        }
        await append_event_async(
            db,
            run,
            "run.cancelled",
            "terminal",
            {"status": "cancelled", "reason": "approval_rejected"},
        )
    else:
        plan.status = "approved"
        plan.updated_at = now
        run.status = "queued"
        task = enqueue_task(db, auth[0].id, "agent_run", run.id, stage="approved_resume")
        await db.flush()
        run.task_id = task.id
        execution_status = "queued"
        response_data = {
            "run_id": str(run.id),
            "status": "queued",
            "task_id": str(task.id),
            "approval_id": str(approval_id),
        }
        await append_event_async(
            db,
            run,
            "run.step",
            "approval.queued",
            {
                "stage": "approved",
                "message": "已确认所选材料，正在生成资料包"
                if run.mode == "application_pack"
                else "已确认所选建议，正在应用",
            },
        )
    db.add(
        Approval(
            id=approval_id,
            owner_id=auth[0].id,
            plan_id=plan.id,
            plan_hash=plan.plan_hash,
            selected_action_ids=body.selected_action_ids,
            decision=body.decision,
            execution_status=execution_status,
            request_key=key,
            request_hash=request_hash,
            response_status=202,
            response_body=response_data,
            decided_at=now,
        )
    )
    db.add(
        AuditEvent(
            owner_id=auth[0].id,
            actor_id=auth[0].id,
            action=(
                "application_pack.approval.submitted"
                if run.mode == "application_pack"
                else "organization.approval.submitted"
            ),
            resource_type="plan",
            resource_id=plan.id,
            request_id=request.state.request_id,
            details={
                "decision": body.decision,
                "selected_action_count": len(body.selected_action_ids),
                "approval_id": str(approval_id),
            },
        )
    )
    await db.commit()
    return {"data": response_data, "request_id": request.state.request_id}


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
    if run.status == "awaiting_approval" and run.mode in {"organize", "application_pack"}:
        plan = await db.scalar(
            select(OrganizationPlan)
            .where(OrganizationPlan.run_id == run.id, OrganizationPlan.owner_id == run.owner_id)
            .with_for_update()
        )
        now = datetime.now(UTC)
        run.status = "cancelled"
        run.finished_at = now
        if plan is not None and plan.status == "awaiting_approval":
            plan.status = "cancelled"
            plan.updated_at = now
        await append_event_async(db, run, "run.cancelled", "terminal", {"status": "cancelled"})
        await db.commit()
        return {
            "data": {"run_id": str(run.id), "status": run.status},
            "request_id": request.state.request_id,
        }
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
        if run.mode in {"organize", "application_pack"}:
            plan = await db.scalar(
                select(OrganizationPlan)
                .where(OrganizationPlan.run_id == run.id, OrganizationPlan.owner_id == run.owner_id)
                .with_for_update()
            )
            if plan is not None and plan.status not in ("completed", "rejected"):
                plan.status = "cancelled"
                plan.updated_at = now
                approval = await db.scalar(
                    select(Approval)
                    .where(Approval.plan_id == plan.id, Approval.owner_id == plan.owner_id)
                    .with_for_update()
                )
                if approval is not None and approval.execution_status == "queued":
                    approval.execution_status = "cancelled"
                    approval.finished_at = now
        await append_event_async(db, run, "run.cancelled", "terminal", {"status": "cancelled"})
    elif task.status == "running":
        task.status = "cancel_requested"
        task.cancel_requested = True
        if run.mode in {"organize", "application_pack"}:
            plan = await db.scalar(
                select(OrganizationPlan)
                .where(OrganizationPlan.run_id == run.id, OrganizationPlan.owner_id == run.owner_id)
                .with_for_update()
            )
            if plan is not None and plan.status == "approved":
                plan.status = "cancelled"
                plan.updated_at = now
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
