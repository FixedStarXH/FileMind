"""Publish database outbox events and recover expired task leases."""

import logging
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from filemind.celery_app import process_task
from filemind.config import get_settings
from filemind.models import BackgroundTask, DocumentVersion, OutboxEvent

logger = logging.getLogger("filemind.dispatcher")
_LEASE_RECOVERY_LIMIT = 100
_QUEUED_RECONCILE_SECONDS = 300


def _engine_url() -> str:
    url = get_settings().database_url
    if not url:
        raise RuntimeError("DATABASE_URL is required for the dispatcher")
    return url


def _queue_for(task: BackgroundTask) -> str:
    if task.kind == "parse":
        return "ingestion"
    if task.kind == "agent_run":
        return "agent"
    return "maintenance"


def _new_outbox(task: BackgroundTask, due: datetime | None = None) -> OutboxEvent:
    return OutboxEvent(
        event_type="task.queued",
        aggregate_id=task.id,
        dispatch_seq=task.dispatch_seq,
        payload={"task_id": str(task.id)},
        next_attempt_at=due or datetime.now(UTC),
    )


def _mark_version_parse_state(session: Session, task: BackgroundTask, state: str) -> None:
    if task.kind != "parse" or task.version_id is None:
        return
    version = session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.id == task.version_id,
            DocumentVersion.owner_id == task.owner_id,
        )
    )
    if version is not None:
        version.parse_state = state


def recover_expired_leases(session: Session, now: datetime | None = None) -> int:
    current = now or datetime.now(UTC)
    tasks = list(
        session.scalars(
            select(BackgroundTask)
            .where(
                BackgroundTask.status.in_(("running", "cancel_requested")),
                BackgroundTask.lease_until <= current,
            )
            .order_by(BackgroundTask.lease_until, BackgroundTask.id)
            .limit(_LEASE_RECOVERY_LIMIT)
            .with_for_update(skip_locked=True)
        )
    )
    recovered = 0
    for task in tasks:
        task.lease_until = None
        task.heartbeat_at = None
        task.updated_at = current
        if task.status == "cancel_requested":
            task.status = "cancelled"
            task.stage = "cancelled"
            _mark_version_parse_state(session, task, "cancelled")
            continue
        if task.attempt >= 3:
            task.status = "failed"
            task.stage = "failed"
            task.error_code = "WORKER_LOST"
            _mark_version_parse_state(session, task, "failed")
            continue
        task.status = "queued"
        task.stage = "recovering"
        task.error_code = "WORKER_LOST"
        _mark_version_parse_state(session, task, "queued")
        task.dispatch_seq += 1
        task.last_enqueued_at = None
        session.add(_new_outbox(task, current))
        recovered += 1

    queued = list(
        session.scalars(
            select(BackgroundTask)
            .where(
                BackgroundTask.status == "queued",
                BackgroundTask.stage != "waiting_parser",
                BackgroundTask.last_enqueued_at
                <= current - timedelta(seconds=_QUEUED_RECONCILE_SECONDS),
            )
            .order_by(BackgroundTask.last_enqueued_at, BackgroundTask.id)
            .limit(_LEASE_RECOVERY_LIMIT)
            .with_for_update(skip_locked=True)
        )
    )
    for task in queued:
        task.dispatch_seq += 1
        task.last_enqueued_at = current
        task.updated_at = current
        session.add(_new_outbox(task, current))
        recovered += 1
    return recovered


def dispatch_ready(session: Session, batch_size: int = 50) -> int:
    now = datetime.now(UTC)
    events = list(
        session.scalars(
            select(OutboxEvent)
            .where(
                OutboxEvent.published_at.is_(None),
                OutboxEvent.next_attempt_at <= now,
            )
            .order_by(OutboxEvent.created_at, OutboxEvent.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
    )
    published = 0
    for event in events:
        task = session.get(BackgroundTask, event.aggregate_id)
        if task is None or task.status != "queued":
            event.published_at = now
            continue
        queue = _queue_for(task)
        if queue == "ingestion" and not get_settings().enable_ingestion_worker:
            event.next_attempt_at = now + timedelta(seconds=10)
            continue
        try:
            process_task.apply_async(
                args=[str(task.id)],
                queue=queue,
                task_id=str(uuid4()),
                ignore_result=True,
            )
        except Exception:
            event.attempt += 1
            event.next_attempt_at = now + timedelta(seconds=min(60, 2**event.attempt))
            logger.warning("outbox publish failed event_id=%s", event.id)
            continue
        event.published_at = now
        task.last_enqueued_at = now
        published += 1
    return published


def run_once(engine: Engine) -> tuple[int, int]:
    with Session(engine) as session, session.begin():
        recovered = recover_expired_leases(session)
        published = dispatch_ready(session)
    from filemind.export_runtime import cleanup_obsolete_exports

    cleanup_obsolete_exports(engine)
    return recovered, published


def run_forever() -> None:
    engine = create_engine(_engine_url(), pool_pre_ping=True)
    logger.info("dispatcher started")
    try:
        while True:
            try:
                recovered, published = run_once(engine)
                if recovered or published:
                    logger.info("dispatcher tick recovered=%d published=%d", recovered, published)
            except Exception:
                logger.exception("dispatcher tick failed")
            time.sleep(1)
    finally:
        engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_forever()
