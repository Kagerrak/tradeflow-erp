"""AWS Lambda entry point for the TradeFlow business API.

The Lambda is VPC-attached so it can reach private Aurora, and it has no NAT
gateway.  Everything it needs at runtime therefore travels over free gateway
endpoints:

* Aurora over the VPC ENI,
* S3 over the S3 gateway endpoint (documents and job markers),
* DynamoDB over the DynamoDB gateway endpoint (demo coordination).

No secret is read at runtime: configuration and credentials are resolved by the
deployment workflow and injected as encrypted Lambda environment variables.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from mangum import Mangum
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tradeflow_api.app import create_app
from tradeflow_api.config import Settings, get_settings
from tradeflow_api.job_queue import JobPublisher, outbox_event_job
from tradeflow_api.outbox_jobs import pending_outbox_jobs

logger = logging.getLogger(__name__)

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

_settings: Settings = get_settings()
_app = create_app(_settings)
_mangum = Mangum(_app, lifespan="auto")
_publisher: JobPublisher | None = getattr(_app.state, "job_publisher", None)
_session_factory: async_sessionmaker[AsyncSession] | None = getattr(
    _app.state, "session_factory", None
)
_last_recovery_at = 0.0


def _request_facts(event: dict[str, Any]) -> tuple[str, str]:
    context = event.get("requestContext") or {}
    http = context.get("http") or {}
    method = str(http.get("method") or event.get("httpMethod") or "GET")
    path = str(event.get("rawPath") or event.get("path") or "/")
    return method.upper(), path


async def _publish_pending_jobs() -> int:
    if _publisher is None or _session_factory is None:
        return 0
    async with _session_factory() as session:
        addresses = await pending_outbox_jobs(session, limit=_settings.demo_dispatch_batch_size)
    for address in addresses:
        await _publisher.publish(
            outbox_event_job(str(address.outbox_event_id), address.handler_group)
        )
    return len(addresses)


async def _dispatch_after_response(*, method: str, path: str, status_code: int) -> None:
    """Publish outbox jobs the just-committed transaction created.

    Runs in the same invocation as the write, so the common path needs no
    polling at all.  A time-boxed recovery pass re-publishes anything a failed
    publish left behind; because it is gated on API activity it never wakes a
    paused Aurora cluster on its own.
    """
    global _last_recovery_at
    if _publisher is None or _session_factory is None:
        return
    mutated = method in MUTATING_METHODS and path.startswith("/v1/") and 200 <= status_code < 300
    if not mutated and _settings.demo_dispatch_recovery_seconds <= 0:
        return
    recovery_due = time.monotonic() - _last_recovery_at >= _settings.demo_dispatch_recovery_seconds
    if not mutated and not recovery_due:
        return
    _last_recovery_at = time.monotonic()
    try:
        published = await _publish_pending_jobs()
        if published:
            logger.info("outbox_jobs_published", extra={"count": published})
    except Exception:
        logger.warning("outbox_dispatch_failed", exc_info=True)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """API Gateway HTTP API (payload v2) / Lambda Function URL entry point."""
    response = _mangum(event, context)
    method, path = _request_facts(event)
    status_code = int(response.get("statusCode", 500))
    try:
        asyncio.run(_dispatch_after_response(method=method, path=path, status_code=status_code))
    except RuntimeError:  # pragma: no cover - defensive: never fail a response.
        logger.warning("outbox_dispatch_skipped", exc_info=True)
    return response
