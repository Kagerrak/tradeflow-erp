from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from uuid import UUID, uuid4

import structlog
from fastapi import FastAPI, Request
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from tradeflow_api.config import Settings

correlation_id_context: ContextVar[str | None] = ContextVar(
    "correlation_id",
    default=None,
)
logger = structlog.get_logger("tradeflow_api.request")


def configure_observability(settings: Settings) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
    )

    if not settings.telemetry_enabled:
        return

    provider = TracerProvider(resource=Resource.create({"service.name": "tradeflow-api"}))
    if settings.otlp_endpoint is not None:
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otlp_endpoint))
        )
    trace.set_tracer_provider(provider)


def instrument_app(app: FastAPI, engine: AsyncEngine, settings: Settings) -> None:
    if not settings.telemetry_enabled:
        return
    FastAPIInstrumentor.instrument_app(app)
    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)


def valid_or_new_correlation_id(value: str | None) -> str:
    if value is not None:
        try:
            return str(UUID(value))
        except ValueError:
            pass
    return str(uuid4())


def log_unexpected_error(error: Exception) -> None:
    logger.error(
        "api_request_failed",
        error_type=type(error).__name__,
    )


class CorrelationMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        correlation_id = valid_or_new_correlation_id(request.headers.get("X-Correlation-ID"))
        request.state.correlation_id = correlation_id
        token: Token[str | None] = correlation_id_context.set(correlation_id)
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)
        try:
            trace.get_current_span().set_attribute(
                "tradeflow.correlation_id",
                correlation_id,
            )
            response = await call_next(request)
            response.headers["X-Correlation-ID"] = correlation_id
            logger.info(
                "api_request_completed",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
            )
            return response
        except Exception as error:
            log_unexpected_error(error)
            raise
        finally:
            structlog.contextvars.unbind_contextvars("correlation_id")
            correlation_id_context.reset(token)
