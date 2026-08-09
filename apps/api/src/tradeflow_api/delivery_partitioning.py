from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tradeflow_api.errors import AppError
from tradeflow_api.models import (
    delivery_confirmation_identity_partitions,
    delivery_confirmation_lines,
    delivery_exception_case_evidence,
    delivery_exception_cases,
    delivery_exception_event_evidence,
    delivery_exception_events,
    delivery_exception_state,
    delivery_line_identity_allocations,
    inventory_availability,
    lot_identities,
    pick_identity_assignments,
    stock_movement_identity_allocations,
    stock_movements,
    stock_serial_allocations,
    warehouse_stock_locations,
)

ZERO = Decimal("0")
SIX_PLACES = Decimal("0.000001")
OUTCOME_FIELDS = (
    "accepted_quantity_base",
    "refused_quantity_base",
    "damaged_quantity_base",
    "short_missing_quantity_base",
    "still_undelivered_quantity_base",
)


def validate_delivery_partitions(
    delivery_lines: Sequence[Mapping[str, Any]], commands: Sequence[Any]
) -> dict[UUID, Any]:
    resolved: list[tuple[UUID, Any]] = []
    for command in commands:
        delivery_line_id = command.delivery_line_id
        if delivery_line_id is None:
            matches = [line for line in delivery_lines if line["line_id"] == command.line_id]
            matching_total = sum((cast(Decimal, line["quantity_base"]) for line in matches), ZERO)
            legacy_full_acceptance = (
                bool(matches)
                and command.accepted_quantity_base == matching_total
                and all(
                    cast(Decimal, getattr(command, field)) == ZERO for field in OUTCOME_FIELDS[1:]
                )
            )
            if not legacy_full_acceptance:
                raise AppError(
                    409,
                    "delivery_quantity_conflict",
                    "Accepted quantity must equal the dispatched Sales Order Line quantity.",
                )
            for line in matches:
                resolved.append(
                    (
                        cast(UUID, line["delivery_line_id"]),
                        command.model_copy(
                            update={
                                "delivery_line_id": line["delivery_line_id"],
                                "accepted_quantity_base": line["quantity_base"],
                            }
                        ),
                    )
                )
            continue
        resolved.append((delivery_line_id, command))
    supplied = dict(resolved)
    expected_ids = {cast(UUID, line["delivery_line_id"]) for line in delivery_lines}
    if len(supplied) != len(resolved) or set(supplied) != expected_ids:
        raise AppError(
            409,
            "delivery_partition_conflict",
            "Every dispatched Delivery Line must be partitioned exactly once.",
        )
    for line in delivery_lines:
        command = supplied[cast(UUID, line["delivery_line_id"])]
        total = sum((cast(Decimal, getattr(command, field)) for field in OUTCOME_FIELDS), ZERO)
        if total != cast(Decimal, line["quantity_base"]):
            raise AppError(
                409,
                "delivery_partition_conflict",
                "Delivery outcome quantities must equal the dispatched quantity exactly.",
            )
    return supplied


async def ensure_custody_location(
    session: AsyncSession,
    *,
    warehouse_id: UUID,
    custody: str,
    actor_subject: str,
) -> UUID:
    location_id = await session.scalar(
        select(warehouse_stock_locations.c.location_id).where(
            warehouse_stock_locations.c.warehouse_id == warehouse_id,
            warehouse_stock_locations.c.custody == custody,
            warehouse_stock_locations.c.is_active.is_(True),
        )
    )
    if location_id is not None:
        return cast(UUID, location_id)
    location_id = uuid4()
    code = custody.upper().replace("_", "-")
    inserted = await session.scalar(
        pg_insert(warehouse_stock_locations)
        .values(
            location_id=location_id,
            warehouse_id=warehouse_id,
            code=code,
            name=custody.replace("_", " ").title(),
            custody=custody,
            is_active=True,
            created_by=actor_subject,
        )
        .on_conflict_do_nothing()
        .returning(warehouse_stock_locations.c.location_id)
    )
    if inserted is not None:
        return cast(UUID, inserted)
    existing = await session.scalar(
        select(warehouse_stock_locations.c.location_id).where(
            warehouse_stock_locations.c.warehouse_id == warehouse_id,
            warehouse_stock_locations.c.custody == custody,
            warehouse_stock_locations.c.is_active.is_(True),
        )
    )
    if existing is None:
        raise AppError(409, "custody_location_conflict", "Custody location changed; retry.")
    return cast(UUID, existing)


async def _identity_allocations(
    session: AsyncSession, delivery_line_id: UUID
) -> list[Mapping[str, Any]]:
    return cast(
        list[Mapping[str, Any]],
        list(
            (
                await session.execute(
                    select(
                        delivery_line_identity_allocations,
                        pick_identity_assignments.c.tracking_policy,
                        pick_identity_assignments.c.lot_identity_id,
                        pick_identity_assignments.c.serial_allocation_id,
                        lot_identities.c.lot_code,
                        stock_serial_allocations.c.serial_number,
                    )
                    .join(
                        pick_identity_assignments,
                        delivery_line_identity_allocations.c.pick_identity_assignment_id
                        == pick_identity_assignments.c.pick_identity_assignment_id,
                    )
                    .outerjoin(
                        lot_identities,
                        pick_identity_assignments.c.lot_identity_id
                        == lot_identities.c.lot_identity_id,
                    )
                    .outerjoin(
                        stock_serial_allocations,
                        pick_identity_assignments.c.serial_allocation_id
                        == stock_serial_allocations.c.serial_allocation_id,
                    )
                    .where(
                        delivery_line_identity_allocations.c.delivery_line_id == delivery_line_id
                    )
                    .order_by(
                        lot_identities.c.lot_code,
                        stock_serial_allocations.c.serial_number,
                        delivery_line_identity_allocations.c.allocation_id,
                    )
                )
            ).mappings()
        ),
    )


def _allocate_outcomes(
    allocations: Sequence[Mapping[str, Any]], command: Any
) -> list[dict[str, Decimal | UUID]]:
    if not allocations:
        if command.identity_partitions:
            raise AppError(
                409,
                "delivery_identity_partition_conflict",
                "Untracked stock cannot include tracked identity partitions.",
            )
        return []
    if not command.identity_partitions:
        all_accepted = command.accepted_quantity_base == sum(
            (cast(Decimal, row["quantity_base"]) for row in allocations), ZERO
        ) and all(cast(Decimal, getattr(command, field)) == ZERO for field in OUTCOME_FIELDS[1:])
        if all_accepted:
            return [
                {
                    "allocation_id": cast(UUID, allocation["allocation_id"]),
                    "accepted_quantity_base": cast(Decimal, allocation["quantity_base"]),
                    "refused_quantity_base": ZERO,
                    "damaged_quantity_base": ZERO,
                    "short_missing_quantity_base": ZERO,
                    "still_undelivered_quantity_base": ZERO,
                }
                for allocation in allocations
            ]
    supplied = {
        item.delivery_line_identity_allocation_id: item for item in command.identity_partitions
    }
    expected = {cast(UUID, item["allocation_id"]) for item in allocations}
    if len(supplied) != len(command.identity_partitions) or set(supplied) != expected:
        raise AppError(
            409,
            "delivery_identity_partition_conflict",
            "Every tracked Delivery position must be partitioned exactly once.",
        )
    totals = {field: ZERO for field in OUTCOME_FIELDS}
    result: list[dict[str, Decimal | UUID]] = []
    for allocation in allocations:
        allocation_id = cast(UUID, allocation["allocation_id"])
        item = supplied[allocation_id]
        row: dict[str, Decimal | UUID] = {"allocation_id": allocation_id}
        allocation_total = ZERO
        for field in OUTCOME_FIELDS:
            quantity = cast(Decimal, getattr(item, field))
            row[field] = quantity
            totals[field] += quantity
            allocation_total += quantity
        if allocation_total != cast(Decimal, allocation["quantity_base"]):
            raise AppError(
                409,
                "delivery_identity_partition_conflict",
                "Tracked outcome quantities must equal the position quantity exactly.",
            )
        if allocation["tracking_policy"] == "serial" and sorted(
            cast(Decimal, row[field]) for field in OUTCOME_FIELDS
        ) != [ZERO, ZERO, ZERO, ZERO, Decimal("1")]:
            raise AppError(
                409,
                "delivery_identity_partition_conflict",
                "Each serial identity must be assigned whole to exactly one Delivery outcome.",
            )
        result.append(row)
    if any(totals[field] != cast(Decimal, getattr(command, field)) for field in OUTCOME_FIELDS):
        raise AppError(
            409,
            "delivery_identity_partition_conflict",
            "Tracked position outcomes must equal the Delivery Line partition.",
        )
    return result


async def validate_delivery_identity_partitions(
    session: AsyncSession, line: Mapping[str, Any], command: Any
) -> tuple[list[Mapping[str, Any]], list[dict[str, Decimal | UUID]]]:
    allocations = await _identity_allocations(session, cast(UUID, line["delivery_line_id"]))
    allocation_policies = {allocation["tracking_policy"] for allocation in allocations}
    tracking_policy = line["tracking_policy"]
    valid_tracking = (tracking_policy == "untracked" and not allocations) or (
        tracking_policy in {"lot", "serial"}
        and allocation_policies == {tracking_policy}
        and sum((cast(Decimal, row["quantity_base"]) for row in allocations), ZERO)
        == cast(Decimal, line["quantity_base"])
    )
    if not valid_tracking:
        raise AppError(
            409,
            "delivery_tracking_policy_conflict",
            "Current SKU Tracking Policy no longer matches dispatched identities.",
        )
    return allocations, _allocate_outcomes(allocations, command)


async def _move_position(
    session: AsyncSession,
    *,
    sku_id: UUID,
    warehouse_id: UUID,
    source_location_id: UUID,
    destination_location_id: UUID | None,
    identity_key: str,
    quantity: Decimal,
    lot_code: str | None,
    serial_numbers: list[str],
) -> None:
    if quantity == ZERO:
        return
    source = (
        (
            await session.execute(
                select(inventory_availability)
                .where(
                    inventory_availability.c.sku_id == sku_id,
                    inventory_availability.c.warehouse_id == warehouse_id,
                    inventory_availability.c.location_id == source_location_id,
                    inventory_availability.c.identity_key == identity_key,
                )
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if source is None or cast(Decimal, source["on_hand"]) < quantity:
        raise AppError(
            409,
            "delivery_inventory_conflict",
            "Current custody quantity or tracked identity changed; refresh before posting.",
        )
    remaining_serials = sorted(set(source["serial_numbers"]) - set(serial_numbers))
    await session.execute(
        update(inventory_availability)
        .where(
            inventory_availability.c.sku_id == sku_id,
            inventory_availability.c.warehouse_id == warehouse_id,
            inventory_availability.c.location_id == source_location_id,
            inventory_availability.c.identity_key == identity_key,
        )
        .values(
            on_hand=inventory_availability.c.on_hand - quantity,
            serial_numbers=remaining_serials,
        )
    )
    if destination_location_id is not None:
        await session.execute(
            pg_insert(inventory_availability)
            .values(
                sku_id=sku_id,
                warehouse_id=warehouse_id,
                location_id=destination_location_id,
                identity_key=identity_key,
                lot_code=lot_code,
                serial_numbers=serial_numbers,
                expiration_date=source["expiration_date"],
                on_hand=quantity,
                reserved=ZERO,
            )
            .on_conflict_do_update(
                index_elements=["sku_id", "warehouse_id", "location_id", "identity_key"],
                set_={
                    "on_hand": inventory_availability.c.on_hand + quantity,
                    "serial_numbers": serial_numbers,
                },
            )
        )


async def post_delivery_partition_line(
    session: AsyncSession,
    *,
    confirmation_id: UUID,
    line: Mapping[str, Any],
    command: Any,
    warehouse_id: UUID,
    transit_location_id: UUID,
    investigation_location_id: UUID,
    unit_cost: Decimal,
    base_currency: str,
    actor_subject: str,
    correlation_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    accepted = cast(Decimal, command.accepted_quantity_base)
    short_missing = cast(Decimal, command.short_missing_quantity_base)
    confirmation_line_id = uuid4()
    outbound_movement_id = uuid4() if accepted > ZERO else None
    value_delta = -(accepted * unit_cost).quantize(SIX_PLACES, rounding=ROUND_HALF_UP)

    allocations, partitioned = await validate_delivery_identity_partitions(session, line, command)
    if allocations:
        for allocation, partition in zip(allocations, partitioned, strict=True):
            accepted_part = cast(Decimal, partition["accepted_quantity_base"])
            short_part = cast(Decimal, partition["short_missing_quantity_base"])
            identity_key = (
                f"lot:{allocation['lot_code']}"
                if allocation["tracking_policy"] == "lot"
                else f"serial:{allocation['serial_number']}"
            )
            serials = (
                [cast(str, allocation["serial_number"])]
                if allocation["tracking_policy"] == "serial"
                else []
            )
            await _move_position(
                session,
                sku_id=cast(UUID, line["sku_id"]),
                warehouse_id=warehouse_id,
                source_location_id=transit_location_id,
                destination_location_id=None,
                identity_key=identity_key,
                quantity=accepted_part,
                lot_code=cast(str | None, allocation["lot_code"]),
                serial_numbers=serials if accepted_part else [],
            )
            await _move_position(
                session,
                sku_id=cast(UUID, line["sku_id"]),
                warehouse_id=warehouse_id,
                source_location_id=transit_location_id,
                destination_location_id=investigation_location_id,
                identity_key=identity_key,
                quantity=short_part,
                lot_code=cast(str | None, allocation["lot_code"]),
                serial_numbers=serials if short_part else [],
            )
    else:
        await _move_position(
            session,
            sku_id=cast(UUID, line["sku_id"]),
            warehouse_id=warehouse_id,
            source_location_id=transit_location_id,
            destination_location_id=None,
            identity_key="",
            quantity=accepted,
            lot_code=None,
            serial_numbers=[],
        )
        await _move_position(
            session,
            sku_id=cast(UUID, line["sku_id"]),
            warehouse_id=warehouse_id,
            source_location_id=transit_location_id,
            destination_location_id=investigation_location_id,
            identity_key="",
            quantity=short_missing,
            lot_code=None,
            serial_numbers=[],
        )

    if outbound_movement_id is not None:
        await session.execute(
            insert(stock_movements).values(
                movement_id=outbound_movement_id,
                sku_id=line["sku_id"],
                warehouse_id=warehouse_id,
                location_id=transit_location_id,
                movement_type="delivery_confirmation",
                quantity_base=accepted,
                unit_cost=unit_cost,
                value_delta=value_delta,
                base_currency=base_currency,
                source_reference=f"DELIVERY-CONFIRMATION:{confirmation_id}",
                entered_unit=line["movement_entered_unit"],
                conversion_snapshot={"source": "delivery_confirmation", "factor": "1.000000"},
                actor_subject=actor_subject,
                correlation_id=correlation_id,
                idempotency_key=f"{idempotency_key}:{line['delivery_line_id']}:outbound",
                movement_group_id=uuid4(),
                movement_leg="delivery_outbound",
                reversal_of_movement_id=None,
            )
        )
        accepted_identity_rows = [
            {
                "allocation_id": uuid4(),
                "movement_id": outbound_movement_id,
                "delivery_line_identity_allocation_id": partition["allocation_id"],
                "quantity_base": partition["accepted_quantity_base"],
            }
            for partition in partitioned
            if cast(Decimal, partition["accepted_quantity_base"]) > ZERO
        ]
        if accepted_identity_rows:
            await session.execute(
                insert(stock_movement_identity_allocations), accepted_identity_rows
            )
    await session.execute(
        insert(delivery_confirmation_lines).values(
            confirmation_line_id=confirmation_line_id,
            confirmation_id=confirmation_id,
            delivery_line_id=line["delivery_line_id"],
            line_id=line["line_id"],
            sku_id=line["sku_id"],
            accepted_quantity_base=accepted,
            refused_quantity_base=command.refused_quantity_base,
            damaged_quantity_base=command.damaged_quantity_base,
            short_missing_quantity_base=short_missing,
            still_undelivered_quantity_base=command.still_undelivered_quantity_base,
            unit_cost=unit_cost,
            value_delta=value_delta,
            outbound_movement_id=outbound_movement_id,
        )
    )
    if partitioned:
        await session.execute(
            insert(delivery_confirmation_identity_partitions),
            [
                {
                    "partition_id": uuid4(),
                    "confirmation_line_id": confirmation_line_id,
                    "delivery_line_identity_allocation_id": partition["allocation_id"],
                    **{field: partition[field] for field in OUTCOME_FIELDS},
                }
                for partition in partitioned
            ],
        )

    for kind, field in (
        ("refused", "refused_quantity_base"),
        ("damaged", "damaged_quantity_base"),
        ("short_missing", "short_missing_quantity_base"),
        ("still_undelivered", "still_undelivered_quantity_base"),
    ):
        quantity = cast(Decimal, getattr(command, field))
        if quantity == ZERO:
            continue
        detail = getattr(command.exception_details, kind)
        if detail is None:  # Defensive: the command validator enforces this contract.
            raise AppError(
                422,
                "delivery_exception_detail_required",
                f"Outcome-specific audit detail is required for {kind}.",
            )
        case_id = uuid4()
        initial_custody = "investigation" if kind == "short_missing" else "in_transit"
        movement_group_id: UUID | None = None
        out_id: UUID | None = None
        in_id: UUID | None = None
        if kind == "short_missing":
            movement_group_id, out_id, in_id = uuid4(), uuid4(), uuid4()
            common = {
                "sku_id": line["sku_id"],
                "warehouse_id": warehouse_id,
                "movement_type": "delivery_exception",
                "quantity_base": quantity,
                "unit_cost": unit_cost,
                "base_currency": base_currency,
                "source_reference": f"DELIVERY-EXCEPTION:{case_id}",
                "entered_unit": line["movement_entered_unit"],
                "conversion_snapshot": {"source": "delivery_exception", "factor": "1.000000"},
                "actor_subject": actor_subject,
                "correlation_id": correlation_id,
                "movement_group_id": movement_group_id,
                "reversal_of_movement_id": None,
            }
            value = (quantity * unit_cost).quantize(SIX_PLACES, rounding=ROUND_HALF_UP)
            await session.execute(
                insert(stock_movements),
                [
                    {
                        **common,
                        "movement_id": out_id,
                        "location_id": transit_location_id,
                        "value_delta": -value,
                        "idempotency_key": f"{idempotency_key}:{case_id}:investigation-out",
                        "movement_leg": "exception_transit_out",
                    },
                    {
                        **common,
                        "movement_id": in_id,
                        "location_id": investigation_location_id,
                        "value_delta": value,
                        "idempotency_key": f"{idempotency_key}:{case_id}:investigation-in",
                        "movement_leg": "exception_investigation_in",
                    },
                ],
            )
            short_identity_rows = [
                {
                    "allocation_id": uuid4(),
                    "movement_id": movement_id,
                    "delivery_line_identity_allocation_id": partition["allocation_id"],
                    "quantity_base": partition["short_missing_quantity_base"],
                }
                for movement_id in (out_id, in_id)
                for partition in partitioned
                if cast(Decimal, partition["short_missing_quantity_base"]) > ZERO
            ]
            if short_identity_rows:
                await session.execute(
                    insert(stock_movement_identity_allocations), short_identity_rows
                )
        await session.execute(
            insert(delivery_exception_cases).values(
                exception_case_id=case_id,
                confirmation_line_id=confirmation_line_id,
                exception_kind=kind,
                original_quantity_base=quantity,
                initial_custody=initial_custody,
                responsible_party_type=detail.responsible_party_type,
                responsible_subject=detail.responsible_subject,
                responsible_snapshot={"reason": detail.reason},
                investigation_movement_group_id=movement_group_id,
                investigation_out_movement_id=out_id,
                investigation_in_movement_id=in_id,
                opened_by=actor_subject,
                correlation_id=correlation_id,
            )
        )
        await session.execute(
            insert(delivery_exception_state).values(
                exception_case_id=case_id,
                status="open",
                custody=initial_custody,
                open_quantity_base=quantity,
            )
        )
        event_id = uuid4()
        await session.execute(
            insert(delivery_exception_events).values(
                exception_event_id=event_id,
                exception_case_id=case_id,
                event_type="opened",
                quantity_base=quantity,
                source_document_type="delivery_confirmation",
                source_document_id=confirmation_id,
                from_custody="in_transit",
                to_custody=initial_custody,
                reason=detail.reason,
                actor_subject=actor_subject,
                correlation_id=correlation_id,
                idempotency_key=f"{idempotency_key}:{case_id}:opened",
            )
        )
        await session.execute(
            insert(delivery_exception_case_evidence),
            [
                {"exception_case_id": case_id, "evidence_id": evidence_id}
                for evidence_id in detail.evidence_ids
            ],
        )
        await session.execute(
            insert(delivery_exception_event_evidence),
            [
                {"exception_event_id": event_id, "evidence_id": evidence_id}
                for evidence_id in detail.evidence_ids
            ],
        )
    return {
        "confirmation_line_id": confirmation_line_id,
        "accepted_quantity_base": accepted,
        "value_delta": value_delta,
        "unit_cost": unit_cost,
        "outbound_movement_id": outbound_movement_id,
    }
