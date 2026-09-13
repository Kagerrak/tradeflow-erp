"""AWS Lambda entry point for the TradeFlow business API.

The Lambda is the same ASGI application the tests and local development use;
outbox dispatching and demo gating live in the application itself so every
caller behaves identically.  This module only adapts the HTTP event shape and
makes sure no database connection outlives the invocation.
"""

from __future__ import annotations

from typing import Any

from mangum import Mangum

from tradeflow_api.app import create_app
from tradeflow_api.config import get_settings

_settings = get_settings()
_app = create_app(_settings)
_handler = Mangum(_app, lifespan="auto")


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """API Gateway HTTP API (payload v2) / Lambda Function URL entry point."""
    return _handler(event, context)
