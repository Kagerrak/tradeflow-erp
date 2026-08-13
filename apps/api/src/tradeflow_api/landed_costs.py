from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from tradeflow_api.auth import (
    AuthorizedUser,
    require_landed_cost_allocator,
)
from tradeflow_api.database import get_database_session
from tradeflow_api.errors import AppError, error_responses
from tradeflow_api.inventory_projection_service import apply_valuation_delta
from tradeflow_api.models import (
    companies,
    goods_receipt_lines,
    goods_receipts,
    landed_cost_allocations,
    landed_cost_charges,
    purchase_order_lines,
    purchase_orders,
)

router = APIRouter(
    prefix="/v1/procurement/goods-receipts",
    tags=["procurement"],
)

SIX_PLACES = Decimal("0.000001")
ZERO = Decimal("0")

CHARGE_TYPES = {"freight", "insurance", "customs", "brokerage", "handling"}


class ChargeCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    charge_type: str = Field(min_length=1, max_length=50)
    amount_base: Decimal = Field(gt=0, max_digits=18, decimal_places=6)


class CreateLandedCostCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    charges: list[ChargeCommand] = Field(min_length=1)


class LandedCostAllocationResponse(BaseModel):
    landed_cost_allocation_id: UUID
    goods_receipt_line_id: UUID
    allocated_amount_base: str


class LandedCostChargeResponse(BaseModel):
    landed_cost_charge_id: UUID
    charge_type: str
    amount_base: str
    allocations: list[LandedCostAllocationResponse]


class LandedCostReceiptLineResponse(BaseModel):
    goods_receipt_line_id: UUID
    sku_id: UUID
    received_quantity_base: str
    unit_cost: str
    original_line_value: str
    total_allocated_landed_cost: str


class LandedCostResponse(BaseModel):
    goods_receipt_id: UUID
    base_currency: str
    charges: list[LandedCostChargeResponse]
    lines: list[LandedCostReceiptLineResponse]


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


async def _load_receipt(
    session: AsyncSession,
    company_id: UUID,
    goods_receipt_id: UUID,
) -> dict[str, Any]:
    receipt = (
        (
            await session.execute(
                select(
                    goods_receipts.c.goods_receipt_id,
                    goods_receipts.c.purchase_order_id,
                    goods_receipts.c.warehouse_id,
                    purchase_orders.c.branch_id,
                )
                .select_from(
                    goods_receipts.join(
                        purchase_orders,
                        goods_receipts.c.purchase_order_id == purchase_orders.c.purchase_order_id,
                    )
                )
                .where(
                    goods_receipts.c.goods_receipt_id == goods_receipt_id,
                    purchase_orders.c.company_id == company_id,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if receipt is None:
        raise AppError(
            404,
            "goods_receipt_not_found",
            "The goods receipt does not exist.",
        )
    return dict(receipt)


async def _load_receipt_lines(
    session: AsyncSession,
    goods_receipt_id: UUID,
) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                select(
                    goods_receipt_lines.c.goods_receipt_line_id,
                    goods_receipt_lines.c.purchase_order_line_id,
                    goods_receipt_lines.c.received_quantity_base,
                    purchase_order_lines.c.sku_id,
                    purchase_order_lines.c.unit_cost,
                )
                .select_from(
                    goods_receipt_lines.join(
                        purchase_order_lines,
                        goods_receipt_lines.c.purchase_order_line_id
                        == purchase_order_lines.c.purchase_order_line_id,
                    )
                )
                .where(goods_receipt_lines.c.goods_receipt_id == goods_receipt_id)
                .order_by(goods_receipt_lines.c.goods_receipt_line_id)
            )
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


async def _existing_total_for_charge(
    session: AsyncSession,
    goods_receipt_id: UUID,
    charge_type: str,
) -> Decimal:
    total = await session.scalar(
        select(func.sum(landed_cost_charges.c.amount_base)).where(
            landed_cost_charges.c.goods_receipt_id == goods_receipt_id,
            landed_cost_charges.c.charge_type == charge_type,
        )
    )
    return total or ZERO


def _allocate_by_value(
    amount: Decimal,
    line_values: list[tuple[UUID, Decimal]],
    total_value: Decimal,
) -> list[tuple[UUID, Decimal]]:
    if total_value <= ZERO:
        total_quantity = sum(quantity for _, quantity in line_values)
        if total_quantity <= ZERO:
            return [(line_id, ZERO) for line_id, _ in line_values]
        return [
            (
                line_id,
                (amount * quantity / total_quantity).quantize(SIX_PLACES, rounding=ROUND_HALF_UP),
            )
            for line_id, quantity in line_values
        ]

    allocations: list[tuple[UUID, Decimal]] = []
    remaining = amount
    for index, (line_id, value) in enumerate(line_values):
        if index == len(line_values) - 1:
            allocated = remaining
        else:
            allocated = (amount * value / total_value).quantize(SIX_PLACES, rounding=ROUND_HALF_UP)
        allocations.append((line_id, allocated))
        remaining -= allocated
    return allocations


@router.post(
    "/{goods_receipt_id}/landed-costs",
    response_model=LandedCostResponse,
    status_code=201,
    responses=error_responses(400, 401, 403, 404, 409, 422, 503),
)
async def create_landed_cost(
    goods_receipt_id: UUID,
    command: CreateLandedCostCommand,
    actor: Annotated[AuthorizedUser, Depends(require_landed_cost_allocator)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> LandedCostResponse:
    company = await _company(session)
    company_id = cast(UUID, company["company_id"])
    base_currency = cast(str, company["base_currency"])

    receipt = await _load_receipt(session, company_id, goods_receipt_id)
    if receipt["branch_id"] not in actor.branch_ids:
        raise AppError(
            403,
            "branch_scope_required",
            "The actor is not assigned to the goods receipt branch.",
        )
    if receipt["warehouse_id"] not in actor.warehouse_ids:
        raise AppError(
            403,
            "warehouse_scope_required",
            "The actor is not assigned to the goods receipt warehouse.",
        )

    receipt_lines = await _load_receipt_lines(session, goods_receipt_id)
    if not receipt_lines:
        raise AppError(
            422,
            "goods_receipt_has_no_lines",
            "Landed cost cannot be allocated to an empty goods receipt.",
        )

    line_values: list[tuple[UUID, Decimal]] = []
    line_value_by_id: dict[UUID, Decimal] = {}
    for line in receipt_lines:
        value = (
            cast(Decimal, line["received_quantity_base"]) * cast(Decimal, line["unit_cost"])
        ).quantize(SIX_PLACES, rounding=ROUND_HALF_UP)
        line_values.append((line["goods_receipt_line_id"], value))
        line_value_by_id[line["goods_receipt_line_id"]] = value

    total_value = sum((value for _, value in line_values), ZERO)

    for charge in command.charges:
        if charge.charge_type not in CHARGE_TYPES:
            raise AppError(
                422,
                "landed_cost_charge_type_invalid",
                f"Charge type must be one of {sorted(CHARGE_TYPES)}.",
            )

    correlation_id = str(uuid4())
    idempotency_key = str(uuid4())

    charge_inputs: list[dict[str, Any]] = []
    allocation_inputs: list[dict[str, Any]] = []
    charge_responses: list[tuple[UUID, str, Decimal, list[tuple[UUID, Decimal]]]] = []
    valuation_deltas: dict[UUID, Decimal] = {}

    for charge in command.charges:
        existing = await _existing_total_for_charge(
            session,
            goods_receipt_id,
            charge.charge_type,
        )
        if existing + charge.amount_base > charge.amount_base:
            # Duplicate charge type is allowed; no strict cap.
            pass

        charge_id = uuid4()
        allocations = _allocate_by_value(
            charge.amount_base,
            line_values,
            total_value,
        )
        charge_inputs.append(
            {
                "landed_cost_charge_id": charge_id,
                "goods_receipt_id": goods_receipt_id,
                "charge_type": charge.charge_type,
                "amount_base": charge.amount_base,
                "base_currency": base_currency,
                "correlation_id": correlation_id,
                "idempotency_key": f"{idempotency_key}:{charge_id}",
                "created_by": actor.subject,
            }
        )
        for line_id, allocated in allocations:
            if allocated <= ZERO:
                continue
            allocation_inputs.append(
                {
                    "landed_cost_allocation_id": uuid4(),
                    "landed_cost_charge_id": charge_id,
                    "goods_receipt_line_id": line_id,
                    "allocated_amount_base": allocated,
                }
            )
            valuation_deltas[line_id] = valuation_deltas.get(line_id, ZERO) + allocated
        charge_responses.append((charge_id, charge.charge_type, charge.amount_base, allocations))

    await session.rollback()
    async with session.begin():
        await session.execute(insert(landed_cost_charges), charge_inputs)
        if allocation_inputs:
            await session.execute(insert(landed_cost_allocations), allocation_inputs)

        for line in receipt_lines:
            line_id = cast(UUID, line["goods_receipt_line_id"])
            delta = valuation_deltas.get(line_id, ZERO)
            if delta > ZERO:
                await apply_valuation_delta(
                    session,
                    sku_id=cast(UUID, line["sku_id"]),
                    warehouse_id=cast(UUID, receipt["warehouse_id"]),
                    quantity_delta=ZERO,
                    value_delta=delta,
                    allow_create=False,
                )

    existing_allocations_by_line: dict[UUID, Decimal] = {}
    for line_id, delta in valuation_deltas.items():
        existing_allocations_by_line[line_id] = delta

    return LandedCostResponse(
        goods_receipt_id=goods_receipt_id,
        base_currency=base_currency,
        charges=[
            LandedCostChargeResponse(
                landed_cost_charge_id=charge_id,
                charge_type=charge_type,
                amount_base=str(amount),
                allocations=[
                    LandedCostAllocationResponse(
                        landed_cost_allocation_id=uuid4(),
                        goods_receipt_line_id=line_id,
                        allocated_amount_base=str(allocated),
                    )
                    for line_id, allocated in allocations
                    if allocated > ZERO
                ],
            )
            for charge_id, charge_type, amount, allocations in charge_responses
        ],
        lines=[
            LandedCostReceiptLineResponse(
                goods_receipt_line_id=line["goods_receipt_line_id"],
                sku_id=line["sku_id"],
                received_quantity_base=str(line["received_quantity_base"]),
                unit_cost=str(line["unit_cost"]),
                original_line_value=str(line_value_by_id[line["goods_receipt_line_id"]]),
                total_allocated_landed_cost=str(
                    existing_allocations_by_line.get(line["goods_receipt_line_id"], ZERO)
                ),
            )
            for line in receipt_lines
        ],
    )


@router.get(
    "/{goods_receipt_id}/landed-costs",
    response_model=LandedCostResponse,
    responses=error_responses(401, 403, 404, 503),
)
async def get_landed_costs(
    goods_receipt_id: UUID,
    actor: Annotated[AuthorizedUser, Depends(require_landed_cost_allocator)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> LandedCostResponse:
    company = await _company(session)
    company_id = cast(UUID, company["company_id"])
    base_currency = cast(str, company["base_currency"])

    receipt = await _load_receipt(session, company_id, goods_receipt_id)
    if receipt["branch_id"] not in actor.branch_ids:
        raise AppError(
            403,
            "branch_scope_required",
            "The actor is not assigned to the goods receipt branch.",
        )
    if receipt["warehouse_id"] not in actor.warehouse_ids:
        raise AppError(
            403,
            "warehouse_scope_required",
            "The actor is not assigned to the goods receipt warehouse.",
        )

    receipt_lines = await _load_receipt_lines(session, goods_receipt_id)
    line_value_by_id: dict[UUID, Decimal] = {}
    for line in receipt_lines:
        value = (
            cast(Decimal, line["received_quantity_base"]) * cast(Decimal, line["unit_cost"])
        ).quantize(SIX_PLACES, rounding=ROUND_HALF_UP)
        line_value_by_id[line["goods_receipt_line_id"]] = value

    charges = (
        (
            await session.execute(
                select(
                    landed_cost_charges.c.landed_cost_charge_id,
                    landed_cost_charges.c.charge_type,
                    landed_cost_charges.c.amount_base,
                ).where(landed_cost_charges.c.goods_receipt_id == goods_receipt_id)
            )
        )
        .mappings()
        .all()
    )

    charge_ids = [row["landed_cost_charge_id"] for row in charges]
    allocations: dict[UUID, list[tuple[UUID, Decimal]]] = {}
    if charge_ids:
        allocation_rows = (
            (
                await session.execute(
                    select(
                        landed_cost_allocations.c.landed_cost_charge_id,
                        landed_cost_allocations.c.goods_receipt_line_id,
                        landed_cost_allocations.c.allocated_amount_base,
                    ).where(landed_cost_allocations.c.landed_cost_charge_id.in_(charge_ids))
                )
            )
            .mappings()
            .all()
        )
        for row in allocation_rows:
            allocations.setdefault(row["landed_cost_charge_id"], []).append(
                (row["goods_receipt_line_id"], row["allocated_amount_base"])
            )

    total_by_line: dict[UUID, Decimal] = {}
    for line_id, amount in [
        (line_id, amount)
        for charge_allocs in allocations.values()
        for line_id, amount in charge_allocs
    ]:
        total_by_line[line_id] = total_by_line.get(line_id, ZERO) + amount

    return LandedCostResponse(
        goods_receipt_id=goods_receipt_id,
        base_currency=base_currency,
        charges=[
            LandedCostChargeResponse(
                landed_cost_charge_id=row["landed_cost_charge_id"],
                charge_type=row["charge_type"],
                amount_base=str(row["amount_base"]),
                allocations=[
                    LandedCostAllocationResponse(
                        landed_cost_allocation_id=uuid4(),
                        goods_receipt_line_id=line_id,
                        allocated_amount_base=str(amount),
                    )
                    for line_id, amount in allocations.get(row["landed_cost_charge_id"], [])
                ],
            )
            for row in charges
        ],
        lines=[
            LandedCostReceiptLineResponse(
                goods_receipt_line_id=line["goods_receipt_line_id"],
                sku_id=line["sku_id"],
                received_quantity_base=str(line["received_quantity_base"]),
                unit_cost=str(line["unit_cost"]),
                original_line_value=str(line_value_by_id[line["goods_receipt_line_id"]]),
                total_allocated_landed_cost=str(
                    total_by_line.get(line["goods_receipt_line_id"], ZERO)
                ),
            )
            for line in receipt_lines
        ],
    )
