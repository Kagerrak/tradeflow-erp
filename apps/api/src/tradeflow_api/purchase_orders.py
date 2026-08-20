from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, insert, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tradeflow_api.auth import (
    AuthorizedUser,
    require_purchase_order_approver,
    require_purchase_order_reader,
    require_purchase_order_writer,
)
from tradeflow_api.database import get_database_session
from tradeflow_api.errors import AppError, error_responses
from tradeflow_api.models import (
    branches,
    companies,
    purchase_order_lines,
    purchase_orders,
    skus,
    suppliers,
    unit_conversions,
)

router = APIRouter(prefix="/v1/procurement/purchase-orders", tags=["procurement"])


class PurchaseOrderCommandModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PurchaseOrderLineCommand(PurchaseOrderCommandModel):
    sku_id: UUID
    requested_quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=6)
    unit_code: str = Field(min_length=1, max_length=30)
    unit_cost: Decimal = Field(ge=0, max_digits=18, decimal_places=6)


class CreatePurchaseOrderCommand(PurchaseOrderCommandModel):
    supplier_id: UUID
    branch_id: UUID
    code: str = Field(min_length=1, max_length=50)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    exchange_rate: Decimal = Field(default=Decimal("1"), gt=0, max_digits=18, decimal_places=6)
    lines: list[PurchaseOrderLineCommand] = Field(min_length=1)


class PurchaseOrderLineResponse(BaseModel):
    purchase_order_line_id: UUID
    line_number: int
    sku_id: UUID
    requested_quantity: str
    unit_code: str
    base_quantity: str
    received_quantity_base: str
    accepted_quantity_base: str
    backorder_quantity_base: str
    unit_cost: str


class PurchaseOrderResponse(BaseModel):
    purchase_order_id: UUID
    supplier_id: UUID
    branch_id: UUID
    code: str
    currency: str
    exchange_rate: str
    status: str
    version: int
    lines: list[PurchaseOrderLineResponse]


class PurchaseOrderSummary(BaseModel):
    purchase_order_id: UUID
    supplier_id: UUID
    branch_id: UUID
    code: str
    currency: str
    status: str
    version: int


class PurchaseOrderSearchResponse(BaseModel):
    items: list[PurchaseOrderSummary]
    total: int


async def _company_id(session: AsyncSession) -> UUID:
    company_id = await session.scalar(select(companies.c.company_id).limit(1))
    if company_id is None:
        raise AppError(500, "company_missing", "Company not configured.")
    return cast(UUID, company_id)


async def _resolve_branch(
    session: AsyncSession,
    company_id: UUID,
    branch_id: UUID,
) -> None:
    branch = await session.scalar(
        select(branches.c.branch_id).where(
            branches.c.branch_id == branch_id,
            branches.c.company_id == company_id,
            branches.c.is_active.is_(True),
        )
    )
    if branch is None:
        raise AppError(
            404,
            "branch_not_found",
            "The branch does not exist or is not active.",
        )


async def _resolve_supplier(
    session: AsyncSession,
    company_id: UUID,
    supplier_id: UUID,
) -> None:
    supplier = await session.scalar(
        select(suppliers.c.supplier_id).where(
            suppliers.c.supplier_id == supplier_id,
            suppliers.c.company_id == company_id,
            suppliers.c.is_active.is_(True),
        )
    )
    if supplier is None:
        raise AppError(
            404,
            "supplier_not_found",
            "The supplier does not exist or is not active.",
        )


async def _resolve_sku(
    session: AsyncSession,
    sku_id: UUID,
) -> None:
    sku = await session.scalar(
        select(skus.c.sku_id).where(
            skus.c.sku_id == sku_id,
            skus.c.is_active.is_(True),
        )
    )
    if sku is None:
        raise AppError(
            404,
            "sku_not_found",
            "The SKU does not exist or is not active.",
        )


async def _conversion_factor(
    session: AsyncSession,
    sku_id: UUID,
    unit_code: str,
) -> Decimal:
    today = date.today()
    factor = await session.scalar(
        select(unit_conversions.c.base_quantity)
        .where(
            unit_conversions.c.sku_id == sku_id,
            unit_conversions.c.unit_code == unit_code,
            unit_conversions.c.effective_from <= today,
            or_(
                unit_conversions.c.effective_to.is_(None),
                unit_conversions.c.effective_to >= today,
            ),
        )
        .order_by(unit_conversions.c.effective_from.desc())
        .limit(1)
    )
    if factor is None:
        raise AppError(
            422,
            "unit_conversion_missing",
            f"No active unit conversion found for SKU and unit '{unit_code}'.",
        )
    return cast(Decimal, factor)


@router.post(
    "",
    response_model=PurchaseOrderResponse,
    status_code=201,
    responses=error_responses(400, 401, 403, 409, 422, 503),
)
async def create_purchase_order(
    command: CreatePurchaseOrderCommand,
    actor: Annotated[AuthorizedUser, Depends(require_purchase_order_writer)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> PurchaseOrderResponse:
    company_id = await _company_id(session)

    if command.branch_id not in actor.branch_ids:
        raise AppError(
            403,
            "branch_scope_required",
            "The actor is not assigned to the purchase order branch.",
        )

    await _resolve_branch(session, company_id, command.branch_id)
    await _resolve_supplier(session, company_id, command.supplier_id)

    existing = await session.scalar(
        select(purchase_orders.c.purchase_order_id).where(
            purchase_orders.c.company_id == company_id,
            purchase_orders.c.code == command.code,
        )
    )
    if existing is not None:
        raise AppError(
            409,
            "purchase_order_code_duplicate",
            "A purchase order with this code already exists.",
        )

    line_inputs: list[dict[str, Any]] = []
    purchase_order_id = uuid4()
    for index, line in enumerate(command.lines, start=1):
        await _resolve_sku(session, line.sku_id)
        factor = await _conversion_factor(session, line.sku_id, line.unit_code)
        line_inputs.append(
            {
                "purchase_order_line_id": uuid4(),
                "purchase_order_id": purchase_order_id,
                "line_number": index,
                "sku_id": line.sku_id,
                "requested_quantity": line.requested_quantity,
                "unit_code": line.unit_code,
                "base_quantity": line.requested_quantity * factor,
                "unit_cost": line.unit_cost,
            }
        )

    await session.rollback()
    async with session.begin():
        await session.execute(
            insert(purchase_orders).values(
                purchase_order_id=purchase_order_id,
                company_id=company_id,
                supplier_id=command.supplier_id,
                branch_id=command.branch_id,
                code=command.code,
                currency=command.currency,
                exchange_rate=command.exchange_rate,
                status="draft",
                version=1,
                created_by=actor.subject,
            )
        )
        await session.execute(insert(purchase_order_lines), line_inputs)

    return PurchaseOrderResponse(
        purchase_order_id=purchase_order_id,
        supplier_id=command.supplier_id,
        branch_id=command.branch_id,
        code=command.code,
        currency=command.currency,
        exchange_rate=str(command.exchange_rate),
        status="draft",
        version=1,
        lines=[
            PurchaseOrderLineResponse(
                purchase_order_line_id=line["purchase_order_line_id"],
                line_number=line["line_number"],
                sku_id=line["sku_id"],
                requested_quantity=str(line["requested_quantity"]),
                unit_code=line["unit_code"],
                base_quantity=str(line["base_quantity"]),
                received_quantity_base="0.000000",
                accepted_quantity_base="0.000000",
                backorder_quantity_base=str(line["base_quantity"]),
                unit_cost=str(line["unit_cost"]),
            )
            for line in line_inputs
        ],
    )


@router.get(
    "",
    response_model=PurchaseOrderSearchResponse,
    responses=error_responses(401, 403, 503),
)
async def list_purchase_orders(
    actor: Annotated[AuthorizedUser, Depends(require_purchase_order_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    query: Annotated[str, Query(max_length=200)] = "",
    status: Annotated[str, Query(max_length=30)] = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PurchaseOrderSearchResponse:
    company_id = await _company_id(session)

    filters = [
        purchase_orders.c.company_id == company_id,
        purchase_orders.c.branch_id.in_(actor.branch_ids),
    ]
    if query.strip():
        filters.append(
            purchase_orders.c.code.ilike(f"%{query.strip()}%"),
        )
    if status.strip():
        filters.append(purchase_orders.c.status == status.strip())

    total = await session.scalar(
        select(func.count(purchase_orders.c.purchase_order_id)).where(*filters)
    )

    rows = (
        (
            await session.execute(
                select(
                    purchase_orders.c.purchase_order_id,
                    purchase_orders.c.supplier_id,
                    purchase_orders.c.branch_id,
                    purchase_orders.c.code,
                    purchase_orders.c.currency,
                    purchase_orders.c.status,
                    purchase_orders.c.version,
                )
                .where(*filters)
                .order_by(purchase_orders.c.code)
                .limit(limit)
                .offset(offset)
            )
        )
        .mappings()
        .all()
    )

    return PurchaseOrderSearchResponse(
        items=[
            PurchaseOrderSummary(
                purchase_order_id=row["purchase_order_id"],
                supplier_id=row["supplier_id"],
                branch_id=row["branch_id"],
                code=row["code"],
                currency=row["currency"],
                status=row["status"],
                version=row["version"],
            )
            for row in rows
        ],
        total=total or 0,
    )


@router.get(
    "/{purchase_order_id}",
    response_model=PurchaseOrderResponse,
    responses=error_responses(401, 403, 404, 503),
)
async def get_purchase_order(
    purchase_order_id: UUID,
    actor: Annotated[AuthorizedUser, Depends(require_purchase_order_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> PurchaseOrderResponse:
    company_id = await _company_id(session)

    order = (
        (
            await session.execute(
                select(
                    purchase_orders.c.purchase_order_id,
                    purchase_orders.c.supplier_id,
                    purchase_orders.c.branch_id,
                    purchase_orders.c.code,
                    purchase_orders.c.currency,
                    purchase_orders.c.exchange_rate,
                    purchase_orders.c.status,
                    purchase_orders.c.version,
                ).where(
                    purchase_orders.c.purchase_order_id == purchase_order_id,
                    purchase_orders.c.company_id == company_id,
                )
            )
        )
        .mappings()
        .one_or_none()
    )

    if order is None:
        raise AppError(404, "purchase_order_not_found", "The purchase order does not exist.")
    if order["branch_id"] not in actor.branch_ids:
        raise AppError(
            403,
            "branch_scope_required",
            "The actor is not assigned to the purchase order branch.",
        )

    lines = (
        (
            await session.execute(
                select(
                    purchase_order_lines.c.purchase_order_line_id,
                    purchase_order_lines.c.line_number,
                    purchase_order_lines.c.sku_id,
                    purchase_order_lines.c.requested_quantity,
                    purchase_order_lines.c.unit_code,
                    purchase_order_lines.c.base_quantity,
                    purchase_order_lines.c.received_quantity_base,
                    purchase_order_lines.c.accepted_quantity_base,
                    purchase_order_lines.c.backorder_quantity_base,
                    purchase_order_lines.c.unit_cost,
                )
                .where(purchase_order_lines.c.purchase_order_id == purchase_order_id)
                .order_by(purchase_order_lines.c.line_number)
            )
        )
        .mappings()
        .all()
    )

    return PurchaseOrderResponse(
        purchase_order_id=order["purchase_order_id"],
        supplier_id=order["supplier_id"],
        branch_id=order["branch_id"],
        code=order["code"],
        currency=order["currency"],
        exchange_rate=str(order["exchange_rate"]),
        status=order["status"],
        version=order["version"],
        lines=[
            PurchaseOrderLineResponse(
                purchase_order_line_id=line["purchase_order_line_id"],
                line_number=line["line_number"],
                sku_id=line["sku_id"],
                requested_quantity=str(line["requested_quantity"]),
                unit_code=line["unit_code"],
                base_quantity=str(line["base_quantity"]),
                received_quantity_base=str(line["received_quantity_base"]),
                accepted_quantity_base=str(line["accepted_quantity_base"]),
                backorder_quantity_base=str(line["backorder_quantity_base"]),
                unit_cost=str(line["unit_cost"]),
            )
            for line in lines
        ],
    )


@router.post(
    "/{purchase_order_id}/approve",
    response_model=PurchaseOrderResponse,
    responses=error_responses(400, 401, 403, 404, 409, 503),
)
async def approve_purchase_order(
    purchase_order_id: UUID,
    actor: Annotated[AuthorizedUser, Depends(require_purchase_order_approver)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> PurchaseOrderResponse:
    company_id = await _company_id(session)

    order = (
        (
            await session.execute(
                select(
                    purchase_orders.c.purchase_order_id,
                    purchase_orders.c.supplier_id,
                    purchase_orders.c.branch_id,
                    purchase_orders.c.code,
                    purchase_orders.c.currency,
                    purchase_orders.c.exchange_rate,
                    purchase_orders.c.status,
                    purchase_orders.c.version,
                ).where(
                    purchase_orders.c.purchase_order_id == purchase_order_id,
                    purchase_orders.c.company_id == company_id,
                )
            )
        )
        .mappings()
        .one_or_none()
    )

    if order is None:
        raise AppError(404, "purchase_order_not_found", "The purchase order does not exist.")
    if order["branch_id"] not in actor.branch_ids:
        raise AppError(
            403,
            "branch_scope_required",
            "The actor is not assigned to the purchase order branch.",
        )
    if order["status"] != "draft":
        raise AppError(
            409,
            "purchase_order_not_draft",
            "Only draft purchase orders can be approved.",
        )

    await session.rollback()
    async with session.begin():
        await session.execute(
            purchase_orders.update()
            .where(purchase_orders.c.purchase_order_id == purchase_order_id)
            .values(status="approved", version=purchase_orders.c.version + 1)
        )

    return await get_purchase_order(purchase_order_id, actor, session)
