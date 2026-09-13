"""Publish committed outbox events as jobs.

The API owns its outbox: the transaction that records a delivery confirmation
also records the outbox row, and this middleware turns any unfinished row into a
job right after a successful mutating request.  Keeping that here rather than in
the Lambda entry point means every caller behaves identically — the deployed
API, the in-process API the demo seeder drives, and local development.

A time-boxed recovery pass re-publishes anything a failed publish left behind.
It is gated on API activity, so it never wakes a paused Aurora cluster on its
own, and it is bounded by a batch size and a lookback window.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from tradeflow_api.job_queue import JobPublisher, outbox_event_job
from tradeflow_api.outbox_jobs import pending_outbox_jobs

logger = logging.getLogger(__name__)

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
GATED_PREFIX = "/v1/"


class OutboxDispatchMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: ASGIApp,
        *,
        publisher: JobPublisher,
        session_factory: async_sessionmaker[AsyncSession],
        batch_size: int = 25,
        recovery_seconds: int = 300,
    ) -> None:
        super().__init__(app)
        self._publisher = publisher
        self._session_factory = session_factory
        self._batch_size = batch_size
        self._recovery_seconds = recovery_seconds
        self._last_recovery_at = 0.0

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        if not request.url.path.startswith(GATED_PREFIX):
            return response
        mutated = request.method.upper() in MUTATING_METHODS and 200 <= response.status_code < 300
        recovery_due = (
            self._recovery_seconds > 0
            and time.monotonic() - self._last_recovery_at >= self._recovery_seconds
        )
        if not mutated and not recovery_due:
            return response
        self._last_recovery_at = time.monotonic()
        try:
            published = await self._publish_pending()
            if published:
                logger.info("outbox_jobs_published", extra={"count": published})
        except Exception:
            # Dispatch failure must never fail the request: the committed outbox
            # row is the source of truth and the next pass republishes it.
            logger.warning("outbox_dispatch_failed", exc_info=True)
        return response

    async def _publish_pending(self) -> int:
        async with self._session_factory() as session:
            addresses = await pending_outbox_jobs(session, limit=self._batch_size)
        for address in addresses:
            await self._publisher.publish(
                outbox_event_job(str(address.outbox_event_id), address.handler_group)
            )
        return len(addresses)


def dispatcher_from_state(
    state: Any,
) -> tuple[JobPublisher, async_sessionmaker[AsyncSession]] | None:
    publisher = getattr(state, "job_publisher", None)
    session_factory = getattr(state, "session_factory", None)
    if publisher is None or session_factory is None:
        return None
    return publisher, session_factory
