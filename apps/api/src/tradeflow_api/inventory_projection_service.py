from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tradeflow_api.errors import AppError
from tradeflow_api.models import inventory_availability, inventory_valuation

PROJECTION_REBUILD_LOCK_KEY = "inventory-projection-rebuild"
SIX_PLACES = Decimal("0.000001")
ZERO = Decimal("0")


@dataclass(frozen=True)
class AvailabilityIdentity:
    """Tracked identity partition for an inventory_availability row."""

    identity_key: str
    lot_code: str | None = None
    serial_numbers: Sequence[str] = ()
    expiration_date: date | None = None
    merge_serials: bool = False


@dataclass(frozen=True)
class ValuationRow:
    """Result of applying a valuation delta."""

    quantity_on_hand: Decimal
    inventory_value: Decimal
    moving_average_unit_cost: Decimal


async def acquire_projection_rebuild_lock(session: AsyncSession, *, shared: bool = True) -> None:
    """Acquire the inventory-projection-rebuild advisory lock.

    Callers that mutate projections incrementally must hold the shared lock.
    Rebuilds that truncate and replay projections must hold the exclusive lock.
    """
    lock_fn = "pg_advisory_xact_lock_shared" if shared else "pg_advisory_xact_lock"
    await session.execute(
        text(f"SELECT {lock_fn}(hashtext(:key))"),
        {"key": PROJECTION_REBUILD_LOCK_KEY},
    )


async def acquire_sku_warehouse_lock(
    session: AsyncSession, sku_id: UUID, warehouse_id: UUID
) -> None:
    """Acquire the per-sku/warehouse advisory lock.

    The canonical lock order is:
    1. projection-rebuild lock (shared or exclusive)
    2. per-sku/warehouse advisory lock
    3. row-level lock via SELECT ... FOR UPDATE
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"{sku_id}:{warehouse_id}"},
    )


async def apply_availability_delta(
    session: AsyncSession,
    sku_id: UUID,
    warehouse_id: UUID,
    location_id: UUID,
    quantity: Decimal,
    identity: AvailabilityIdentity | None = None,
    *,
    conflict_code: str = "inventory_projection_conflict",
    conflict_message: str | None = None,
) -> None:
    """Apply a signed quantity change to a single inventory_availability row.

    Positive quantities upsert an inbound row; negative quantities decrement an
    existing row after locking it and verifying sufficient on-hand quantity.
    """
    if quantity == ZERO:
        return

    if quantity > ZERO:
        await _increase_availability(
            session,
            sku_id=sku_id,
            warehouse_id=warehouse_id,
            location_id=location_id,
            quantity=quantity,
            identity=identity,
        )
        return

    await _decrease_availability(
        session,
        sku_id=sku_id,
        warehouse_id=warehouse_id,
        location_id=location_id,
        quantity=-quantity,
        identity=identity,
        conflict_code=conflict_code,
        conflict_message=conflict_message,
    )


async def _increase_availability(
    session: AsyncSession,
    *,
    sku_id: UUID,
    warehouse_id: UUID,
    location_id: UUID,
    quantity: Decimal,
    identity: AvailabilityIdentity | None,
) -> None:
    values: dict[str, object] = {
        "sku_id": sku_id,
        "warehouse_id": warehouse_id,
        "location_id": location_id,
        "on_hand": quantity,
        "reserved": ZERO,
    }
    set_: dict[str, object] = {"on_hand": inventory_availability.c.on_hand + quantity}

    if identity is None:
        values["identity_key"] = ""
        values["lot_code"] = None
        values["serial_numbers"] = []
        values["expiration_date"] = None
    else:
        serial_numbers = list(identity.serial_numbers)
        if identity.merge_serials:
            existing = (
                (
                    await session.execute(
                        select(inventory_availability.c.serial_numbers).where(
                            inventory_availability.c.sku_id == sku_id,
                            inventory_availability.c.warehouse_id == warehouse_id,
                            inventory_availability.c.location_id == location_id,
                            inventory_availability.c.identity_key == identity.identity_key,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                serial_numbers = sorted(
                    set(existing["serial_numbers"]) | set(identity.serial_numbers)
                )
        values["identity_key"] = identity.identity_key
        values["lot_code"] = identity.lot_code
        values["serial_numbers"] = serial_numbers
        values["expiration_date"] = identity.expiration_date
        set_["expiration_date"] = identity.expiration_date
        set_["serial_numbers"] = serial_numbers

    await session.execute(
        pg_insert(inventory_availability)
        .values(**values)
        .on_conflict_do_update(
            index_elements=["sku_id", "warehouse_id", "location_id", "identity_key"],
            set_=set_,
        )
    )


async def _decrease_availability(
    session: AsyncSession,
    *,
    sku_id: UUID,
    warehouse_id: UUID,
    location_id: UUID,
    quantity: Decimal,
    identity: AvailabilityIdentity | None,
    conflict_code: str,
    conflict_message: str | None = None,
) -> None:
    identity_key = identity.identity_key if identity is not None else ""
    position = (
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
    if position is None or position["on_hand"] < quantity:
        raise AppError(
            409,
            conflict_code,
            conflict_message or "Insufficient inventory for projection update.",
        )

    remaining_serials: list[str] = position["serial_numbers"]
    if identity is not None and identity.serial_numbers:
        remaining_serials = sorted(set(position["serial_numbers"]) - set(identity.serial_numbers))

    await session.execute(
        update(inventory_availability)
        .where(
            inventory_availability.c.sku_id == sku_id,
            inventory_availability.c.warehouse_id == warehouse_id,
            inventory_availability.c.location_id == location_id,
            inventory_availability.c.identity_key == identity_key,
        )
        .values(
            on_hand=inventory_availability.c.on_hand - quantity,
            serial_numbers=remaining_serials,
        )
    )


async def apply_valuation_delta(
    session: AsyncSession,
    sku_id: UUID,
    warehouse_id: UUID,
    quantity_delta: Decimal,
    value_delta: Decimal,
    *,
    allow_create: bool = False,
    missing_code: str = "inventory_valuation_missing",
) -> ValuationRow:
    """Apply a signed quantity/value delta to inventory_valuation.

    The row is locked with SELECT ... FOR UPDATE before reading. If the row is
    missing and allow_create is False, a domain AppError is raised.
    """
    current = (
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
    if current is None and not allow_create:
        raise AppError(
            409,
            missing_code,
            f"SKU valuation is not initialized for warehouse {warehouse_id}.",
        )

    quantity = quantity_delta + (current["quantity_on_hand"] if current is not None else ZERO)
    value = value_delta + (current["inventory_value"] if current is not None else ZERO)
    average = (
        (value / quantity).quantize(SIX_PLACES, ROUND_HALF_UP)
        if quantity != ZERO
        else current["moving_average_unit_cost"]
        if current is not None
        else ZERO
    )

    await session.execute(
        pg_insert(inventory_valuation)
        .values(
            sku_id=sku_id,
            warehouse_id=warehouse_id,
            quantity_on_hand=quantity,
            inventory_value=value,
            moving_average_unit_cost=average,
        )
        .on_conflict_do_update(
            index_elements=["sku_id", "warehouse_id"],
            set_={
                "quantity_on_hand": quantity,
                "inventory_value": value,
                "moving_average_unit_cost": average,
            },
        )
    )
    return ValuationRow(
        quantity_on_hand=quantity,
        inventory_value=value,
        moving_average_unit_cost=average,
    )
