"""Celery configuration; PostgreSQL remains the source of task state."""

from celery import Celery  # type: ignore[import-untyped]
from kombu import Queue  # type: ignore[attr-defined]

from filemind.config import get_settings

settings = get_settings()
if not settings.redis_url:
    raise RuntimeError("REDIS_URL is required for task workers")

celery_app = Celery("filemind", broker=settings.redis_url)
celery_app.conf.update(
    task_default_queue="maintenance",
    task_queues=(
        Queue("maintenance", durable=True),  # type: ignore[no-untyped-call]
        Queue("ingestion", durable=True),  # type: ignore[no-untyped-call]
    ),
    task_serializer="json",
    accept_content=["json"],
    result_backend=None,
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": 180},
)


@celery_app.task(  # type: ignore[untyped-decorator]
    name="filemind.process_task", acks_late=True, reject_on_worker_lost=True
)
def process_task(task_id: str) -> None:
    from filemind.task_worker import execute_task

    execute_task(task_id)
