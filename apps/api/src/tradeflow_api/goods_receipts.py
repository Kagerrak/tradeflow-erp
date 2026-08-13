from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from tradeflow_api.auth import (
    AuthorizedUser,
    require_goods_receipt_poster,
)
from tradeflow_api.database import get_database_session
from tradeflow_api.errors import AppError, error_responses
from tradeflow_api.inventory_projection_service import (
    AvailabilityIdentity,
    apply_availability_delta,
    apply_valuation_delta,
)
from tradeflow_api.models import (
    companies,
    goods_receipt_lines,
    goods_receipts,
    purchase_order_lines,
    purchase_orders,
    skus,
    stock_movements,
    warehouse_stock_locations,
    warehouses,
)

router = APIRouter(
    prefix="/v1/procurement/purchase-orders",
    tags=["procurement"],
)

SIX_PLACES = Decimal("0.000001")


class ReceiptLineCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purchase_order_line_id: UUID
    received_quantity_base: Decimal = Field(gt=0, max_digits=18, decimal_places=6)
    lot_code: str | None = Field(default=None, max_length=100)
    serial_numbers: list[str] = Field(default_factory=list)


class CreateGoodsReceiptCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    warehouse_id: UUID
    location_id: UUID
    receipt_number: str = Field(min_length=1, max_length=50)
    lines: list[ReceiptLineCommand] = Field(min_length=1)


class GoodsReceiptLineResponse(BaseModel):
    goods_receipt_line_id: UUID
    purchase_order_line_id: UUID
    received_quantity_base: str
    lot_code: str | None
    serial_numbers: list[str]


class GoodsReceiptResponse(BaseModel):
    goods_receipt_id: UUID
    purchase_order_id: UUID
    warehouse_id: UUID
    location_id: UUID
    receipt_number: str
    status: str
    lines: list[GoodsReceiptLineResponse]


async def _company(session: AsyncSession) -> dict[str, Any]:
    row = (
        (
            await session.execute(
                select(
                    companies.c.company_id,
                    companies.c.base_currency,
                ).limit(1)
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise AppError(500, "company_missing", "Company not configured.")
    return dict(row)


async def _resolve_warehouse_and_location(
    session: AsyncSession,
    warehouse_id: UUID,
    location_id: UUID,
) -> None:
    warehouse = await session.scalar(
        select(warehouses.c.warehouse_id).where(
            warehouses.c.warehouse_id == warehouse_id,
            warehouses.c.is_active.is_(True),
        )
    )
    if warehouse is None:
        raise AppError(
            404,
            "warehouse_not_found",
            "The warehouse does not exist or is not active.",
        )

    location = await session.scalar(
        select(warehouse_stock_locations.c.location_id).where(
            warehouse_stock_locations.c.location_id == location_id,
            warehouse_stock_locations.c.warehouse_id == warehouse_id,
            warehouse_stock_locations.c.is_active.is_(True),
        )
    )
    if location is None:
        raise AppError(
            404,
            "location_not_found",
            "The stock location does not exist or is not active in the warehouse.",
        )


async def _load_purchase_order(
    session: AsyncSession,
    company_id: UUID,
    purchase_order_id: UUID,
) -> dict[str, Any]:
    order = (
        (
            await session.execute(
                select(
                    purchase_orders.c.purchase_order_id,
                    purchase_orders.c.branch_id,
                    purchase_orders.c.status,
                    purchase_orders.c.exchange_rate,
                    purchase_orders.c.currency,
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
        raise AppError(
            404,
            "purchase_order_not_found",
            "The purchase order does not exist.",
        )
    return dict(order)


async def _load_purchase_order_line(
    session: AsyncSession,
    purchase_order_id: UUID,
    purchase_order_line_id: UUID,
) -> dict[str, Any]:
    line = (
        (
            await session.execute(
                select(
                    purchase_order_lines.c.purchase_order_line_id,
                    purchase_order_lines.c.sku_id,
                    purchase_order_lines.c.base_quantity,
                    purchase_order_lines.c.received_quantity_base,
                    purchase_order_lines.c.unit_cost,
                ).where(
                    purchase_order_lines.c.purchase_order_line_id == purchase_order_line_id,
                    purchase_order_lines.c.purchase_order_id == purchase_order_id,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if line is None:
        raise AppError(
            404,
            "purchase_order_line_not_found",
            "The purchase order line does not exist.",
        )
    return dict(line)


async def _validate_tracking(
    session: AsyncSession,
    sku_id: UUID,
    received_quantity_base: Decimal,
    lot_code: str | None,
    serial_numbers: list[str],
) -> tuple[str, list[str]]:
    sku = (
        (
            await session.execute(
                select(
                    skus.c.tracking_policy,
                    skus.c.expiration_control,
                ).where(skus.c.sku_id == sku_id)
            )
        )
        .mappings()
        .one_or_none()
    )
    if sku is None:
        raise AppError(404, "sku_not_found", "The SKU does not exist.")

    policy = cast(str, sku["tracking_policy"])

    if policy == "untracked":
        if lot_code is not None or serial_numbers:
            raise AppError(
                422,
                "tracking_identity_unexpected",
                "Untracked SKUs must not include lot or serial identities.",
            )
        return "", []

    if policy == "lot":
        if lot_code is None:
            raise AppError(
                422,
                "lot_code_required",
                "Lot-tracked SKUs require a lot code.",
            )
        if serial_numbers:
            raise AppError(
                422,
                "serial_numbers_unexpected",
                "Lot-tracked SKUs must not include serial numbers.",
            )
        return lot_code, []

    # serial
    if serial_numbers is None or len(serial_numbers) != int(received_quantity_base):
        raise AppError(
            422,
            "serial_count_mismatch",
            "Serial-tracked SKUs require one serial number per unit received.",
        )
    if len(set(serial_numbers)) != len(serial_numbers):
        raise AppError(
            422,
            "serial_numbers_duplicate",
            "Serial numbers must be unique.",
        )
    return "", list(serial_numbers)


@router.post(
    "/{purchase_order_id}/receipts",
    response_model=GoodsReceiptResponse,
    status_code=201,
    responses=error_responses(400, 401, 403, 404, 409, 422, 503),
)
async def create_goods_receipt(
    purchase_order_id: UUID,
    command: CreateGoodsReceiptCommand,
    actor: Annotated[AuthorizedUser, Depends(require_goods_receipt_poster)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> GoodsReceiptResponse:
    company = await _company(session)
    company_id = cast(UUID, company["company_id"])
    base_currency = cast(str, company["base_currency"])

    order = await _load_purchase_order(session, company_id, purchase_order_id)
    if order["branch_id"] not in actor.branch_ids:
        raise AppError(
            403,
            "branch_scope_required",
            "The actor is not assigned to the purchase order branch.",
        )
    if order["status"] not in {"approved", "partially_received", "received"}:
        raise AppError(
            409,
            "purchase_order_not_receivable",
            "Goods receipts can only be posted against approved or "
            "partially received purchase orders.",
        )

    if command.warehouse_id not in actor.warehouse_ids:
        raise AppError(
            403,
            "warehouse_scope_required",
            "The actor is not assigned to the receipt warehouse.",
        )

    await _resolve_warehouse_and_location(
        session,
        command.warehouse_id,
        command.location_id,
    )

    existing = await session.scalar(
        select(goods_receipts.c.goods_receipt_id).where(
            goods_receipts.c.purchase_order_id == purchase_order_id,
            goods_receipts.c.receipt_number == command.receipt_number,
        )
    )
    if existing is not None:
        raise AppError(
            409,
            "goods_receipt_number_duplicate",
            "A goods receipt with this number already exists for the purchase order.",
        )

    # Validate lines and compute open quantities before writing.
    line_inputs: list[dict[str, Any]] = []
    line_updates: list[tuple[UUID, Decimal]] = []
    stock_inputs: list[dict[str, Any]] = []
    movement_group_id = uuid4()
    correlation_id = str(uuid4())
    idempotency_key = str(uuid4())

    for receipt_line in command.lines:
        po_line = await _load_purchase_order_line(
            session,
            purchase_order_id,
            receipt_line.purchase_order_line_id,
        )
        open_quantity = cast(Decimal, po_line["base_quantity"]) - cast(
            Decimal, po_line["received_quantity_base"]
        )
        if receipt_line.received_quantity_base > open_quantity:
            raise AppError(
                409,
                "goods_receipt_over_receipt",
                "Received quantity exceeds the open quantity on the purchase order line.",
            )

        identity_key, serials = await _validate_tracking(
            session,
            cast(UUID, po_line["sku_id"]),
            receipt_line.received_quantity_base,
            receipt_line.lot_code,
            receipt_line.serial_numbers,
        )

        goods_receipt_line_id = uuid4()
        line_inputs.append(
            {
                "goods_receipt_line_id": goods_receipt_line_id,
                "purchase_order_line_id": receipt_line.purchase_order_line_id,
                "received_quantity_base": receipt_line.received_quantity_base,
                "lot_code": receipt_line.lot_code,
                "serial_numbers": serials,
            }
        )
        line_updates.append(
            (
                receipt_line.purchase_order_line_id,
                receipt_line.received_quantity_base,
            )
        )

        unit_cost = cast(Decimal, po_line["unit_cost"])
        value_delta = (receipt_line.received_quantity_base * unit_cost).quantize(
            SIX_PLACES, rounding="ROUND_HALF_UP"
        )

        stock_inputs.append(
            {
                "movement_id": uuid4(),
                "sku_id": po_line["sku_id"],
                "warehouse_id": command.warehouse_id,
                "location_id": command.location_id,
                "movement_type": "goods_receipt",
                "movement_leg": "goods_receipt_in",
                "quantity_base": receipt_line.received_quantity_base,
                "unit_cost": unit_cost,
                "value_delta": value_delta,
                "base_currency": base_currency,
                "source_reference": f"GR:{purchase_order_id}:{receipt_line.purchase_order_line_id}",
                "entered_unit": "base",
                "conversion_snapshot": {"factor": "1"},
                "actor_subject": actor.subject,
                "correlation_id": correlation_id,
                "idempotency_key": f"{idempotency_key}:{goods_receipt_line_id}",
                "movement_group_id": movement_group_id,
            }
        )

    goods_receipt_id = uuid4()

    await session.rollback()
    async with session.begin():
        await session.execute(
            insert(goods_receipts).values(
                goods_receipt_id=goods_receipt_id,
                purchase_order_id=purchase_order_id,
                warehouse_id=command.warehouse_id,
                location_id=command.location_id,
                receipt_number=command.receipt_number,
                status="posted",
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
                created_by=actor.subject,
            )
        )

        for line_input in line_inputs:
            line_input["goods_receipt_id"] = goods_receipt_id
        await session.execute(insert(goods_receipt_lines), line_inputs)

        await session.execute(insert(stock_movements), stock_inputs)

        for purchase_order_line_id, received in line_updates:
            await session.execute(
                purchase_order_lines.update()
                .where(purchase_order_lines.c.purchase_order_line_id == purchase_order_line_id)
                .values(
                    received_quantity_base=purchase_order_lines.c.received_quantity_base + received,
                )
            )

        # Refresh line received quantities to determine new PO status.
        total_base = await session.scalar(
            select(func.sum(purchase_order_lines.c.base_quantity)).where(
                purchase_order_lines.c.purchase_order_id == purchase_order_id
            )
        )
        total_received = await session.scalar(
            select(func.sum(purchase_order_lines.c.received_quantity_base)).where(
                purchase_order_lines.c.purchase_order_id == purchase_order_id
            )
        )

        new_status = "partially_received"
        if total_received is not None and total_base is not None:
            if total_received >= total_base:
                new_status = "received"

        if order["status"] != new_status:
            await session.execute(
                purchase_orders.update()
                .where(purchase_orders.c.purchase_order_id == purchase_order_id)
                .values(status=new_status, version=purchase_orders.c.version + 1)
            )

        # Apply availability and valuation projections.
        for receipt_line in command.lines:
            po_line = await _load_purchase_order_line(
                session,
                purchase_order_id,
                receipt_line.purchase_order_line_id,
            )
            identity_key, serials = await _validate_tracking(
                session,
                cast(UUID, po_line["sku_id"]),
                receipt_line.received_quantity_base,
                receipt_line.lot_code,
                receipt_line.serial_numbers,
            )
            identity = AvailabilityIdentity(
                identity_key=identity_key,
                lot_code=receipt_line.lot_code,
                serial_numbers=serials,
            )
            await apply_availability_delta(
                session,
                sku_id=cast(UUID, po_line["sku_id"]),
                warehouse_id=command.warehouse_id,
                location_id=command.location_id,
                quantity=receipt_line.received_quantity_base,
                identity=identity,
            )
            unit_cost = cast(Decimal, po_line["unit_cost"])
            value_delta = (receipt_line.received_quantity_base * unit_cost).quantize(
                SIX_PLACES, rounding="ROUND_HALF_UP"
            )
            await apply_valuation_delta(
                session,
                sku_id=cast(UUID, po_line["sku_id"]),
                warehouse_id=command.warehouse_id,
                quantity_delta=receipt_line.received_quantity_base,
                value_delta=value_delta,
                allow_create=True,
            )

    return GoodsReceiptResponse(
        goods_receipt_id=goods_receipt_id,
        purchase_order_id=purchase_order_id,
        warehouse_id=command.warehouse_id,
        location_id=command.location_id,
        receipt_number=command.receipt_number,
        status="posted",
        lines=[
            GoodsReceiptLineResponse(
                goods_receipt_line_id=line["goods_receipt_line_id"],
                purchase_order_line_id=line["purchase_order_line_id"],
                received_quantity_base=str(line["received_quantity_base"]),
                lot_code=line["lot_code"],
                serial_numbers=line["serial_numbers"],
            )
            for line in line_inputs
        ],
    )
