"""Idempotent, database-backed run events and progress steps."""

from datetime import UTC, datetime
from time import perf_counter
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from filemind.models import AgentRun, RunStep, StreamEvent


def append_event(
    session: Session,
    run: AgentRun,
    event_type: str,
    event_key: str,
    payload: dict[str, object],
) -> StreamEvent:
    existing = session.scalar(
        select(StreamEvent).where(
            StreamEvent.run_id == run.id,
            StreamEvent.event_key == event_key,
        )
    )
    if existing is not None:
        return existing
    sequence = run.next_event_seq
    run.next_event_seq = sequence + 1
    event = StreamEvent(
        id=uuid4(),
        owner_id=run.owner_id,
        run_id=run.id,
        seq=sequence,
        event_key=event_key,
        event_type=event_type,
        payload={"run_id": str(run.id), "seq": sequence, **payload},
        created_at=datetime.now(UTC),
    )
    session.add(event)
    session.flush()
    return event


async def append_event_async(
    session: AsyncSession,
    run: AgentRun,
    event_type: str,
    event_key: str,
    payload: dict[str, object],
) -> None:
    existing = await session.scalar(
        select(StreamEvent.id).where(
            StreamEvent.run_id == run.id,
            StreamEvent.event_key == event_key,
        )
    )
    if existing is not None:
        return
    sequence = run.next_event_seq
    run.next_event_seq = sequence + 1
    session.add(
        StreamEvent(
            id=uuid4(),
            owner_id=run.owner_id,
            run_id=run.id,
            seq=sequence,
            event_key=event_key,
            event_type=event_type,
            payload={"run_id": str(run.id), "seq": sequence, **payload},
            created_at=datetime.now(UTC),
        )
    )


def record_step(
    session: Session,
    run: AgentRun,
    *,
    tool_name: str,
    safe_input: dict[str, object],
    safe_output: dict[str, object],
    status: str,
    started: float,
) -> None:
    next_seq = session.scalar(
        select(func.coalesce(func.max(RunStep.seq), 0) + 1).where(RunStep.run_id == run.id)
    )
    session.add(
        RunStep(
            owner_id=run.owner_id,
            run_id=run.id,
            seq=int(next_seq or 1),
            tool_name=tool_name,
            safe_input=safe_input,
            safe_output=safe_output,
            status=status,
            duration_ms=max(0, int((perf_counter() - started) * 1000)),
        )
    )
