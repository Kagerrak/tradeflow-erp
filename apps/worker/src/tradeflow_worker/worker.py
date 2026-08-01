from __future__ import annotations

from typing import Any, cast
from uuid import UUID

import structlog
from arq import cron
from arq.connections import RedisSettings
from arq.typing import WorkerCoroutine
from sqlalchemy import exists, select
from tradeflow_api.database import create_database_engine, create_session_factory
from tradeflow_api.delivery_confirmation_outbox import (
    HANDLER_NAME,
    create_draft_invoice_for_event,
)
from tradeflow_api.models import outbox_events, outbox_handler_receipts

from tradeflow_worker.config import get_worker_settings

logger = structlog.get_logger()


async def startup(context: dict[str, Any]) -> None:
    settings = get_worker_settings()
    engine = create_database_engine(settings.database_url)
    context["database_engine"] = engine
    context["database_session_factory"] = create_session_factory(engine)
    await logger.ainfo("worker_started", service="tradeflow-worker")


async def shutdown(context: dict[str, Any]) -> None:
    await context["database_engine"].dispose()
    await logger.ainfo("worker_stopped", service="tradeflow-worker")


async def heartbeat(_: dict[str, Any]) -> dict[str, str]:
    return {"service": "tradeflow-worker", "status": "ok"}


async def process_delivery_confirmation_event(
    context: dict[str, Any],
    outbox_event_id: str,
) -> dict[str, str]:
    factory = context["database_session_factory"]
    async with factory() as session, session.begin():
        invoice_id = await create_draft_invoice_for_event(
            session,
            UUID(outbox_event_id),
        )
    return {"draft_invoice_id": str(invoice_id), "status": "draft"}


async def poll_delivery_confirmation_outbox(context: dict[Any, Any]) -> Any:
    factory = context["database_session_factory"]
    async with factory() as session, session.begin():
        event_ids = list(
            (
                await session.scalars(
                    select(outbox_events.c.outbox_event_id)
                    .where(
                        outbox_events.c.event_type == "delivery.confirmed.v1",
                        ~exists().where(
                            outbox_handler_receipts.c.outbox_event_id
                            == outbox_events.c.outbox_event_id,
                            outbox_handler_receipts.c.handler_name == HANDLER_NAME,
                        ),
                    )
                    .order_by(outbox_events.c.occurred_at, outbox_events.c.outbox_event_id)
                    .limit(50)
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        for event_id in event_ids:
            await create_draft_invoice_for_event(session, event_id)
    return {"processed": len(event_ids)}


class Worker:
    settings = get_worker_settings()
    functions = [heartbeat, process_delivery_confirmation_event]
    cron_jobs = [
        cron(
            cast(WorkerCoroutine, poll_delivery_confirmation_outbox),
            second={0, 15, 30, 45},
            unique=True,
        )
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 10
    job_timeout = 300
