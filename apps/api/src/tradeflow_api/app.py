from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from tradeflow_api.auth import (
    CurrentUser,
    TokenVerifier,
    require_platform_reader,
)
from tradeflow_api.catalog_inventory import router as catalog_inventory_router
from tradeflow_api.commercial_approval import router as commercial_approval_router
from tradeflow_api.config import Settings, get_settings
from tradeflow_api.credit_notes import router as credit_notes_router
from tradeflow_api.customer_statement import router as customer_statement_router
from tradeflow_api.customers import router as customers_router
from tradeflow_api.database import (
    check_database,
    check_database_migrations,
    create_database_engine,
    create_session_factory,
    migration_heads,
)
from tradeflow_api.delivery_confirmation import router as delivery_confirmation_router
from tradeflow_api.delivery_corrections import router as delivery_corrections_router
from tradeflow_api.delivery_exceptions import router as delivery_exceptions_router
from tradeflow_api.dispatch import router as dispatch_router
from tradeflow_api.errors import AppError, error_response, error_responses
from tradeflow_api.expenses import router as expenses_router
from tradeflow_api.goods_receipts import router as goods_receipts_router
from tradeflow_api.invoice_posting import router as invoice_posting_router
from tradeflow_api.landed_costs import router as landed_costs_router
from tradeflow_api.object_storage import S3ObjectStorage
from tradeflow_api.observability import (
    CorrelationMiddleware,
    configure_observability,
    instrument_app,
)
from tradeflow_api.operational_policies import router as operational_policies_router
from tradeflow_api.organization import router as organization_router
from tradeflow_api.payment_allocation import router as payment_allocation_router
from tradeflow_api.payment_fulfillment import router as payment_fulfillment_router
from tradeflow_api.picking import router as picking_router
from tradeflow_api.platform import router as platform_router
from tradeflow_api.purchase_orders import router as purchase_orders_router
from tradeflow_api.purchase_requests import router as purchase_requests_router
from tradeflow_api.rate_limit import RateLimitMiddleware
from tradeflow_api.sales import router as sales_router
from tradeflow_api.suppliers import router as suppliers_router


class LiveResponse(BaseModel):
    service: str
    status: str


class ReadyResponse(BaseModel):
    service: str
    status: str
    database: str


class SessionUserResponse(BaseModel):
    subject: str
    display_name: str
    capabilities: list[str]


class SessionResponse(BaseModel):
    service: str
    database: str
    user: SessionUserResponse


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_observability(resolved_settings)
    engine = create_database_engine(resolved_settings.database_url)
    expected_database_heads = migration_heads(Path(__file__).resolve().parents[2] / "alembic.ini")
    verifier = TokenVerifier(resolved_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            await check_database(engine)
            await check_database_migrations(engine, expected_database_heads)
            yield
        finally:
            await engine.dispose()

    app = FastAPI(
        title="TradeFlow ERP API",
        version="0.1.0",
        lifespan=lifespan,
        responses=error_responses(500),
    )
    app.state.token_verifier = verifier
    app.state.session_factory = create_session_factory(engine)
    app.state.object_storage = S3ObjectStorage(resolved_settings)
    app.add_middleware(RateLimitMiddleware, settings=resolved_settings)
    app.add_middleware(CorrelationMiddleware)
    app.include_router(catalog_inventory_router)
    app.include_router(commercial_approval_router)
    app.include_router(dispatch_router)
    app.include_router(delivery_confirmation_router)
    app.include_router(delivery_corrections_router)
    app.include_router(delivery_exceptions_router)
    app.include_router(goods_receipts_router)
    app.include_router(landed_costs_router)
    app.include_router(invoice_posting_router)
    app.include_router(credit_notes_router)
    app.include_router(expenses_router)
    app.include_router(payment_allocation_router)
    app.include_router(customer_statement_router)
    app.include_router(payment_fulfillment_router)
    if resolved_settings.picking_enabled:
        app.include_router(picking_router)
    app.include_router(customers_router)
    app.include_router(organization_router)
    app.include_router(operational_policies_router)
    app.include_router(platform_router)
    app.include_router(purchase_orders_router)
    app.include_router(purchase_requests_router)
    app.include_router(sales_router)
    app.include_router(suppliers_router)
    instrument_app(app, engine, resolved_settings)

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, error: AppError) -> object:
        return error_response(request, error)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request,
        _: RequestValidationError,
    ) -> object:
        return error_response(
            request,
            AppError(
                status_code=422,
                code="request_validation_failed",
                message="The request did not satisfy the API contract.",
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(
        request: Request,
        error: StarletteHTTPException,
    ) -> object:
        if error.status_code == 404:
            return error_response(
                request,
                AppError(
                    status_code=404,
                    code="route_not_found",
                    message="The requested API route does not exist.",
                ),
            )
        return error_response(
            request,
            AppError(
                status_code=error.status_code,
                code="http_error",
                message="The API could not complete the request.",
            ),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, error: Exception) -> object:
        return error_response(
            request,
            AppError(
                status_code=500,
                code="internal_error",
                message="The API could not complete the request.",
            ),
        )

    @app.get("/health/live", response_model=LiveResponse, tags=["platform"])
    async def live() -> LiveResponse:
        return LiveResponse(service="tradeflow-api", status="ok")

    @app.get(
        "/health/ready",
        response_model=ReadyResponse,
        responses=error_responses(503),
        tags=["platform"],
    )
    async def ready(request: Request) -> ReadyResponse:
        await check_database(engine, request.state.correlation_id)
        return ReadyResponse(
            service="tradeflow-api",
            status="ready",
            database="ready",
        )

    @app.get(
        "/v1/session",
        response_model=SessionResponse,
        responses=error_responses(401, 403, 503),
        tags=["platform"],
    )
    async def session(
        request: Request,
        user: Annotated[CurrentUser, Depends(require_platform_reader)],
    ) -> SessionResponse:
        await check_database(engine, request.state.correlation_id)
        return SessionResponse(
            service="tradeflow-api",
            database="ready",
            user=SessionUserResponse(
                subject=user.subject,
                display_name=user.display_name,
                capabilities=list(user.capabilities),
            ),
        )

    return app
