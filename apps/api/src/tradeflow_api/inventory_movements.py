from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256
from typing import Annotated, Any, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, insert, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from tradeflow_api.auth import (
    AuthorizedUser,
    require_inventory_reader,
    require_inventory_transfer_receiver,
    require_inventory_transfer_requester,
)
from tradeflow_api.command_receipts import get_command_replay, store_command_result
from tradeflow_api.database import get_database_session
from tradeflow_api.delivery_partitioning import ensure_custody_location
from tradeflow_api.errors import AppError, error_responses
from tradeflow_api.inventory_projection_service import (
    AvailabilityIdentity,
    acquire_projection_rebuild_lock,
    acquire_sku_warehouse_lock,
    apply_availability_delta,
    apply_valuation_delta,
)
from tradeflow_api.models import (
    companies,
    inventory_availability,
    inventory_transfers,
    inventory_valuation,
    lot_identities,
    skus,
    stock_lot_allocations,
    stock_movements,
    unit_conversions,
    warehouse_stock_locations,
)
from tradeflow_api.money import currency_quantum

router = APIRouter(prefix="/v1/inventory", tags=["inventory movements"])
SIX_PLACES = Decimal("0.000001")
ZERO = Decimal("0")


class CommandModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RequestTransferCommand(CommandModel):
    sku_id: UUID
    from_warehouse_id: UUID
    to_warehouse_id: UUID
    from_location_id: UUID
    to_location_id: UUID
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=6)
    unit_code: str = Field(pattern=r"^[A-Z][A-Z0-9_-]{0,29}$")
    reason: str = Field(min_length=1, max_length=500)
    source_reference: str = Field(min_length=1, max_length=100)
    lot_code: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def distinct_warehouses(self) -> RequestTransferCommand:
        if self.from_warehouse_id == self.to_warehouse_id:
            raise ValueError("Transfer source and destination warehouses must differ.")
        return self


class ReceiveTransferCommand(CommandModel):
    expected_version: int = Field(ge=1)


class TransferReleasedItem(BaseModel):
    transfer_id: UUID
    status: str
    version: int
    sku_id: UUID
    from_warehouse_id: UUID
    to_warehouse_id: UUID
    from_location_id: UUID
    to_location_id: UUID
    quantity_base: Decimal
    unit_cost: Decimal
    base_currency: str
    reason: str
    source_reference: str
    lot_code: str | None
    requested_by: str
    requested_at: str
    release_movement_group_id: UUID


class TransferReceivedItem(TransferReleasedItem):
    received_by: str
    received_at: str
    receive_movement_group_id: UUID


class TransferResponse(BaseModel):
    transfer: TransferReceivedItem | TransferReleasedItem


class TransferListResponse(BaseModel):
    items: list[TransferReceivedItem | TransferReleasedItem]
    total: int


def command_hash(operation: str, command: BaseModel, context: str = "") -> str:
    payload = f"{operation}:{context}:{command.model_dump_json(exclude_none=False)}"
    return sha256(payload.encode()).hexdigest()


def _ensure_transfer_scope(
    actor: AuthorizedUser, from_warehouse_id: UUID, to_warehouse_id: UUID
) -> None:
    if from_warehouse_id not in actor.warehouse_ids:
        raise AppError(
            403,
            "transfer_source_forbidden",
            "You are not authorized to transfer from the source warehouse.",
        )
    if to_warehouse_id not in actor.warehouse_ids:
        raise AppError(
            403,
            "transfer_destination_forbidden",
            "You are not authorized to transfer into the destination warehouse.",
        )


async def _load_transfer_context(
    session: AsyncSession,
    command: RequestTransferCommand,
    actor: AuthorizedUser,
) -> dict[str, Any]:
    _ensure_transfer_scope(actor, command.from_warehouse_id, command.to_warehouse_id)

    sku = await _load_sku(session, command.sku_id)
    from_location = await _load_location(
        session, command.from_location_id, command.from_warehouse_id, expected_custody="available"
    )
    to_location = await _load_location(
        session, command.to_location_id, command.to_warehouse_id, expected_custody="available"
    )

    if sku["tracking_policy"] == "serial":
        raise AppError(
            422,
            "serial_transfer_not_supported",
            "Serial-tracked SKU transfers are not supported in this release.",
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
            "Lot tracking requires a Lot Code for the transfer.",
        )

    lot_identity = None
    if command.lot_code is not None:
        lot_identity = (
            (
                await session.execute(
                    select(lot_identities).where(
                        lot_identities.c.sku_id == command.sku_id,
                        lot_identities.c.lot_code == command.lot_code,
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

    factor = await _resolve_conversion_factor(
        session,
        sku_id=command.sku_id,
        unit_code=command.unit_code,
        base_stocking_unit=sku["base_stocking_unit"],
    )
    quantity_base = (command.quantity * factor).quantize(SIX_PLACES)

    return {
        "sku": sku,
        "from_location": from_location,
        "to_location": to_location,
        "quantity_base": quantity_base,
        "factor": factor,
        "identity": AvailabilityIdentity(
            identity_key=f"lot:{command.lot_code}",
            lot_code=command.lot_code,
            serial_numbers=(),
            expiration_date=lot_identity["expiration_date"],
        )
        if lot_identity is not None
        else AvailabilityIdentity(identity_key="", serial_numbers=()),
        "lot_identity_id": lot_identity["lot_identity_id"] if lot_identity is not None else None,
    }


async def _load_sku(session: AsyncSession, sku_id: UUID) -> dict[str, Any]:
    sku = (
        (await session.execute(select(skus).where(skus.c.sku_id == sku_id)))
        .mappings()
        .one_or_none()
    )
    if sku is None:
        raise AppError(404, "sku_not_found", "The SKU does not exist.")
    return dict(sku)


async def _load_location(
    session: AsyncSession,
    location_id: UUID,
    warehouse_id: UUID,
    expected_custody: str,
) -> dict[str, Any]:
    location = (
        (
            await session.execute(
                select(warehouse_stock_locations).where(
                    warehouse_stock_locations.c.location_id == location_id,
                    warehouse_stock_locations.c.warehouse_id == warehouse_id,
                    warehouse_stock_locations.c.is_active.is_(True),
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if location is None:
        raise AppError(
            404,
            "warehouse_location_not_found",
            "The warehouse location does not exist or is not active.",
        )
    if location["custody"] != expected_custody:
        raise AppError(
            409,
            "warehouse_location_custody_mismatch",
            f"The location must have '{expected_custody}' custody for this operation.",
        )
    return dict(location)


async def _resolve_conversion_factor(
    session: AsyncSession,
    *,
    sku_id: UUID,
    unit_code: str,
    base_stocking_unit: str,
) -> Decimal:
    if unit_code == base_stocking_unit:
        return Decimal("1")
    conversion = (
        (
            await session.execute(
                select(unit_conversions)
                .where(
                    unit_conversions.c.sku_id == sku_id,
                    unit_conversions.c.unit_code == unit_code,
                    unit_conversions.c.effective_from <= date.today(),
                    or_(
                        unit_conversions.c.effective_to.is_(None),
                        unit_conversions.c.effective_to >= date.today(),
                    ),
                )
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if conversion is None:
        raise AppError(
            422,
            "unit_conversion_not_effective",
            "No effective Unit Conversion exists for the entered unit.",
        )
    return cast(Decimal, conversion["base_quantity"])


async def _ensure_source_available(
    session: AsyncSession,
    *,
    sku_id: UUID,
    warehouse_id: UUID,
    location_id: UUID,
    identity: AvailabilityIdentity,
    quantity_base: Decimal,
) -> None:
    identity_key = identity.identity_key
    row = (
        (
            await session.execute(
                select(inventory_availability)
                .where(
                    inventory_availability.c.sku_id == sku_id,
                    inventory_availability.c.warehouse_id == warehouse_id,
                    inventory_availability.c.location_id == location_id,
                    inventory_availability.c.identity_key == identity_key,
                )
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise AppError(
            409,
            "insufficient_inventory_for_transfer",
            "The source location has no inventory for the requested identity.",
        )
    available = row["on_hand"] - row["reserved"]
    if available < quantity_base:
        raise AppError(
            409,
            "insufficient_inventory_for_transfer",
            "The source location does not have enough unreserved inventory for the transfer.",
        )


async def _read_source_cost(
    session: AsyncSession,
    sku_id: UUID,
    warehouse_id: UUID,
) -> tuple[Decimal, str]:
    base_currency = await session.scalar(select(companies.c.base_currency))
    if base_currency is None:
        raise AppError(
            409,
            "base_currency_not_configured",
            "The Company Base Currency must be configured before posting stock movements.",
        )
    valuation = (
        (
            await session.execute(
                select(inventory_valuation)
                .where(
                    inventory_valuation.c.sku_id == sku_id,
                    inventory_valuation.c.warehouse_id == warehouse_id,
                )
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if valuation is None:
        raise AppError(
            409,
            "inventory_valuation_missing",
            "Source warehouse has no inventory valuation for this SKU.",
        )
    return valuation["moving_average_unit_cost"], base_currency


async def _load_transfer_identity(
    session: AsyncSession, *, sku_id: UUID, lot_code: str | None
) -> tuple[AvailabilityIdentity, UUID | None]:
    if lot_code is None:
        return AvailabilityIdentity(identity_key="", serial_numbers=()), None
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
        raise AppError(409, "lot_identity_not_found", "The transfer Lot Identity no longer exists.")
    return (
        AvailabilityIdentity(
            identity_key=f"lot:{lot_code}",
            lot_code=lot_code,
            serial_numbers=(),
            expiration_date=lot_identity["expiration_date"],
        ),
        lot_identity["lot_identity_id"],
    )


async def _record_lot_allocations(
    session: AsyncSession,
    *,
    movement_ids: tuple[UUID, UUID],
    lot_identity_id: UUID | None,
    quantity_base: Decimal,
) -> None:
    if lot_identity_id is None:
        return
    await session.execute(
        insert(stock_lot_allocations),
        [
            {
                "lot_allocation_id": uuid4(),
                "movement_id": movement_id,
                "lot_identity_id": lot_identity_id,
                "quantity_base": quantity_base,
            }
            for movement_id in movement_ids
        ],
    )


async def _insert_movement(
    session: AsyncSession,
    *,
    movement_id: UUID,
    group_id: UUID,
    sku_id: UUID,
    warehouse_id: UUID,
    location_id: UUID,
    movement_type: str,
    movement_leg: str,
    quantity_base: Decimal,
    unit_cost: Decimal,
    value_delta: Decimal,
    base_currency: str,
    source_reference: str,
    entered_unit: str,
    conversion_snapshot: dict[str, str],
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
            movement_type=movement_type,
            quantity_base=quantity_base,
            unit_cost=unit_cost,
            value_delta=value_delta,
            base_currency=base_currency,
            source_reference=source_reference,
            entered_unit=entered_unit,
            conversion_snapshot=conversion_snapshot,
            actor_subject=actor_subject,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
        )
    )


@router.post(
    "/transfers",
    response_model=TransferResponse,
    status_code=201,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def request_transfer(
    command: RequestTransferCommand,
    request: Request,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_inventory_transfer_requester)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> TransferResponse:
    if idempotency_key is None:
        raise AppError(
            status_code=400,
            code="idempotency_key_required",
            message="Idempotency-Key is required for this command.",
        )

    _ensure_transfer_scope(actor, command.from_warehouse_id, command.to_warehouse_id)
    request_hash = command_hash("request_inventory_transfer", command)
    replay = await get_command_replay(
        session,
        actor_subject=actor.subject,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    if replay is not None:
        response.status_code = 200
        response.headers["X-Idempotency-Replayed"] = "true"
        return TransferResponse.model_validate(replay)

    context = await _load_transfer_context(session, command, actor)
    sku = context["sku"]
    quantity_base = context["quantity_base"]
    factor = context["factor"]
    identity = context["identity"]
    lot_identity_id = context["lot_identity_id"]

    first_warehouse = min(command.from_warehouse_id, command.to_warehouse_id)
    second_warehouse = max(command.from_warehouse_id, command.to_warehouse_id)

    await acquire_projection_rebuild_lock(session, shared=True)
    await acquire_sku_warehouse_lock(session, sku["sku_id"], first_warehouse)
    if second_warehouse != first_warehouse:
        await acquire_sku_warehouse_lock(session, sku["sku_id"], second_warehouse)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"inventory-transfer-request:{command.from_warehouse_id}:{sku['sku_id']}"},
    )

    await _ensure_source_available(
        session,
        sku_id=sku["sku_id"],
        warehouse_id=command.from_warehouse_id,
        location_id=command.from_location_id,
        identity=identity,
        quantity_base=quantity_base,
    )

    unit_cost, base_currency = await _read_source_cost(
        session, sku["sku_id"], command.from_warehouse_id
    )
    value = (quantity_base * unit_cost).quantize(currency_quantum(base_currency), ROUND_HALF_UP)

    conversion_snapshot = {
        "entered_quantity": str(command.quantity),
        "entered_unit": command.unit_code,
        "base_quantity_per_unit": str(factor),
        "base_quantity": str(quantity_base),
    }

    in_transit_location_id = await ensure_custody_location(
        session,
        warehouse_id=command.from_warehouse_id,
        custody="in_transit",
        actor_subject=actor.subject,
    )

    transfer_id = uuid4()
    release_group_id = uuid4()
    source_reference = f"TRANSFER:{transfer_id}"
    source_movement_id = uuid4()
    transit_in_movement_id = uuid4()

    await _insert_movement(
        session,
        movement_id=source_movement_id,
        group_id=release_group_id,
        sku_id=sku["sku_id"],
        warehouse_id=command.from_warehouse_id,
        location_id=command.from_location_id,
        movement_type="transfer",
        movement_leg="transfer_source_out",
        quantity_base=quantity_base,
        unit_cost=unit_cost,
        value_delta=-value,
        base_currency=base_currency,
        source_reference=source_reference,
        entered_unit=command.unit_code,
        conversion_snapshot=conversion_snapshot,
        actor_subject=actor.subject,
        correlation_id=request.state.correlation_id,
        idempotency_key=f"{idempotency_key}:source-out",
    )
    await _insert_movement(
        session,
        movement_id=transit_in_movement_id,
        group_id=release_group_id,
        sku_id=sku["sku_id"],
        warehouse_id=command.from_warehouse_id,
        location_id=in_transit_location_id,
        movement_type="transfer",
        movement_leg="transfer_in_transit_in",
        quantity_base=quantity_base,
        unit_cost=unit_cost,
        value_delta=value,
        base_currency=base_currency,
        source_reference=source_reference,
        entered_unit=command.unit_code,
        conversion_snapshot=conversion_snapshot,
        actor_subject=actor.subject,
        correlation_id=request.state.correlation_id,
        idempotency_key=f"{idempotency_key}:transit-in",
    )
    await _record_lot_allocations(
        session,
        movement_ids=(source_movement_id, transit_in_movement_id),
        lot_identity_id=lot_identity_id,
        quantity_base=quantity_base,
    )

    await apply_availability_delta(
        session,
        sku_id=sku["sku_id"],
        warehouse_id=command.from_warehouse_id,
        location_id=command.from_location_id,
        quantity=-quantity_base,
        identity=identity,
        conflict_code="insufficient_inventory_for_transfer",
    )
    await apply_availability_delta(
        session,
        sku_id=sku["sku_id"],
        warehouse_id=command.from_warehouse_id,
        location_id=in_transit_location_id,
        quantity=quantity_base,
        identity=identity,
    )
    await session.execute(
        insert(inventory_transfers).values(
            transfer_id=transfer_id,
            sku_id=sku["sku_id"],
            from_warehouse_id=command.from_warehouse_id,
            to_warehouse_id=command.to_warehouse_id,
            from_location_id=command.from_location_id,
            to_location_id=command.to_location_id,
            quantity_base=quantity_base,
            unit_cost=unit_cost,
            base_currency=base_currency,
            status="released",
            version=1,
            reason=command.reason,
            source_reference=command.source_reference,
            lot_code=command.lot_code,
            requested_by=actor.subject,
            requested_at=func.now(),
            received_by=None,
            received_at=None,
            release_movement_group_id=release_group_id,
            receive_movement_group_id=None,
            correlation_id=request.state.correlation_id,
            idempotency_key=idempotency_key,
        )
    )

    released_item = TransferReleasedItem(
        transfer_id=transfer_id,
        status="released",
        version=1,
        sku_id=sku["sku_id"],
        from_warehouse_id=command.from_warehouse_id,
        to_warehouse_id=command.to_warehouse_id,
        from_location_id=command.from_location_id,
        to_location_id=command.to_location_id,
        quantity_base=quantity_base,
        unit_cost=unit_cost,
        base_currency=base_currency,
        reason=command.reason,
        source_reference=command.source_reference,
        lot_code=command.lot_code,
        requested_by=actor.subject,
        requested_at=_now_iso(),
        release_movement_group_id=release_group_id,
    )
    result = TransferResponse(transfer=released_item)
    await store_command_result(
        session,
        actor_subject=actor.subject,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        result=result,
    )
    await session.commit()
    return result


@router.post(
    "/transfers/{transfer_id}/receive",
    response_model=TransferResponse,
    status_code=201,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def receive_transfer(
    transfer_id: UUID,
    command: ReceiveTransferCommand,
    request: Request,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_inventory_transfer_receiver)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> TransferResponse:
    if idempotency_key is None:
        raise AppError(
            status_code=400,
            code="idempotency_key_required",
            message="Idempotency-Key is required for this command.",
        )

    transfer = await _load_transfer_for_read(session, transfer_id, actor)
    request_hash = command_hash("receive_inventory_transfer", command, str(transfer_id))
    replay = await get_command_replay(
        session,
        actor_subject=actor.subject,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    if replay is not None:
        response.status_code = 200
        response.headers["X-Idempotency-Replayed"] = "true"
        return TransferResponse.model_validate(replay)

    sku = await _load_sku(session, transfer["sku_id"])

    first_warehouse = min(transfer["from_warehouse_id"], transfer["to_warehouse_id"])
    second_warehouse = max(transfer["from_warehouse_id"], transfer["to_warehouse_id"])

    await acquire_projection_rebuild_lock(session, shared=True)
    await acquire_sku_warehouse_lock(session, sku["sku_id"], first_warehouse)
    if second_warehouse != first_warehouse:
        await acquire_sku_warehouse_lock(session, sku["sku_id"], second_warehouse)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"inventory-transfer:{transfer_id}"},
    )

    transfer = await _load_transfer_for_receive(
        session,
        transfer_id,
        actor,
        expected_version=command.expected_version,
    )
    identity, lot_identity_id = await _load_transfer_identity(
        session,
        sku_id=transfer["sku_id"],
        lot_code=transfer["lot_code"],
    )

    in_transit_location_id = await ensure_custody_location(
        session,
        warehouse_id=transfer["from_warehouse_id"],
        custody="in_transit",
        actor_subject=actor.subject,
    )

    receive_group_id = uuid4()
    transit_out_movement_id = uuid4()
    destination_in_movement_id = uuid4()
    source_reference = f"TRANSFER:{transfer_id}"
    quantity_base = transfer["quantity_base"]
    unit_cost = transfer["unit_cost"]
    value = (quantity_base * unit_cost).quantize(
        currency_quantum(transfer["base_currency"]), ROUND_HALF_UP
    )
    conversion_snapshot = {
        "entered_quantity": str(quantity_base),
        "entered_unit": sku["base_stocking_unit"],
        "base_quantity_per_unit": "1.000000",
        "base_quantity": str(quantity_base),
    }

    await _insert_movement(
        session,
        movement_id=transit_out_movement_id,
        group_id=receive_group_id,
        sku_id=sku["sku_id"],
        warehouse_id=transfer["from_warehouse_id"],
        location_id=in_transit_location_id,
        movement_type="transfer",
        movement_leg="transfer_in_transit_out",
        quantity_base=quantity_base,
        unit_cost=unit_cost,
        value_delta=-value,
        base_currency=transfer["base_currency"],
        source_reference=source_reference,
        entered_unit=sku["base_stocking_unit"],
        conversion_snapshot=conversion_snapshot,
        actor_subject=actor.subject,
        correlation_id=request.state.correlation_id,
        idempotency_key=f"{idempotency_key}:transit-out",
    )
    await _insert_movement(
        session,
        movement_id=destination_in_movement_id,
        group_id=receive_group_id,
        sku_id=sku["sku_id"],
        warehouse_id=transfer["to_warehouse_id"],
        location_id=transfer["to_location_id"],
        movement_type="transfer",
        movement_leg="transfer_destination_in",
        quantity_base=quantity_base,
        unit_cost=unit_cost,
        value_delta=value,
        base_currency=transfer["base_currency"],
        source_reference=source_reference,
        entered_unit=sku["base_stocking_unit"],
        conversion_snapshot=conversion_snapshot,
        actor_subject=actor.subject,
        correlation_id=request.state.correlation_id,
        idempotency_key=f"{idempotency_key}:destination-in",
    )
    await _record_lot_allocations(
        session,
        movement_ids=(transit_out_movement_id, destination_in_movement_id),
        lot_identity_id=lot_identity_id,
        quantity_base=quantity_base,
    )

    await apply_availability_delta(
        session,
        sku_id=sku["sku_id"],
        warehouse_id=transfer["from_warehouse_id"],
        location_id=in_transit_location_id,
        quantity=-quantity_base,
        identity=identity,
        conflict_code="transfer_in_transit_depleted",
    )
    await apply_availability_delta(
        session,
        sku_id=sku["sku_id"],
        warehouse_id=transfer["to_warehouse_id"],
        location_id=transfer["to_location_id"],
        quantity=quantity_base,
        identity=identity,
    )
    await apply_valuation_delta(
        session,
        sku_id=sku["sku_id"],
        warehouse_id=transfer["from_warehouse_id"],
        quantity_delta=-quantity_base,
        value_delta=-value,
        allow_create=False,
        missing_code="inventory_valuation_missing",
    )
    await apply_valuation_delta(
        session,
        sku_id=sku["sku_id"],
        warehouse_id=transfer["to_warehouse_id"],
        quantity_delta=quantity_base,
        value_delta=value,
        allow_create=True,
    )

    await session.execute(
        update(inventory_transfers)
        .where(inventory_transfers.c.transfer_id == transfer_id)
        .values(
            status="received",
            version=inventory_transfers.c.version + 1,
            received_by=actor.subject,
            received_at=func.now(),
            receive_movement_group_id=receive_group_id,
        )
    )

    received_item = TransferReceivedItem(
        transfer_id=transfer_id,
        status="received",
        version=transfer["version"] + 1,
        sku_id=sku["sku_id"],
        from_warehouse_id=transfer["from_warehouse_id"],
        to_warehouse_id=transfer["to_warehouse_id"],
        from_location_id=transfer["from_location_id"],
        to_location_id=transfer["to_location_id"],
        quantity_base=quantity_base,
        unit_cost=unit_cost,
        base_currency=transfer["base_currency"],
        reason=transfer["reason"],
        source_reference=transfer["source_reference"],
        lot_code=transfer["lot_code"],
        requested_by=transfer["requested_by"],
        requested_at=transfer["requested_at"].isoformat(),
        received_by=actor.subject,
        received_at=_now_iso(),
        release_movement_group_id=transfer["release_movement_group_id"],
        receive_movement_group_id=receive_group_id,
    )
    result = TransferResponse(transfer=received_item)
    await store_command_result(
        session,
        actor_subject=actor.subject,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        result=result,
    )
    await session.commit()
    return result


async def _load_transfer_for_receive(
    session: AsyncSession,
    transfer_id: UUID,
    actor: AuthorizedUser,
    *,
    expected_version: int,
) -> dict[str, Any]:
    transfer = (
        (
            await session.execute(
                select(inventory_transfers)
                .where(inventory_transfers.c.transfer_id == transfer_id)
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if transfer is None:
        raise AppError(404, "transfer_not_found", "The transfer does not exist.")
    if (
        transfer["from_warehouse_id"] not in actor.warehouse_ids
        or transfer["to_warehouse_id"] not in actor.warehouse_ids
    ):
        raise AppError(
            403,
            "transfer_forbidden",
            "You are not authorized to receive this transfer.",
        )
    if transfer["status"] == "received":
        raise AppError(
            409,
            "transfer_already_received",
            "The transfer has already been received.",
        )
    if transfer["version"] != expected_version:
        raise AppError(
            409,
            "transfer_version_conflict",
            "The transfer changed after it was loaded. Refresh and retry.",
        )
    return dict(transfer)


@router.get(
    "/transfers/{transfer_id}",
    response_model=TransferResponse,
    responses=error_responses(401, 403, 404, 500),
)
async def get_transfer(
    transfer_id: UUID,
    actor: Annotated[AuthorizedUser, Depends(require_inventory_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> TransferResponse:
    transfer = await _load_transfer_for_read(session, transfer_id, actor)
    return TransferResponse(transfer=_transfer_item(transfer))


@router.get(
    "/transfers",
    response_model=TransferListResponse,
    responses=error_responses(401, 403, 422, 500),
)
async def list_transfers(
    actor: Annotated[AuthorizedUser, Depends(require_inventory_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    sku_id: Annotated[UUID | None, Query()] = None,
    status: Annotated[str | None, Query(pattern=r"^(released|received)?$")] = None,
    from_warehouse_id: Annotated[UUID | None, Query()] = None,
    to_warehouse_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TransferListResponse:
    if not actor.warehouse_ids:
        return TransferListResponse(items=[], total=0)

    filters: list[ColumnElement[bool]] = [
        inventory_transfers.c.from_warehouse_id.in_(actor.warehouse_ids),
        inventory_transfers.c.to_warehouse_id.in_(actor.warehouse_ids),
    ]
    if sku_id is not None:
        filters.append(inventory_transfers.c.sku_id == sku_id)
    if status is not None:
        filters.append(inventory_transfers.c.status == status)
    if from_warehouse_id is not None:
        filters.append(inventory_transfers.c.from_warehouse_id == from_warehouse_id)
    if to_warehouse_id is not None:
        filters.append(inventory_transfers.c.to_warehouse_id == to_warehouse_id)

    statement = (
        select(inventory_transfers)
        .where(*filters)
        .order_by(inventory_transfers.c.requested_at.desc())
    )
    total = await session.scalar(select(func.count()).select_from(statement.subquery()))
    rows = (await session.execute(statement.offset(offset).limit(limit))).mappings().all()
    return TransferListResponse(
        items=[_transfer_item(dict(row)) for row in rows],
        total=total or 0,
    )


async def _load_transfer_for_read(
    session: AsyncSession,
    transfer_id: UUID,
    actor: AuthorizedUser,
) -> dict[str, Any]:
    transfer = (
        (
            await session.execute(
                select(inventory_transfers).where(inventory_transfers.c.transfer_id == transfer_id)
            )
        )
        .mappings()
        .one_or_none()
    )
    if transfer is None:
        raise AppError(404, "transfer_not_found", "The transfer does not exist.")
    if (
        transfer["from_warehouse_id"] not in actor.warehouse_ids
        or transfer["to_warehouse_id"] not in actor.warehouse_ids
    ):
        raise AppError(
            403,
            "transfer_forbidden",
            "You are not authorized to view this transfer.",
        )
    return dict(transfer)


def _transfer_item(transfer: dict[str, Any]) -> TransferReceivedItem | TransferReleasedItem:
    common = {
        "transfer_id": transfer["transfer_id"],
        "status": transfer["status"],
        "version": transfer["version"],
        "sku_id": transfer["sku_id"],
        "from_warehouse_id": transfer["from_warehouse_id"],
        "to_warehouse_id": transfer["to_warehouse_id"],
        "from_location_id": transfer["from_location_id"],
        "to_location_id": transfer["to_location_id"],
        "quantity_base": transfer["quantity_base"],
        "unit_cost": transfer["unit_cost"],
        "base_currency": transfer["base_currency"],
        "reason": transfer["reason"],
        "source_reference": transfer["source_reference"],
        "lot_code": transfer["lot_code"],
        "requested_by": transfer["requested_by"],
        "requested_at": cast(str, transfer["requested_at"].isoformat()),
        "release_movement_group_id": transfer["release_movement_group_id"],
    }
    if transfer["status"] == "received":
        return TransferReceivedItem(
            **common,
            received_by=cast(str, transfer["received_by"]),
            received_at=cast(str, transfer["received_at"].isoformat()),
            receive_movement_group_id=cast(UUID, transfer["receive_movement_group_id"]),
        )
    return TransferReleasedItem(**common)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
