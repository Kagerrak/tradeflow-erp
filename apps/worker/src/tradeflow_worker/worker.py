from __future__ import annotations

from typing import Any, cast
from uuid import UUID

import structlog
from arq import cron
from arq.connections import RedisSettings
from arq.typing import WorkerCoroutine
from sqlalchemy import exists, func, select, text, update
from tradeflow_api.database import create_database_engine, create_session_factory
from tradeflow_api.delivery_confirmation_outbox import (
    HANDLER_NAME,
    RECEIPT_HANDLER_NAME,
    create_draft_invoice_for_event,
    render_delivery_receipt_for_event,
)
from tradeflow_api.models import (
    delivery_receipt_documents,
    outbox_events,
    outbox_handler_receipts,
    outbox_processing_state,
)
from tradeflow_api.object_storage import S3ObjectStorage

from tradeflow_worker.config import get_worker_settings

logger = structlog.get_logger()


async def startup(context: dict[str, Any]) -> None:
    settings = get_worker_settings()
    engine = create_database_engine(settings.database_url)
    context["database_engine"] = engine
    context["database_session_factory"] = create_session_factory(engine)
    context["object_storage"] = S3ObjectStorage(settings)
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


async def process_delivery_receipt_event(
    context: dict[str, Any],
    outbox_event_id: str,
) -> dict[str, str]:
    factory = context["database_session_factory"]
    async with factory() as session, session.begin():
        receipt_id = await render_delivery_receipt_for_event(
            session,
            UUID(outbox_event_id),
            context["object_storage"],
        )
    return {"delivery_receipt_id": str(receipt_id), "status": "ready"}


async def poll_delivery_confirmation_outbox(context: dict[Any, Any]) -> Any:
    factory = context["database_session_factory"]
    async with factory() as session, session.begin():
        event_ids = list(
            (
                await session.scalars(
                    select(outbox_events.c.outbox_event_id)
                    .select_from(
                        outbox_events.join(
                            outbox_processing_state,
                            outbox_processing_state.c.outbox_event_id
                            == outbox_events.c.outbox_event_id,
                        )
                    )
                    .where(
                        outbox_events.c.event_type == "delivery.confirmed.v1",
                        outbox_processing_state.c.available_at <= func.now(),
                        (
                            ~exists().where(
                                outbox_handler_receipts.c.outbox_event_id
                                == outbox_events.c.outbox_event_id,
                                outbox_handler_receipts.c.handler_name == HANDLER_NAME,
                            )
                            | ~exists().where(
                                outbox_handler_receipts.c.outbox_event_id
                                == outbox_events.c.outbox_event_id,
                                outbox_handler_receipts.c.handler_name == RECEIPT_HANDLER_NAME,
                            )
                        ),
                    )
                    .order_by(outbox_events.c.occurred_at, outbox_events.c.outbox_event_id)
                    .limit(50)
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
    completed = 0
    failed = 0
    for event_id in event_ids:
        async with factory() as session, session.begin():
            await session.execute(
                update(outbox_processing_state)
                .where(outbox_processing_state.c.outbox_event_id == event_id)
                .values(
                    status="processing",
                    attempts=outbox_processing_state.c.attempts + 1,
                    last_error=None,
                )
            )
        try:
            async with factory() as session, session.begin():
                await create_draft_invoice_for_event(session, event_id)
                await render_delivery_receipt_for_event(
                    session,
                    event_id,
                    context["object_storage"],
                )
                await session.execute(
                    update(outbox_processing_state)
                    .where(outbox_processing_state.c.outbox_event_id == event_id)
                    .values(status="completed", processed_at=func.now())
                )
            completed += 1
        except Exception as error:
            async with factory() as session, session.begin():
                event_payload = await session.scalar(
                    select(outbox_events.c.payload).where(
                        outbox_events.c.outbox_event_id == event_id
                    )
                )
                receipt_id = (
                    event_payload.get("delivery_receipt_id")
                    if isinstance(event_payload, dict)
                    else None
                )
                if isinstance(receipt_id, str):
                    await session.execute(
                        update(delivery_receipt_documents)
                        .where(delivery_receipt_documents.c.delivery_receipt_id == UUID(receipt_id))
                        .values(status="unavailable", last_error=str(error)[:2000])
                    )
                await session.execute(
                    update(outbox_processing_state)
                    .where(outbox_processing_state.c.outbox_event_id == event_id)
                    .values(
                        status="failed",
                        available_at=func.now() + text("interval '1 minute'"),
                        last_error=str(error)[:2000],
                    )
                )
            failed += 1
    return {"completed": completed, "failed": failed}


class Worker:
    settings = get_worker_settings()
    functions = [
        heartbeat,
        process_delivery_confirmation_event,
        process_delivery_receipt_event,
    ]
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
