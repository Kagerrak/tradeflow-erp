"""Transactional-outbox processing shared by the API and worker tiers.

The API commits business state and its outbox rows in one PostgreSQL
transaction.  A separate step publishes a job for every outbox event that still
has unfinished handlers.  The worker then runs those handlers idempotently.

Handlers are grouped (delivery documents and finance, then notifications) and
each group is a separate job.  That mirrors the previous two-poller worker: a
notification that cannot be delivered does not roll back or re-run the finance
work, and each group keeps its own retry and dead-letter behaviour.

Two properties matter and are preserved from the previous Redis/ARQ worker:

* **No lost event.**  A committed outbox row is the source of truth.  If the
  publish step fails, the row is still unfinished and the next bounded dispatch
  pass re-publishes it.  Publishing is idempotent because the job marker key is
  derived from the event id and handler group.
* **No duplicate effect.**  Every handler records an ``outbox_handler_receipts``
  row before it is considered done, so a redelivered job is a no-op.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import ColumnElement, and_, exists, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tradeflow_api.delivery_confirmation_outbox import (
    CORRECTION_HANDLER_NAME,
    CORRECTION_RECEIPT_HANDLER_NAME,
    HANDLER_NAME,
    RECEIPT_HANDLER_NAME,
    create_corrected_draft_invoices_for_event,
    create_draft_invoice_for_event,
    render_corrected_delivery_receipt_for_event,
    render_delivery_receipt_for_event,
)
from tradeflow_api.models import (
    delivery_receipt_documents,
    outbox_events,
    outbox_handler_receipts,
    outbox_processing_state,
)
from tradeflow_api.notification_outbox import (
    HANDLER_NAME as NOTIFICATION_HANDLER_NAME,
)
from tradeflow_api.notification_outbox import (
    create_notifications_for_event,
)
from tradeflow_api.object_storage import ObjectStorage

logger = logging.getLogger(__name__)

HandlerGroup = Literal["delivery", "notifications"]

NOTIFICATION_HANDLER_NAMES = (NOTIFICATION_HANDLER_NAME,)
# Which handlers each event type owes, per group.  A confirmation has no
# correction handler, so a group is only "unfinished" when the handlers that
# apply to *that* event type are missing.
HANDLER_GROUPS: dict[str, dict[str, tuple[str, ...]]] = {
    "delivery.confirmed.v1": {
        "delivery": (HANDLER_NAME, RECEIPT_HANDLER_NAME),
        "notifications": NOTIFICATION_HANDLER_NAMES,
    },
    "delivery.correction.posted.v1": {
        "delivery": (CORRECTION_HANDLER_NAME, CORRECTION_RECEIPT_HANDLER_NAME),
        "notifications": NOTIFICATION_HANDLER_NAMES,
    },
}
DELIVERY_EVENT_TYPES = tuple(HANDLER_GROUPS)
DISPATCH_LOOKBACK_DAYS = 7


@dataclass(frozen=True, slots=True)
class OutboxJobAddress:
    """Identifies one handler group of one outbox event."""

    outbox_event_id: UUID
    handler_group: str

    @property
    def marker_id(self) -> str:
        return f"{self.outbox_event_id}:{self.handler_group}"


@dataclass(frozen=True, slots=True)
class OutboxProcessingResult:
    outbox_event_id: str
    handler_group: str
    completed_handlers: tuple[str, ...]


def _any_handler_missing(handler_names: tuple[str, ...]) -> ColumnElement[bool]:
    clauses = [
        ~exists().where(
            outbox_handler_receipts.c.outbox_event_id == outbox_events.c.outbox_event_id,
            outbox_handler_receipts.c.handler_name == handler_name,
        )
        for handler_name in handler_names
    ]
    return or_(*clauses)


def _required_handlers(event_type: str, group: str) -> tuple[str, ...]:
    return HANDLER_GROUPS.get(event_type, {}).get(group, ())


async def _candidate_events(
    session: AsyncSession,
    *,
    limit: int,
    groups: tuple[str, ...],
) -> list[tuple[UUID, str]]:
    conditions = []
    for event_type, group_map in HANDLER_GROUPS.items():
        names = tuple(name for group in groups for name in group_map.get(group, ()))
        if not names:
            continue
        conditions.append(
            and_(outbox_events.c.event_type == event_type, _any_handler_missing(names))
        )
    if not conditions:
        return []
    statement = (
        select(outbox_events.c.outbox_event_id, outbox_events.c.event_type)
        .select_from(
            outbox_events.join(
                outbox_processing_state,
                outbox_processing_state.c.outbox_event_id == outbox_events.c.outbox_event_id,
            )
        )
        .where(
            outbox_processing_state.c.available_at <= func.now(),
            outbox_events.c.occurred_at > func.now() - text("interval '7 days'"),
            or_(*conditions),
        )
        .order_by(outbox_events.c.occurred_at, outbox_events.c.outbox_event_id)
        .limit(limit)
    )
    return [(row[0], str(row[1])) for row in (await session.execute(statement)).all()]


async def pending_outbox_jobs(
    session: AsyncSession,
    *,
    limit: int,
    groups: tuple[str, ...] = ("delivery", "notifications"),
) -> list[OutboxJobAddress]:
    """Return unfinished (event, handler group) pairs, oldest event first.

    Bounded by ``limit`` events and a lookback window so a dispatch pass is a
    cheap, predictable read rather than a table scan.
    """
    candidates = await _candidate_events(session, limit=limit, groups=groups)
    if not candidates:
        return []
    event_ids = [event_id for event_id, _ in candidates]
    receipts = await session.execute(
        select(
            outbox_handler_receipts.c.outbox_event_id,
            outbox_handler_receipts.c.handler_name,
        ).where(outbox_handler_receipts.c.outbox_event_id.in_(event_ids))
    )
    recorded: dict[UUID, set[str]] = defaultdict(set)
    for event_id, handler_name in receipts.all():
        recorded[event_id].add(str(handler_name))
    jobs: list[OutboxJobAddress] = []
    for event_id, event_type in candidates:
        for group in groups:
            required = _required_handlers(event_type, group)
            if required and not set(required) <= recorded[event_id]:
                jobs.append(OutboxJobAddress(outbox_event_id=event_id, handler_group=group))
    return jobs


async def _run_delivery_handlers(
    session: AsyncSession,
    event_id: UUID,
    *,
    event_type: str,
    object_storage: ObjectStorage,
) -> tuple[str, ...]:
    if event_type == "delivery.confirmed.v1":
        await create_draft_invoice_for_event(session, event_id)
        await render_delivery_receipt_for_event(session, event_id, object_storage)
        return (HANDLER_NAME, RECEIPT_HANDLER_NAME)
    await create_corrected_draft_invoices_for_event(session, event_id)
    await render_corrected_delivery_receipt_for_event(session, event_id, object_storage)
    return (CORRECTION_HANDLER_NAME, CORRECTION_RECEIPT_HANDLER_NAME)


async def _mark_receipt_unavailable(
    session: AsyncSession, event_id: UUID, error: Exception
) -> None:
    payload = await session.scalar(
        select(outbox_events.c.payload).where(outbox_events.c.outbox_event_id == event_id)
    )
    receipt_id = payload.get("delivery_receipt_id") if isinstance(payload, dict) else None
    if isinstance(receipt_id, str):
        await session.execute(
            update(delivery_receipt_documents)
            .where(delivery_receipt_documents.c.delivery_receipt_id == UUID(receipt_id))
            .values(status="unavailable", last_error=str(error)[:2000])
        )


async def _group_is_complete(
    session: AsyncSession, event_id: UUID, *, event_type: str, group: str
) -> bool:
    required = set(_required_handlers(event_type, group))
    recorded = set(
        (
            await session.scalars(
                select(outbox_handler_receipts.c.handler_name).where(
                    outbox_handler_receipts.c.outbox_event_id == event_id,
                    outbox_handler_receipts.c.handler_name.in_(required),
                )
            )
        ).all()
    )
    return required <= recorded


async def process_outbox_job(
    session_factory: async_sessionmaker[AsyncSession],
    object_storage: ObjectStorage,
    address: OutboxJobAddress,
) -> OutboxProcessingResult:
    """Run one unfinished handler group for one outbox event.

    Raises the handler error after recording it so the queue can redeliver the
    job.  Re-running is safe: completed handlers are skipped by their receipts.
    """
    event_id = address.outbox_event_id
    group = address.handler_group

    async with session_factory() as session:
        event_type = await session.scalar(
            select(outbox_events.c.event_type).where(outbox_events.c.outbox_event_id == event_id)
        )
    if event_type is None:
        raise ValueError(f"Outbox event {event_id} does not exist.")
    if not _required_handlers(event_type, group):
        raise ValueError(f"Unsupported outbox handler group {group!r} for {event_type!r}.")

    async with session_factory() as session, session.begin():
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
        async with session_factory() as session, session.begin():
            if group == "delivery":
                completed = await _run_delivery_handlers(
                    session,
                    event_id,
                    event_type=event_type,
                    object_storage=object_storage,
                )
            else:
                await create_notifications_for_event(session, event_id)
                completed = NOTIFICATION_HANDLER_NAMES
    except Exception as error:
        logger.warning(
            "outbox_job_failed",
            extra={
                "outbox_event_id": str(event_id),
                "handler_group": group,
                "error": str(error)[:500],
            },
        )
        async with session_factory() as session, session.begin():
            await _mark_receipt_unavailable(session, event_id, error)
            await session.execute(
                update(outbox_processing_state)
                .where(outbox_processing_state.c.outbox_event_id == event_id)
                .values(
                    status="failed",
                    available_at=func.now() + text("interval '1 minute'"),
                    last_error=str(error)[:2000],
                )
            )
        raise

    async with session_factory() as session, session.begin():
        finished = await _group_is_complete(session, event_id, event_type=event_type, group=group)
        await session.execute(
            update(outbox_processing_state)
            .where(outbox_processing_state.c.outbox_event_id == event_id)
            .values(
                status="completed" if finished else "pending",
                processed_at=func.now() if finished else None,
            )
        )
    return OutboxProcessingResult(
        outbox_event_id=str(event_id),
        handler_group=group,
        completed_handlers=tuple(completed),
    )


async def drain_pending_outbox(
    session_factory: async_sessionmaker[AsyncSession],
    object_storage: ObjectStorage,
    *,
    limit: int = 50,
    groups: tuple[str, ...] = ("delivery",),
) -> dict[str, int]:
    """Process pending outbox handler groups in one bounded pass.

    Used by the local development worker and by tests.  The deployed worker is
    event-driven and processes one job per SQS message, but it shares
    :func:`process_outbox_job`, so both paths have identical semantics.  The
    default group matches the historical delivery-only poller; pass both groups
    to drain everything.
    """
    async with session_factory() as session:
        addresses = await pending_outbox_jobs(session, limit=limit, groups=groups)
    completed = 0
    failed = 0
    for address in addresses:
        try:
            await process_outbox_job(session_factory, object_storage, address)
            completed += 1
        except Exception:
            logger.warning(
                "outbox_job_drain_failed",
                extra={"outbox_event_id": str(address.outbox_event_id)},
                exc_info=True,
            )
            failed += 1
    return {"completed": completed, "failed": failed}
