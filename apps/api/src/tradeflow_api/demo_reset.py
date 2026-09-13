"""Demo refresh gating and scheduling.

The demo environment is refreshed on *renewed activity*, not on a wall-clock
timer.  A timer would keep Aurora awake around the clock; instead every
``/v1/`` request reads a small coordination record and starts a refresh only
when the seeded data has expired.  Nothing runs while the demo is idle.
"""

from __future__ import annotations

import logging
import os
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from tradeflow_api.demo_state import (
    DEFAULT_RESET_INTERVAL_MINUTES,
    FAILED,
    READY,
    CachedDemoState,
    DemoState,
    DemoStateStore,
    FileDemoStateStore,
)
from tradeflow_api.job_queue import JobPublisher, demo_reset_job

logger = logging.getLogger(__name__)

DEMO_LOCK_ID = 8_604_501_202_608_241
DEMO_SEED_VERSION = "2026.08.24.2"
DEMO_SEED_REQUIREMENTS = (
    "company",
    "awaiting_approval",
    "ready_to_pick",
    "partially_picked",
    "ready_to_dispatch",
    "delivery_awaiting_confirmation",
    "confirmed_delivery",
    "posted_invoice",
    "payment_awaiting_verification",
    "released_transfer",
    "pending_adjustment",
    "statement_history",
)
GATED_PREFIX = "/v1/"
RESET_LOCK_TTL_SECONDS = 15 * 60


class DemoResetCoordinator:
    """Starts a demo refresh when the seeded data has expired.

    The refresh itself runs in the worker Lambda.  This coordinator only claims
    the single-flight lock and hands the job to the queue, so an API request
    never performs the truncate/seed work inline.
    """

    def __init__(
        self,
        store: DemoStateStore,
        publisher: JobPublisher,
        *,
        seed_version: str = DEMO_SEED_VERSION,
    ) -> None:
        self._store = store
        self._publisher = publisher
        self._seed_version = seed_version

    async def ensure_fresh(self, state: DemoState) -> bool:
        """Start a refresh when due.  Returns True when a refresh was started.

        The single-flight lock is taken by the worker that actually performs the
        refresh, so several API requests racing on an expired demo can all
        enqueue a job and still produce exactly one reset.  The marker key is
        derived from the expiry the refresh is for, so re-enqueueing is an
        idempotent overwrite.
        """
        if state.status == FAILED:
            return False
        if not state.reset_due():
            return False
        marker_id = state.next_reset_at.isoformat() if state.next_reset_at else "due"
        await self._store.mark_refreshing(owner=marker_id, seed_version=self._seed_version)
        try:
            await self._publisher.publish(demo_reset_job(marker_id))
        except Exception:
            logger.exception("demo_reset_job_publish_failed")
            # Keep the demo usable and still due; the next request retries.
            await self._store.mark_ready(
                manifest=state.manifest,
                seed_version=state.seed_version or self._seed_version,
                reset_interval_minutes=0,
            )
            return False
        return True


class DemoMaintenanceMiddleware(BaseHTTPMiddleware):
    """Closes the API while the demo is being refreshed or has failed.

    Reads coordination state only — never the database — so a status check
    cannot wake a paused Aurora cluster.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        state_path: Path | None = None,
        state: CachedDemoState | None = None,
        reset_token: str,
        coordinator: DemoResetCoordinator | None = None,
        gated_prefix: str = GATED_PREFIX,
    ) -> None:
        super().__init__(app)
        if state is None:
            if state_path is None:
                raise ValueError("DemoMaintenanceMiddleware needs a state store or a state path.")
            state = CachedDemoState(
                FileDemoStateStore(
                    state_path, reset_interval_minutes=DEFAULT_RESET_INTERVAL_MINUTES
                )
            )
        self.state = state
        self.reset_token = reset_token
        self.coordinator = coordinator
        self.gated_prefix = gated_prefix

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        supplied_token = request.headers.get("X-TradeFlow-Demo-Reset", "")
        trusted_reset = bool(supplied_token) and secrets.compare_digest(
            supplied_token, self.reset_token
        )
        if request.url.path.startswith(self.gated_prefix) and not trusted_reset:
            await self.state.record_activity()
            snapshot = await self.state.read()
            if snapshot.status == FAILED:
                return _blocked(
                    request,
                    snapshot,
                    code="demo_refresh_failed",
                    message=(
                        "The evaluation environment could not be prepared. "
                        "An operator has been notified."
                    ),
                )
            if snapshot.status != READY:
                return _blocked(
                    request,
                    snapshot,
                    code="demo_refreshing",
                    message=("The demo is refreshing and will be ready shortly."),
                )
            if self.coordinator is not None and await self.coordinator.ensure_fresh(snapshot):
                self.state.invalidate()
                snapshot = await self.state.read(fresh=True)
                return _blocked(
                    request,
                    snapshot,
                    code="demo_refreshing",
                    message=("The demo data expired and is being rebuilt."),
                )
        return await call_next(request)


def _blocked(request: Request, snapshot: DemoState, *, code: str, message: str) -> JSONResponse:
    correlation_id = getattr(request.state, "correlation_id", str(uuid4()))
    retry_after = "60" if code == "demo_refresh_failed" else "30"
    return JSONResponse(
        {
            "error": {
                "code": code,
                "message": message,
                "seed_version": snapshot.seed_version,
                "status": snapshot.status,
                "next_reset_at": snapshot.next_reset_at.isoformat()
                if snapshot.next_reset_at
                else None,
                "correlation_id": correlation_id,
            }
        },
        headers={"Retry-After": retry_after, "X-Correlation-ID": correlation_id},
        status_code=503,
    )


async def missing_demo_seed_requirements(connection: AsyncConnection) -> list[str]:
    result = await connection.execute(
        text(
            """
            SELECT
              (SELECT count(*) FROM companies) AS company,
              (SELECT count(*) FROM sales_orders WHERE status = 'awaiting_approval')
                AS awaiting_approval,
              (SELECT count(*) FROM fulfillment_order_state WHERE status = 'pick_released')
                AS ready_to_pick,
              (SELECT count(*) FROM fulfillment_order_state WHERE status = 'partially_picked')
                AS partially_picked,
              (SELECT count(*) FROM fulfillment_order_state WHERE status = 'picked')
                AS ready_to_dispatch,
              (SELECT count(*) FROM delivery_state WHERE status = 'dispatched')
                AS delivery_awaiting_confirmation,
              (SELECT count(*) FROM delivery_state WHERE status = 'confirmed')
                AS confirmed_delivery,
              (SELECT count(DISTINCT invoice_id) FROM customer_ledger_entries
                 WHERE entry_type = 'invoice' AND invoice_id IS NOT NULL)
                AS posted_invoice,
              (SELECT count(*) FROM payment_receipt_status WHERE state = 'pending_verification')
                AS payment_awaiting_verification,
              (SELECT count(*) FROM inventory_transfers WHERE status = 'released')
                AS released_transfer,
              (SELECT count(*) FROM inventory_adjustments
                 WHERE status = 'pending_authorization') AS pending_adjustment,
              (SELECT count(*) FROM customer_ledger_entries) AS statement_history
            """
        )
    )
    counts = result.mappings().one()
    return [name for name in DEMO_SEED_REQUIREMENTS if int(counts[name]) < 1]


def require_safe_demo_database(database_url: str, environment: str, expected_name: str) -> None:
    database_name = make_url(database_url).database
    if (
        environment != "demo"
        or os.environ.get("TRADEFLOW_DEMO_MODE") not in {"1", "true", "enabled"}
        or database_name != expected_name
        or not expected_name.startswith("tradeflow_demo")
    ):
        raise RuntimeError(
            "Refusing to reset unless the demo environment, demo mode, and an explicit "
            "tradeflow_demo database name all agree."
        )


@contextmanager
def maintenance_state(state_path: Path) -> Iterator[None]:
    """File-backend helper kept for local development and the reset script."""
    from tradeflow_api.demo_state import FileDemoStateStore as _FileStore

    store = _FileStore(state_path, reset_interval_minutes=DEFAULT_RESET_INTERVAL_MINUTES)
    store._write_sync("refreshing", DEMO_SEED_VERSION, None)  # noqa: SLF001
    try:
        yield
    except Exception:
        store._write_sync("failed", None, None, "reset failed")  # noqa: SLF001
        raise
    else:
        store._write_sync(  # noqa: SLF001
            "ready",
            DEMO_SEED_VERSION,
            datetime.now(UTC) + timedelta(minutes=DEFAULT_RESET_INTERVAL_MINUTES),
        )
