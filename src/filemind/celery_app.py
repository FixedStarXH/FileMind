"""Celery configuration; PostgreSQL remains the source of task state."""

from celery import Celery  # type: ignore[import-untyped]
from kombu import Exchange, Queue  # type: ignore[attr-defined]

from filemind.config import get_settings

settings = get_settings()
if not settings.redis_url:
    raise RuntimeError("REDIS_URL is required for task workers")

maintenance_exchange = Exchange("maintenance", type="direct", durable=True)  # type: ignore[no-untyped-call]
ingestion_exchange = Exchange("ingestion", type="direct", durable=True)  # type: ignore[no-untyped-call]
agent_exchange = Exchange("agent", type="direct", durable=True)  # type: ignore[no-untyped-call]
maintenance_queue = Queue(
    "maintenance", exchange=maintenance_exchange, routing_key="maintenance", durable=True
)  # type: ignore[no-untyped-call]
ingestion_queue = Queue(
    "ingestion", exchange=ingestion_exchange, routing_key="ingestion", durable=True
)  # type: ignore[no-untyped-call]
agent_queue = Queue("agent", exchange=agent_exchange, routing_key="agent", durable=True)  # type: ignore[no-untyped-call]

celery_app = Celery("filemind", broker=settings.redis_url)
celery_app.conf.update(
    task_default_queue="maintenance",
    task_default_exchange="maintenance",
    task_default_routing_key="maintenance",
    task_queues=(
        maintenance_queue,
        ingestion_queue,
        agent_queue,
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
