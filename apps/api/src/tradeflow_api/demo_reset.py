from __future__ import annotations

import json
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


class DemoMaintenanceMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, *, state_path: Path, reset_token: str) -> None:
        super().__init__(app)
        self.state_path = state_path
        self.reset_token = reset_token

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        supplied_token = request.headers.get("X-TradeFlow-Demo-Reset", "")
        trusted_reset = bool(supplied_token) and secrets.compare_digest(
            supplied_token, self.reset_token
        )
        if (
            request.url.path.startswith("/v1/")
            and not trusted_reset
            and not _state_is_ready(self.state_path)
        ):
            correlation_id = getattr(request.state, "correlation_id", str(uuid4()))
            return JSONResponse(
                {
                    "error": {
                        "code": "demo_refreshing",
                        "message": "The demo is refreshing and will be ready shortly.",
                        "correlation_id": correlation_id,
                    }
                },
                headers={"Retry-After": "30", "X-Correlation-ID": correlation_id},
                status_code=503,
            )
        return await call_next(request)


def _state_is_ready(state_path: Path) -> bool:
    try:
        return bool(json.loads(state_path.read_text(encoding="utf-8")).get("status") == "ready")
    except (OSError, ValueError):
        return False


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
    state_path.parent.mkdir(parents=True, exist_ok=True)
    _write_state(state_path, "refreshing")
    try:
        yield
    except Exception:
        _write_state(state_path, "failed")
        raise
    else:
        _write_state(state_path, "ready")


def _write_state(state_path: Path, status: str) -> None:
    temporary = state_path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "next_reset_at": (datetime.now(UTC) + timedelta(minutes=45)).isoformat()
                if status == "ready"
                else None,
                "seed_version": DEMO_SEED_VERSION,
                "status": status,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(state_path)
