from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256
from typing import Annotated, Literal, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, insert, or_, select, text, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from tradeflow_api.auth import (
    AuthorizedUser,
    require_inventory_adjustment_approver,
    require_inventory_adjustment_requester,
    require_inventory_reader,
)
from tradeflow_api.command_receipts import get_command_replay, store_command_result
from tradeflow_api.database import get_database_session
from tradeflow_api.errors import AppError, error_responses
from tradeflow_api.inventory_projection_service import (
    AvailabilityIdentity,
    acquire_projection_rebuild_lock,
    acquire_sku_warehouse_lock,
    apply_availability_delta,
    apply_valuation_delta,
)
from tradeflow_api.models import (
    approval_authorities,
    companies,
    inventory_adjustment_authorizations,
    inventory_adjustments,
    inventory_valuation,
    lot_identities,
    skus,
    stock_lot_allocations,
    stock_movements,
    unit_conversions,
    warehouse_stock_locations,
    warehouses,
)

router = APIRouter(prefix="/v1/inventory", tags=["inventory adjustments"])
SIX_PLACES = Decimal("0.000001")
ZERO = Decimal("0")


class CommandModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RequestAdjustmentCommand(CommandModel):
    sku_id: UUID
    warehouse_id: UUID
    location_id: UUID
    kind: Literal["surplus", "shortage"]
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=6)
    unit_code: str = Field(pattern=r"^[A-Z][A-Z0-9_-]{0,29}$")
    reason: str = Field(min_length=1, max_length=500)
    source_reference: str = Field(min_length=1, max_length=100)
    lot_code: str | None = Field(default=None, max_length=100)


class PostAdjustmentCommand(CommandModel):
    expected_version: int = Field(ge=1)


class ReverseAdjustmentCommand(CommandModel):
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)


class AdjustmentResponse(BaseModel):
    adjustment_id: UUID
    status: str
    version: int
    sku_id: UUID
    warehouse_id: UUID
    location_id: UUID
    kind: str
    quantity_base: Decimal
    unit_cost: Decimal
    value_delta: Decimal
    base_currency: str
    reason: str
    source_reference: str
    lot_code: str | None
    requested_by: str
    requested_at: str
    posted_by: str | None
    posted_at: str | None
    posted_movement_group_id: UUID | None
    reversed_by: str | None
    reversed_at: str | None
    reversal_reason: str | None
    reversal_movement_group_id: UUID | None


class AdjustmentResponseWrapper(BaseModel):
    adjustment: AdjustmentResponse


class AdjustmentListResponseWrapper(BaseModel):
    items: list[AdjustmentResponse]
    total: int


def command_hash(operation: str, command: BaseModel, context: str = "") -> str:
    payload = f"{operation}:{context}:{command.model_dump_json(exclude_none=False)}"
    return sha256(payload.encode()).hexdigest()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


async def _load_sku(session: AsyncSession, sku_id: UUID) -> RowMapping:
    sku = (
        (await session.execute(select(skus).where(skus.c.sku_id == sku_id)))
        .mappings()
        .one_or_none()
    )
    if sku is None:
        raise AppError(404, "sku_not_found", "SKU not found.")
    return sku


async def _load_location(
    session: AsyncSession,
    location_id: UUID,
    warehouse_id: UUID,
) -> RowMapping:
    location = (
        (
            await session.execute(
                select(warehouse_stock_locations).where(
                    warehouse_stock_locations.c.location_id == location_id,
                    warehouse_stock_locations.c.warehouse_id == warehouse_id,
                    warehouse_stock_locations.c.custody == "available",
                    warehouse_stock_locations.c.is_active.is_(True),
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if location is None:
        raise AppError(
            422,
            "adjustment_location_invalid",
            "Adjustment location must be an active available location in the warehouse.",
        )
    return location


async def _resolve_conversion_factor(
    session: AsyncSession, sku_id: UUID, unit_code: str, base_unit: str
) -> Decimal:
    if unit_code == base_unit:
        return Decimal("1")
    conversion = (
        (
            await session.execute(
                select(unit_conversions).where(
                    unit_conversions.c.sku_id == sku_id,
                    unit_conversions.c.unit_code == unit_code,
                    unit_conversions.c.is_active.is_(True),
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if conversion is None:
        raise AppError(
            422,
            "unit_conversion_not_found",
            "No active Unit Conversion exists for this SKU and unit.",
        )
    return cast(Decimal, conversion["base_quantity"])


async def _ensure_warehouse_scope(actor: AuthorizedUser, warehouse_id: UUID) -> None:
    if warehouse_id not in actor.warehouse_ids:
        raise AppError(
            403,
            "operational_scope_required",
            "You are not authorized for this warehouse.",
        )


async def _record_lot_allocation(
    session: AsyncSession,
    *,
    movement_id: UUID,
    lot_identity_id: UUID | None,
    quantity_base: Decimal,
) -> None:
    if lot_identity_id is None:
        return
    await session.execute(
        insert(stock_lot_allocations).values(
            lot_allocation_id=uuid4(),
            movement_id=movement_id,
            lot_identity_id=lot_identity_id,
            quantity_base=quantity_base,
        )
    )


async def _load_lot_identity(
    session: AsyncSession,
    sku_id: UUID,
    lot_code: str | None,
) -> tuple[AvailabilityIdentity | None, UUID | None]:
    if lot_code is None:
        return (
            AvailabilityIdentity(
                identity_key="", lot_code=None, serial_numbers=(), expiration_date=None
            ),
            None,
        )
    lot_identity = (
        (
            await session.execute(
                select(lot_identities).where(
                    lot_identities.c.sku_id == sku_id,
                    lot_identities.c.lot_code == lot_code,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if lot_identity is None:
        raise AppError(
            409,
            "lot_identity_not_found",
            "The requested Lot Identity does not exist for this SKU.",
        )
    return (
        AvailabilityIdentity(
            identity_key=f"lot:{lot_code}",
            lot_code=lot_code,
            serial_numbers=(),
            expiration_date=lot_identity["expiration_date"],
        ),
        lot_identity["lot_identity_id"],
    )


async def _read_moving_average(
    session: AsyncSession,
    sku_id: UUID,
    warehouse_id: UUID,
) -> tuple[Decimal, str]:
    valuation = (
        (
            await session.execute(
                select(inventory_valuation).where(
                    inventory_valuation.c.sku_id == sku_id,
                    inventory_valuation.c.warehouse_id == warehouse_id,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if valuation is None:
        raise AppError(
            409,
            "inventory_valuation_missing",
            "No inventory valuation exists for this SKU and warehouse.",
        )
    base_currency = (await session.scalar(select(companies.c.base_currency).limit(1))) or "PHP"
    return cast(Decimal, valuation["moving_average_unit_cost"]), cast(str, base_currency)


async def _require_adjustment_authority(
    session: AsyncSession,
    actor: AuthorizedUser,
    warehouse_id: UUID,
    value: Decimal,
    maker_subject: str,
) -> UUID:
    if actor.subject == maker_subject:
        raise AppError(
            status_code=403,
            code="maker_checker_violation",
            message="You cannot authorize an Inventory Adjustment that you requested.",
        )
    warehouse = (
        (await session.execute(select(warehouses).where(warehouses.c.warehouse_id == warehouse_id)))
        .mappings()
        .one_or_none()
    )
    if warehouse is None:
        raise AppError(404, "warehouse_not_found", "Warehouse not found.")
    row = (
        (
            await session.execute(
                select(approval_authorities).where(
                    approval_authorities.c.user_subject == actor.subject,
                    approval_authorities.c.capability_code == "inventory:adjustment-approve",
                    approval_authorities.c.branch_id == warehouse["branch_id"],
                    or_(
                        approval_authorities.c.warehouse_id.is_(None),
                        approval_authorities.c.warehouse_id == warehouse_id,
                    ),
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise AppError(
            403,
            "approval_authority_required",
            "You do not have Inventory Adjustment approval authority for this warehouse.",
        )
    maximum_amount = row["maximum_amount"]
    if maximum_amount is not None and value > maximum_amount:
        raise AppError(
            403,
            "approval_limit_exceeded",
            message="Inventory Adjustment value exceeds your approval limit.",
        )
    return cast(UUID, row["approval_authority_id"])


async def _load_adjustment_for_read(
    session: AsyncSession,
    adjustment_id: UUID,
    actor: AuthorizedUser,
) -> RowMapping:
    adjustment = (
        (
            await session.execute(
                select(inventory_adjustments).where(
                    inventory_adjustments.c.adjustment_id == adjustment_id
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if adjustment is None:
        raise AppError(404, "adjustment_not_found", "Inventory Adjustment not found.")
    await _ensure_warehouse_scope(actor, adjustment["warehouse_id"])
    return adjustment


async def _load_adjustment_for_post(
    session: AsyncSession,
    adjustment_id: UUID,
    actor: AuthorizedUser,
    expected_version: int,
) -> RowMapping:
    adjustment = await _load_adjustment_for_read(session, adjustment_id, actor)
    if adjustment["status"] != "pending_authorization":
        raise AppError(
            409,
            "adjustment_not_pending",
            "Inventory Adjustment is not pending authorization.",
        )
    if adjustment["version"] != expected_version:
        raise AppError(
            409,
            "stale_adjustment_version",
            "Inventory Adjustment version does not match the expected version.",
        )
    return adjustment


async def _load_adjustment_for_reverse(
    session: AsyncSession,
    adjustment_id: UUID,
    actor: AuthorizedUser,
    expected_version: int,
) -> RowMapping:
    adjustment = await _load_adjustment_for_read(session, adjustment_id, actor)
    if adjustment["status"] != "posted":
        raise AppError(
            409,
            "adjustment_not_posted",
            "Only posted Inventory Adjustments can be reversed.",
        )
    if adjustment["version"] != expected_version:
        raise AppError(
            409,
            "stale_adjustment_version",
            "Inventory Adjustment version does not match the expected version.",
        )
    return adjustment


async def _insert_adjustment_movement(
    session: AsyncSession,
    *,
    movement_id: UUID,
    group_id: UUID,
    sku_id: UUID,
    warehouse_id: UUID,
    location_id: UUID,
    movement_leg: str,
    quantity_base: Decimal,
    unit_cost: Decimal,
    value_delta: Decimal,
    base_currency: str,
    source_reference: str,
    entered_unit: str,
    actor_subject: str,
    correlation_id: str,
    idempotency_key: str,
) -> None:
    await session.execute(
        insert(stock_movements).values(
            movement_id=movement_id,
            movement_group_id=group_id,
            movement_leg=movement_leg,
            sku_id=sku_id,
            warehouse_id=warehouse_id,
            location_id=location_id,
            movement_type="inventory_adjustment",
            quantity_base=quantity_base,
            unit_cost=unit_cost,
            value_delta=value_delta,
            base_currency=base_currency,
            source_reference=source_reference,
            entered_unit=entered_unit,
            conversion_snapshot={
                "entered_quantity": str(quantity_base),
                "entered_unit": entered_unit,
                "base_quantity_per_unit": "1.000000",
                "base_quantity": str(quantity_base),
            },
            actor_subject=actor_subject,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
        )
    )


def _adjustment_response(adjustment: RowMapping) -> AdjustmentResponse:
    return AdjustmentResponse(
        adjustment_id=adjustment["adjustment_id"],
        status=adjustment["status"],
        version=adjustment["version"],
        sku_id=adjustment["sku_id"],
        warehouse_id=adjustment["warehouse_id"],
        location_id=adjustment["location_id"],
        kind=adjustment["kind"],
        quantity_base=adjustment["quantity_base"],
        unit_cost=adjustment["unit_cost"],
        value_delta=adjustment["value_delta"],
        base_currency=adjustment["base_currency"],
        reason=adjustment["reason"],
        source_reference=adjustment["source_reference"],
        lot_code=adjustment["lot_code"],
        requested_by=adjustment["requested_by"],
        requested_at=adjustment["requested_at"].isoformat(),
        posted_by=adjustment["posted_by"],
        posted_at=adjustment["posted_at"].isoformat() if adjustment["posted_at"] else None,
        posted_movement_group_id=adjustment["posted_movement_group_id"],
        reversed_by=adjustment["reversed_by"],
        reversed_at=adjustment["reversed_at"].isoformat() if adjustment["reversed_at"] else None,
        reversal_reason=adjustment["reversal_reason"],
        reversal_movement_group_id=adjustment["reversal_movement_group_id"],
    )


@router.post(
    "/adjustments",
    response_model=AdjustmentResponseWrapper,
    status_code=201,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def request_adjustment(
    command: RequestAdjustmentCommand,
    request: Request,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_inventory_adjustment_requester)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> AdjustmentResponseWrapper:
    if idempotency_key is None:
        raise AppError(
            status_code=400,
            code="idempotency_key_required",
            message="Idempotency-Key is required for this command.",
        )

    await _ensure_warehouse_scope(actor, command.warehouse_id)
    request_hash = command_hash("request_inventory_adjustment", command)
    replay = await get_command_replay(
        session,
        actor_subject=actor.subject,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    if replay is not None:
        response.status_code = 200
        response.headers["X-Idempotency-Replayed"] = "true"
        return AdjustmentResponseWrapper(adjustment=AdjustmentResponse.model_validate(replay))

    sku = await _load_sku(session, command.sku_id)
    if sku["tracking_policy"] == "serial":
        raise AppError(
            422,
            "serial_adjustment_not_supported",
            "Serial-tracked SKU adjustments are not supported in this release.",
        )
    if command.lot_code is not None and sku["tracking_policy"] != "lot":
        raise AppError(
            422,
            "lot_code_unexpected",
            "A Lot Code was provided for an SKU that does not use lot tracking.",
        )
    if sku["tracking_policy"] == "lot" and not command.lot_code:
        raise AppError(
            422,
            "lot_code_required",
            "Lot tracking requires a Lot Code for the adjustment.",
        )

    await _load_location(session, command.location_id, command.warehouse_id)
    await _load_lot_identity(session, command.sku_id, command.lot_code)
    factor = await _resolve_conversion_factor(
        session, command.sku_id, command.unit_code, sku["base_stocking_unit"]
    )
    quantity_base = (command.quantity * factor).quantize(SIX_PLACES, ROUND_HALF_UP)
    unit_cost, base_currency = await _read_moving_average(
        session, command.sku_id, command.warehouse_id
    )
    value_delta = (quantity_base * unit_cost).quantize(SIX_PLACES, ROUND_HALF_UP)
    if command.kind == "shortage":
        value_delta = -value_delta

    adjustment_id = uuid4()
    await session.execute(
        insert(inventory_adjustments).values(
            adjustment_id=adjustment_id,
            sku_id=command.sku_id,
            warehouse_id=command.warehouse_id,
            location_id=command.location_id,
            kind=command.kind,
            quantity_base=quantity_base,
            unit_cost=unit_cost,
            value_delta=value_delta,
            base_currency=base_currency,
            reason=command.reason,
            source_reference=command.source_reference,
            lot_code=command.lot_code,
            status="pending_authorization",
            version=1,
            requested_by=actor.subject,
            requested_at=func.now(),
            correlation_id=request.state.correlation_id,
            idempotency_key=idempotency_key,
        )
    )

    result = AdjustmentResponse.model_validate(
        {
            "adjustment_id": adjustment_id,
            "sku_id": command.sku_id,
            "warehouse_id": command.warehouse_id,
            "location_id": command.location_id,
            "kind": command.kind,
            "quantity_base": quantity_base,
            "unit_cost": unit_cost,
            "value_delta": value_delta,
            "base_currency": base_currency,
            "reason": command.reason,
            "source_reference": command.source_reference,
            "lot_code": command.lot_code,
            "status": "pending_authorization",
            "version": 1,
            "requested_by": actor.subject,
            "requested_at": datetime.now(UTC).isoformat(),
            "posted_by": None,
            "posted_at": None,
            "posted_movement_group_id": None,
            "reversed_by": None,
            "reversed_at": None,
            "reversal_reason": None,
            "reversal_movement_group_id": None,
        }
    )
    await store_command_result(
        session,
        actor_subject=actor.subject,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        result=result,
    )
    response.headers["X-Idempotency-Replayed"] = "false"
    await session.commit()
    return AdjustmentResponseWrapper(adjustment=result)


@router.post(
    "/adjustments/{adjustment_id}/post",
    response_model=AdjustmentResponseWrapper,
    status_code=201,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def post_adjustment(
    adjustment_id: UUID,
    command: PostAdjustmentCommand,
    request: Request,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_inventory_adjustment_approver)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> AdjustmentResponseWrapper:
    if idempotency_key is None:
        raise AppError(
            status_code=400,
            code="idempotency_key_required",
            message="Idempotency-Key is required for this command.",
        )

    request_hash = command_hash("post_inventory_adjustment", command, str(adjustment_id))
    replay = await get_command_replay(
        session,
        actor_subject=actor.subject,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    if replay is not None:
        response.status_code = 200
        response.headers["X-Idempotency-Replayed"] = "true"
        return AdjustmentResponseWrapper(adjustment=AdjustmentResponse.model_validate(replay))

    adjustment = await _load_adjustment_for_post(
        session, adjustment_id, actor, command.expected_version
    )
    await _ensure_warehouse_scope(actor, adjustment["warehouse_id"])
    authority_id = await _require_adjustment_authority(
        session,
        actor,
        adjustment["warehouse_id"],
        abs(adjustment["value_delta"]),
        adjustment["requested_by"],
    )

    sku = await _load_sku(session, adjustment["sku_id"])
    identity, lot_identity_id = await _load_lot_identity(
        session, adjustment["sku_id"], adjustment["lot_code"]
    )

    await acquire_projection_rebuild_lock(session, shared=True)
    await acquire_sku_warehouse_lock(session, adjustment["sku_id"], adjustment["warehouse_id"])
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"inventory-adjustment:{adjustment_id}"},
    )
    replay = await get_command_replay(
        session,
        actor_subject=actor.subject,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    if replay is not None:
        response.status_code = 200
        response.headers["X-Idempotency-Replayed"] = "true"
        return AdjustmentResponseWrapper(adjustment=AdjustmentResponse.model_validate(replay))

    adjustment = await _load_adjustment_for_post(
        session, adjustment_id, actor, command.expected_version
    )

    quantity_delta = adjustment["quantity_base"]
    if adjustment["kind"] == "shortage":
        quantity_delta = -quantity_delta

    if adjustment["kind"] == "shortage":
        await apply_availability_delta(
            session,
            sku_id=adjustment["sku_id"],
            warehouse_id=adjustment["warehouse_id"],
            location_id=adjustment["location_id"],
            quantity=quantity_delta,
            identity=identity,
            conflict_code="insufficient_inventory_for_adjustment",
        )

    movement_leg = (
        "adjustment_surplus_in" if adjustment["kind"] == "surplus" else "adjustment_shortage_out"
    )
    movement_group_id = uuid4()
    movement_id = uuid4()
    source_reference = f"ADJUSTMENT:{adjustment_id}"
    await _insert_adjustment_movement(
        session,
        movement_id=movement_id,
        group_id=movement_group_id,
        sku_id=adjustment["sku_id"],
        warehouse_id=adjustment["warehouse_id"],
        location_id=adjustment["location_id"],
        movement_leg=movement_leg,
        quantity_base=adjustment["quantity_base"],
        unit_cost=adjustment["unit_cost"],
        value_delta=adjustment["value_delta"],
        base_currency=adjustment["base_currency"],
        source_reference=source_reference,
        entered_unit=sku["base_stocking_unit"],
        actor_subject=actor.subject,
        correlation_id=request.state.correlation_id,
        idempotency_key=f"{idempotency_key}:movement",
    )
    await _record_lot_allocation(
        session,
        movement_id=movement_id,
        lot_identity_id=lot_identity_id,
        quantity_base=adjustment["quantity_base"],
    )

    await apply_valuation_delta(
        session,
        sku_id=adjustment["sku_id"],
        warehouse_id=adjustment["warehouse_id"],
        quantity_delta=quantity_delta,
        value_delta=adjustment["value_delta"],
        allow_create=False,
        missing_code="inventory_valuation_missing",
    )
    if adjustment["kind"] == "surplus":
        await apply_availability_delta(
            session,
            sku_id=adjustment["sku_id"],
            warehouse_id=adjustment["warehouse_id"],
            location_id=adjustment["location_id"],
            quantity=quantity_delta,
            identity=identity,
        )

    await session.execute(
        update(inventory_adjustments)
        .where(inventory_adjustments.c.adjustment_id == adjustment_id)
        .values(
            status="posted",
            version=inventory_adjustments.c.version + 1,
            posted_by=actor.subject,
            posted_at=func.now(),
            posted_movement_group_id=movement_group_id,
        )
    )
    await session.execute(
        insert(inventory_adjustment_authorizations).values(
            authorization_id=uuid4(),
            adjustment_id=adjustment_id,
            approval_authority_id=authority_id,
            authorized_by=actor.subject,
            authorized_at=func.now(),
            correlation_id=request.state.correlation_id,
            idempotency_key=idempotency_key,
        )
    )

    updated = await _load_adjustment_for_read(session, adjustment_id, actor)
    result = _adjustment_response(updated)
    await store_command_result(
        session,
        actor_subject=actor.subject,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        result=result,
    )
    response.headers["X-Idempotency-Replayed"] = "false"
    await session.commit()
    return AdjustmentResponseWrapper(adjustment=result)


@router.post(
    "/adjustments/{adjustment_id}/reverse",
    response_model=AdjustmentResponseWrapper,
    status_code=201,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def reverse_adjustment(
    adjustment_id: UUID,
    command: ReverseAdjustmentCommand,
    request: Request,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_inventory_adjustment_approver)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> AdjustmentResponseWrapper:
    if idempotency_key is None:
        raise AppError(
            status_code=400,
            code="idempotency_key_required",
            message="Idempotency-Key is required for this command.",
        )

    request_hash = command_hash("reverse_inventory_adjustment", command, str(adjustment_id))
    replay = await get_command_replay(
        session,
        actor_subject=actor.subject,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    if replay is not None:
        response.status_code = 200
        response.headers["X-Idempotency-Replayed"] = "true"
        return AdjustmentResponseWrapper(adjustment=AdjustmentResponse.model_validate(replay))

    adjustment = await _load_adjustment_for_reverse(
        session, adjustment_id, actor, command.expected_version
    )
    await _ensure_warehouse_scope(actor, adjustment["warehouse_id"])
    authority_id = await _require_adjustment_authority(
        session,
        actor,
        adjustment["warehouse_id"],
        abs(adjustment["value_delta"]),
        adjustment["requested_by"],
    )

    sku = await _load_sku(session, adjustment["sku_id"])
    identity, lot_identity_id = await _load_lot_identity(
        session, adjustment["sku_id"], adjustment["lot_code"]
    )

    await acquire_projection_rebuild_lock(session, shared=True)
    await acquire_sku_warehouse_lock(session, adjustment["sku_id"], adjustment["warehouse_id"])
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"inventory-adjustment:{adjustment_id}"},
    )
    replay = await get_command_replay(
        session,
        actor_subject=actor.subject,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    if replay is not None:
        response.status_code = 200
        response.headers["X-Idempotency-Replayed"] = "true"
        return AdjustmentResponseWrapper(adjustment=AdjustmentResponse.model_validate(replay))

    adjustment = await _load_adjustment_for_reverse(
        session, adjustment_id, actor, command.expected_version
    )

    reversal_quantity = -adjustment["quantity_base"]
    reversal_value = -adjustment["value_delta"]
    movement_leg = (
        "adjustment_surplus_reversal_out"
        if adjustment["kind"] == "surplus"
        else "adjustment_shortage_reversal_in"
    )

    if adjustment["kind"] == "surplus":
        await apply_availability_delta(
            session,
            sku_id=adjustment["sku_id"],
            warehouse_id=adjustment["warehouse_id"],
            location_id=adjustment["location_id"],
            quantity=reversal_quantity,
            identity=identity,
            conflict_code="insufficient_inventory_for_adjustment_reversal",
        )

    movement_group_id = uuid4()
    movement_id = uuid4()
    source_reference = f"ADJUSTMENT:{adjustment_id}"
    await _insert_adjustment_movement(
        session,
        movement_id=movement_id,
        group_id=movement_group_id,
        sku_id=adjustment["sku_id"],
        warehouse_id=adjustment["warehouse_id"],
        location_id=adjustment["location_id"],
        movement_leg=movement_leg,
        quantity_base=adjustment["quantity_base"],
        unit_cost=adjustment["unit_cost"],
        value_delta=reversal_value,
        base_currency=adjustment["base_currency"],
        source_reference=source_reference,
        entered_unit=sku["base_stocking_unit"],
        actor_subject=actor.subject,
        correlation_id=request.state.correlation_id,
        idempotency_key=f"{idempotency_key}:movement",
    )
    await _record_lot_allocation(
        session,
        movement_id=movement_id,
        lot_identity_id=lot_identity_id,
        quantity_base=adjustment["quantity_base"],
    )

    await apply_valuation_delta(
        session,
        sku_id=adjustment["sku_id"],
        warehouse_id=adjustment["warehouse_id"],
        quantity_delta=reversal_quantity,
        value_delta=reversal_value,
        allow_create=False,
        missing_code="inventory_valuation_missing",
    )
    if adjustment["kind"] == "shortage":
        await apply_availability_delta(
            session,
            sku_id=adjustment["sku_id"],
            warehouse_id=adjustment["warehouse_id"],
            location_id=adjustment["location_id"],
            quantity=-reversal_quantity,
            identity=identity,
        )

    await session.execute(
        update(inventory_adjustments)
        .where(inventory_adjustments.c.adjustment_id == adjustment_id)
        .values(
            status="reversed",
            version=inventory_adjustments.c.version + 1,
            reversed_by=actor.subject,
            reversed_at=func.now(),
            reversal_reason=command.reason,
            reversal_movement_group_id=movement_group_id,
        )
    )
    await session.execute(
        insert(inventory_adjustment_authorizations).values(
            authorization_id=uuid4(),
            adjustment_id=adjustment_id,
            approval_authority_id=authority_id,
            authorized_by=actor.subject,
            authorized_at=func.now(),
            correlation_id=request.state.correlation_id,
            idempotency_key=idempotency_key,
        )
    )

    updated = await _load_adjustment_for_read(session, adjustment_id, actor)
    result = _adjustment_response(updated)
    await store_command_result(
        session,
        actor_subject=actor.subject,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        result=result,
    )
    response.headers["X-Idempotency-Replayed"] = "false"
    await session.commit()
    return AdjustmentResponseWrapper(adjustment=result)


@router.get(
    "/adjustments",
    response_model=AdjustmentListResponseWrapper,
    responses=error_responses(401, 403, 500),
)
async def list_adjustments(
    actor: Annotated[AuthorizedUser, Depends(require_inventory_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    warehouse_id: UUID | None = None,
    sku_id: UUID | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> AdjustmentListResponseWrapper:
    query = select(inventory_adjustments)
    count_query = select(func.count()).select_from(inventory_adjustments)
    filters = []
    if warehouse_id is not None:
        await _ensure_warehouse_scope(actor, warehouse_id)
        filters.append(inventory_adjustments.c.warehouse_id == warehouse_id)
    else:
        filters.append(inventory_adjustments.c.warehouse_id.in_(actor.warehouse_ids))
    if sku_id is not None:
        filters.append(inventory_adjustments.c.sku_id == sku_id)
    if filters:
        where_clause = filters[0]
        for clause in filters[1:]:
            where_clause = where_clause & clause
        query = query.where(where_clause)
        count_query = count_query.where(where_clause)
    query = query.order_by(inventory_adjustments.c.requested_at.desc()).limit(limit).offset(offset)
    rows = (await session.execute(query)).mappings().all()
    total = cast(int, (await session.scalar(count_query)) or 0)
    return AdjustmentListResponseWrapper(
        items=[_adjustment_response(row) for row in rows],
        total=total,
    )


@router.get(
    "/adjustments/{adjustment_id}",
    response_model=AdjustmentResponseWrapper,
    responses=error_responses(401, 403, 404, 500),
)
async def get_adjustment(
    adjustment_id: UUID,
    actor: Annotated[AuthorizedUser, Depends(require_inventory_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> AdjustmentResponseWrapper:
    adjustment = await _load_adjustment_for_read(session, adjustment_id, actor)
    return AdjustmentResponseWrapper(adjustment=_adjustment_response(adjustment))
