from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256
from typing import Annotated, Any, Literal, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from tradeflow_api.auth import (
    AuthorizedUser,
    require_delivery_exception_reader,
    require_delivery_retrier,
    require_investigation_resolver,
    require_return_to_warehouse_receiver,
)
from tradeflow_api.command_receipts import get_command_replay, store_command_result
from tradeflow_api.database import get_database_session
from tradeflow_api.delivery_partitioning import _move_position, ensure_custody_location
from tradeflow_api.dispatch import (
    AssignedDeliveryResponse,
    _assigned_delivery_response,
    _assignee_is_authorized,
    _delivery_row,
)
from tradeflow_api.errors import AppError, error_responses
from tradeflow_api.models import (
    approval_authorities,
    companies,
    delivery_confirmation_identity_partitions,
    delivery_confirmation_lines,
    delivery_confirmations,
    delivery_dispatches,
    delivery_evidence,
    delivery_exception_case_evidence,
    delivery_exception_cases,
    delivery_exception_event_evidence,
    delivery_exception_events,
    delivery_exception_state,
    delivery_line_identity_allocations,
    delivery_lines,
    delivery_retry_allocations,
    delivery_state,
    inventory_valuation,
    investigation_resolutions,
    lot_identities,
    pick_identity_assignments,
    return_to_warehouse_receipt_lines,
    return_to_warehouse_receipts,
    skus,
    stock_movement_identity_allocations,
    stock_movements,
    stock_serial_allocations,
    warehouse_stock_locations,
)

router = APIRouter(tags=["delivery exceptions"])
ZERO = Decimal("0")
SIX_PLACES = Decimal("0.000001")


class CommandModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReturnLineCommand(CommandModel):
    delivery_line_id: UUID
    refused_quantity_base: Decimal = Field(default=ZERO, ge=0)
    damaged_quantity_base: Decimal = Field(default=ZERO, ge=0)


class ReturnToWarehouseCommand(CommandModel):
    return_receipt_id: UUID
    expected_delivery_version: int = Field(gt=0)
    received_at: datetime
    evidence_ids: list[UUID] = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=500)
    lines: list[ReturnLineCommand] = Field(min_length=1)


class ReturnLineResponse(BaseModel):
    delivery_line_id: UUID
    exception_case_id: UUID
    exception_kind: Literal["refused", "damaged"]
    quantity_base: Decimal
    custody: Literal["quarantine"]


class ReturnToWarehouseResponse(BaseModel):
    return_receipt_id: UUID
    delivery_id: UUID
    status: Literal["received"]
    lines: list[ReturnLineResponse]


class InvestigationResolutionCommand(CommandModel):
    resolution_id: UUID
    expected_investigation_version: int = Field(gt=0)
    resolution_type: Literal["recovery", "carrier_claim", "inventory_adjustment"]
    reason: str = Field(min_length=1, max_length=500)
    external_reference: str | None = Field(default=None, max_length=200)
    evidence_ids: list[UUID] = Field(min_length=1)


class InvestigationResolutionResponse(BaseModel):
    resolution_id: UUID
    investigation_id: UUID
    resolution_type: str
    quantity_base: Decimal
    status: Literal["resolved"]
    custody: Literal["quarantine", "outbound"]


class RetryDeliveryCommand(CommandModel):
    retry_delivery_id: UUID
    expected_delivery_version: int = Field(gt=0)
    assigned_to: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=500)


class DeliveryExceptionItem(BaseModel):
    investigation_id: UUID | None
    exception_case_id: UUID
    delivery_id: UUID
    delivery_line_id: UUID
    delivery_version: int
    tracking_policy: Literal["untracked", "lot", "serial"]
    exception_kind: str
    original_quantity_base: Decimal
    open_quantity_base: Decimal
    custody: str
    status: str
    responsible_party_type: str
    opened_at: datetime
    age_days: int
    version: int
    evidence_ids: list[UUID]


class DeliveryExceptionListResponse(BaseModel):
    items: list[DeliveryExceptionItem]
    total: int


async def _lock(session: AsyncSession, key: str) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": key},
    )


def _request_hash(kind: str, source_id: UUID, actor: str, command: CommandModel) -> str:
    return sha256(
        f"{kind}:{source_id}:{actor}:{command.model_dump_json(exclude_none=False)}".encode()
    ).hexdigest()


async def _case_row(
    session: AsyncSession, case_id: UUID, *, for_update: bool = False
) -> Mapping[str, Any] | None:
    statement = (
        select(
            delivery_exception_cases,
            delivery_exception_state.c.status,
            delivery_exception_state.c.custody,
            delivery_exception_state.c.open_quantity_base,
            delivery_exception_state.c.returned_quantity_base,
            delivery_exception_state.c.retry_allocated_quantity_base,
            delivery_exception_state.c.resolved_quantity_base,
            delivery_exception_state.c.version,
            delivery_confirmation_lines.c.delivery_line_id,
            delivery_confirmation_lines.c.sku_id,
            delivery_confirmation_lines.c.unit_cost,
            delivery_confirmations.c.delivery_id,
            delivery_dispatches.c.branch_id,
            delivery_dispatches.c.warehouse_id,
            delivery_lines.c.pick_line_id,
        )
        .join(
            delivery_exception_state,
            delivery_exception_cases.c.exception_case_id
            == delivery_exception_state.c.exception_case_id,
        )
        .join(
            delivery_confirmation_lines,
            delivery_exception_cases.c.confirmation_line_id
            == delivery_confirmation_lines.c.confirmation_line_id,
        )
        .join(
            delivery_confirmations,
            delivery_confirmation_lines.c.confirmation_id
            == delivery_confirmations.c.confirmation_id,
        )
        .join(
            delivery_dispatches,
            delivery_confirmations.c.delivery_id == delivery_dispatches.c.delivery_id,
        )
        .join(
            delivery_lines,
            delivery_confirmation_lines.c.delivery_line_id == delivery_lines.c.delivery_line_id,
        )
        .where(delivery_exception_cases.c.exception_case_id == case_id)
    )
    if for_update:
        statement = statement.with_for_update(of=delivery_exception_state)
    return cast(
        Mapping[str, Any] | None,
        (await session.execute(statement)).mappings().one_or_none(),
    )


async def _verified_evidence(
    session: AsyncSession, delivery_id: UUID, evidence_ids: Sequence[UUID]
) -> None:
    if not evidence_ids:
        return
    count = await session.scalar(
        select(func.count()).where(
            delivery_evidence.c.delivery_id == delivery_id,
            delivery_evidence.c.evidence_id.in_(evidence_ids),
            delivery_evidence.c.status == "verified",
        )
    )
    if count != len(set(evidence_ids)):
        raise AppError(
            409,
            "delivery_exception_evidence_conflict",
            "Exception evidence must be verified and belong to this Delivery.",
        )


async def _case_positions(
    session: AsyncSession, case_row: Mapping[str, Any], quantity: Decimal
) -> list[dict[str, Any]]:
    field = f"{case_row['exception_kind']}_quantity_base"
    rows = list(
        (
            await session.execute(
                select(
                    delivery_line_identity_allocations.c.allocation_id,
                    delivery_confirmation_identity_partitions.c[field].label("quantity_base"),
                    pick_identity_assignments.c.tracking_policy,
                    lot_identities.c.lot_code,
                    stock_serial_allocations.c.serial_number,
                )
                .join(
                    delivery_confirmation_identity_partitions,
                    delivery_line_identity_allocations.c.allocation_id
                    == delivery_confirmation_identity_partitions.c[
                        "delivery_line_identity_allocation_id"
                    ],
                )
                .join(
                    pick_identity_assignments,
                    delivery_line_identity_allocations.c.pick_identity_assignment_id
                    == pick_identity_assignments.c.pick_identity_assignment_id,
                )
                .outerjoin(
                    lot_identities,
                    pick_identity_assignments.c.lot_identity_id == lot_identities.c.lot_identity_id,
                )
                .outerjoin(
                    stock_serial_allocations,
                    pick_identity_assignments.c.serial_allocation_id
                    == stock_serial_allocations.c.serial_allocation_id,
                )
                .where(
                    delivery_confirmation_identity_partitions.c.confirmation_line_id
                    == case_row["confirmation_line_id"],
                    delivery_confirmation_identity_partitions.c[field] > ZERO,
                )
                .order_by(
                    lot_identities.c.lot_code,
                    stock_serial_allocations.c.serial_number,
                )
            )
        ).mappings()
    )
    if not rows:
        return [
            {
                "delivery_line_identity_allocation_id": None,
                "identity_key": "",
                "lot_code": None,
                "serial_numbers": [],
                "quantity_base": quantity,
            }
        ]
    remaining = quantity
    positions: list[dict[str, Any]] = []
    for row in rows:
        taken = min(remaining, cast(Decimal, row["quantity_base"]))
        if taken == ZERO:
            continue
        is_serial = row["tracking_policy"] == "serial"
        positions.append(
            {
                "delivery_line_identity_allocation_id": row["allocation_id"],
                "identity_key": (
                    f"serial:{row['serial_number']}" if is_serial else f"lot:{row['lot_code']}"
                ),
                "lot_code": row["lot_code"],
                "serial_numbers": [row["serial_number"]] if is_serial else [],
                "quantity_base": taken,
            }
        )
        remaining -= taken
    if remaining != ZERO:
        raise AppError(409, "delivery_exception_identity_conflict", "Tracked custody changed.")
    return positions


async def _transfer_case_projection(
    session: AsyncSession,
    *,
    case_row: Mapping[str, Any],
    from_location_id: UUID,
    to_location_id: UUID | None,
    positions: Sequence[Mapping[str, Any]],
) -> None:
    for position in positions:
        await _move_position(
            session,
            sku_id=case_row["sku_id"],
            warehouse_id=case_row["warehouse_id"],
            source_location_id=from_location_id,
            destination_location_id=to_location_id,
            identity_key=position["identity_key"],
            quantity=position["quantity_base"],
            lot_code=position["lot_code"],
            serial_numbers=position["serial_numbers"],
        )


async def _movement_pair(
    session: AsyncSession,
    *,
    case_row: Mapping[str, Any],
    quantity: Decimal,
    movement_type: str,
    out_leg: str,
    in_leg: str | None,
    source_location_id: UUID,
    destination_location_id: UUID | None,
    source_reference: str,
    actor: AuthorizedUser,
    correlation_id: str,
    idempotency_key: str,
) -> tuple[UUID, UUID | None, UUID]:
    await session.execute(
        text("SELECT pg_advisory_xact_lock_shared(hashtext('inventory-projection-rebuild'))")
    )
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:stock_key))"),
        {"stock_key": f"{case_row['sku_id']}:{case_row['warehouse_id']}"},
    )
    group_id, out_id = uuid4(), uuid4()
    in_id = uuid4() if in_leg else None
    value = (quantity * case_row["unit_cost"]).quantize(SIX_PLACES, ROUND_HALF_UP)
    base_currency = cast(str, await session.scalar(select(companies.c.base_currency).limit(1)))
    common = {
        "sku_id": case_row["sku_id"],
        "warehouse_id": case_row["warehouse_id"],
        "movement_type": movement_type,
        "quantity_base": quantity,
        "unit_cost": case_row["unit_cost"],
        "base_currency": base_currency,
        "source_reference": source_reference,
        "entered_unit": "BASE",
        "conversion_snapshot": {"source": movement_type, "factor": "1.000000"},
        "actor_subject": actor.subject,
        "correlation_id": correlation_id,
        "movement_group_id": group_id,
        "reversal_of_movement_id": None,
    }
    positions = await _case_positions(session, case_row, quantity)
    movements = [
        {
            **common,
            "movement_id": out_id,
            "location_id": source_location_id,
            "value_delta": -value,
            "idempotency_key": f"{idempotency_key}:out",
            "movement_leg": out_leg,
        }
    ]
    if in_id is not None and in_leg is not None and destination_location_id is not None:
        movements.append(
            {
                **common,
                "movement_id": in_id,
                "location_id": destination_location_id,
                "value_delta": value,
                "idempotency_key": f"{idempotency_key}:in",
                "movement_leg": in_leg,
            }
        )
    await session.execute(insert(stock_movements), movements)
    identity_rows = [
        {
            "allocation_id": uuid4(),
            "movement_id": movement_id,
            "delivery_line_identity_allocation_id": position[
                "delivery_line_identity_allocation_id"
            ],
            "quantity_base": position["quantity_base"],
        }
        for movement_id in (out_id, in_id)
        if movement_id is not None
        for position in positions
        if position["delivery_line_identity_allocation_id"] is not None
    ]
    if identity_rows:
        await session.execute(insert(stock_movement_identity_allocations), identity_rows)
    await _transfer_case_projection(
        session,
        case_row=case_row,
        from_location_id=source_location_id,
        to_location_id=destination_location_id,
        positions=positions,
    )
    return out_id, in_id, group_id


@router.get(
    "/v1/delivery-exceptions",
    response_model=DeliveryExceptionListResponse,
    responses=error_responses(401, 403, 500),
)
async def list_delivery_exceptions(
    actor: Annotated[AuthorizedUser, Depends(require_delivery_exception_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    status: Annotated[str | None, Query()] = None,
) -> DeliveryExceptionListResponse:
    statement = (
        select(
            delivery_exception_cases,
            delivery_exception_state,
            delivery_confirmation_lines.c.delivery_line_id,
            delivery_confirmations.c.delivery_id,
            delivery_state.c.version.label("delivery_version"),
            skus.c.tracking_policy,
        )
        .join(delivery_exception_state)
        .join(delivery_confirmation_lines)
        .join(delivery_confirmations)
        .join(delivery_dispatches)
        .join(delivery_state, delivery_state.c.delivery_id == delivery_dispatches.c.delivery_id)
        .join(skus, skus.c.sku_id == delivery_confirmation_lines.c.sku_id)
        .where(delivery_dispatches.c.branch_id.in_(actor.branch_ids))
        .order_by(delivery_exception_cases.c.opened_at)
    )
    if status:
        statement = statement.where(delivery_exception_state.c.status == status)
    rows = list((await session.execute(statement)).mappings())
    items: list[DeliveryExceptionItem] = []
    for row in rows:
        evidence = list(
            (
                await session.execute(
                    select(delivery_exception_case_evidence.c.evidence_id).where(
                        delivery_exception_case_evidence.c.exception_case_id
                        == row["exception_case_id"]
                    )
                )
            ).scalars()
        )
        age_days = max((datetime.now(row["opened_at"].tzinfo) - row["opened_at"]).days, 0)
        items.append(
            DeliveryExceptionItem(
                investigation_id=(
                    row["exception_case_id"] if row["exception_kind"] == "short_missing" else None
                ),
                exception_case_id=row["exception_case_id"],
                delivery_id=row["delivery_id"],
                delivery_line_id=row["delivery_line_id"],
                delivery_version=row["delivery_version"],
                tracking_policy=row["tracking_policy"],
                exception_kind=row["exception_kind"],
                original_quantity_base=row["original_quantity_base"],
                open_quantity_base=row["open_quantity_base"],
                custody=row["custody"],
                status=row["status"],
                responsible_party_type=row["responsible_party_type"],
                opened_at=row["opened_at"],
                age_days=age_days,
                version=row["version"],
                evidence_ids=evidence,
            )
        )
    return DeliveryExceptionListResponse(items=items, total=len(items))


@router.post(
    "/v1/deliveries/{delivery_id}/return-to-warehouse-receipts",
    response_model=ReturnToWarehouseResponse,
    status_code=201,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def receive_return_to_warehouse(
    delivery_id: UUID,
    command: ReturnToWarehouseCommand,
    request: Request,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_return_to_warehouse_receiver)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ReturnToWarehouseResponse:
    if not idempotency_key:
        raise AppError(400, "idempotency_key_required", "Idempotency-Key is required.")
    request_hash = _request_hash("return-to-warehouse", delivery_id, actor.subject, command)
    await session.rollback()
    async with session.begin():
        await _lock(session, f"delivery:{delivery_id}")
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.status_code = 200
            return ReturnToWarehouseResponse.model_validate(replay)
        existing_receipt = await session.scalar(
            select(return_to_warehouse_receipts.c.receipt_id).where(
                return_to_warehouse_receipts.c.receipt_id == command.return_receipt_id
            )
        )
        if existing_receipt is not None:
            raise AppError(
                409,
                "return_receipt_conflict",
                "The Return-to-Warehouse Receipt identity is already in use.",
            )
        delivery = await _delivery_row(session, delivery_id, for_update=True)
        if delivery is None:
            raise AppError(404, "delivery_not_found", "The Delivery does not exist.")
        if delivery["branch_id"] not in actor.branch_ids:
            raise AppError(403, "operational_scope_required", "Branch scope is required.")
        if delivery["warehouse_id"] not in actor.warehouse_ids:
            raise AppError(403, "operational_scope_required", "Warehouse scope is required.")
        if delivery["delivery_version"] != command.expected_delivery_version:
            raise AppError(409, "delivery_version_conflict", "The Delivery changed; refresh.")
        await _verified_evidence(session, delivery_id, command.evidence_ids)
        quarantine_id = await ensure_custody_location(
            session,
            warehouse_id=delivery["warehouse_id"],
            custody="quarantine",
            actor_subject=actor.subject,
        )
        transit_id = cast(
            UUID,
            await session.scalar(
                select(warehouse_stock_locations.c.location_id).where(
                    warehouse_stock_locations.c.warehouse_id == delivery["warehouse_id"],
                    warehouse_stock_locations.c.custody == "in_transit",
                )
            ),
        )
        await session.execute(
            insert(return_to_warehouse_receipts).values(
                receipt_id=command.return_receipt_id,
                delivery_id=delivery_id,
                warehouse_id=delivery["warehouse_id"],
                received_by=actor.subject,
                received_at=command.received_at,
                notes=command.reason,
                correlation_id=request.state.correlation_id,
                idempotency_key=idempotency_key,
            )
        )
        result_lines: list[ReturnLineResponse] = []
        for line_command in command.lines:
            for kind, quantity in (
                ("refused", line_command.refused_quantity_base),
                ("damaged", line_command.damaged_quantity_base),
            ):
                if quantity == ZERO:
                    continue
                case_id = await session.scalar(
                    select(delivery_exception_cases.c.exception_case_id)
                    .select_from(delivery_exception_cases)
                    .join(
                        delivery_confirmation_lines,
                        delivery_exception_cases.c.confirmation_line_id
                        == delivery_confirmation_lines.c.confirmation_line_id,
                    )
                    .join(
                        delivery_confirmations,
                        delivery_confirmation_lines.c.confirmation_id
                        == delivery_confirmations.c.confirmation_id,
                    )
                    .where(
                        delivery_confirmations.c.delivery_id == delivery_id,
                        delivery_confirmation_lines.c.delivery_line_id
                        == line_command.delivery_line_id,
                        delivery_exception_cases.c.exception_kind == kind,
                    )
                )
                typed_case_id = cast(UUID, case_id)
                case_row = await _case_row(session, typed_case_id, for_update=True)
                if case_row is None or quantity > case_row["open_quantity_base"]:
                    raise AppError(
                        409,
                        "return_quantity_conflict",
                        "Return quantity exceeds open In Transit custody.",
                    )
                tracked = await session.scalar(
                    select(func.count()).where(
                        delivery_line_identity_allocations.c.delivery_line_id
                        == line_command.delivery_line_id
                    )
                )
                if tracked and quantity != case_row["open_quantity_base"]:
                    raise AppError(
                        409,
                        "tracked_return_partition_required",
                        "Tracked Delivery Exception returns must receive the full open case.",
                    )
                out_id, in_id, group_id = await _movement_pair(
                    session,
                    case_row=case_row,
                    quantity=quantity,
                    movement_type="return_to_warehouse",
                    out_leg="return_transit_out",
                    in_leg="return_quarantine_in",
                    source_location_id=transit_id,
                    destination_location_id=quarantine_id,
                    source_reference=f"RETURN-TO-WAREHOUSE:{command.return_receipt_id}",
                    actor=actor,
                    correlation_id=request.state.correlation_id,
                    idempotency_key=f"{idempotency_key}:{case_id}",
                )
                receipt_line_id = uuid4()
                await session.execute(
                    insert(return_to_warehouse_receipt_lines).values(
                        receipt_line_id=receipt_line_id,
                        receipt_id=command.return_receipt_id,
                        exception_case_id=typed_case_id,
                        quantity_base=quantity,
                        transit_out_movement_id=out_id,
                        quarantine_in_movement_id=in_id,
                    )
                )
                event_id = uuid4()
                await session.execute(
                    insert(delivery_exception_events).values(
                        exception_event_id=event_id,
                        exception_case_id=typed_case_id,
                        event_type="return_received",
                        quantity_base=quantity,
                        source_document_type="return_to_warehouse_receipt",
                        source_document_id=command.return_receipt_id,
                        from_custody="in_transit",
                        to_custody="quarantine",
                        reason=command.reason,
                        movement_group_id=group_id,
                        actor_subject=actor.subject,
                        correlation_id=request.state.correlation_id,
                        idempotency_key=f"{idempotency_key}:{case_id}",
                    )
                )
                if command.evidence_ids:
                    await session.execute(
                        insert(delivery_exception_event_evidence),
                        [
                            {"exception_event_id": event_id, "evidence_id": evidence_id}
                            for evidence_id in command.evidence_ids
                        ],
                    )
                remaining = case_row["open_quantity_base"] - quantity
                await session.execute(
                    update(delivery_exception_state)
                    .where(delivery_exception_state.c.exception_case_id == case_id)
                    .values(
                        open_quantity_base=remaining,
                        returned_quantity_base=(
                            delivery_exception_state.c.returned_quantity_base + quantity
                        ),
                        custody="quarantine" if remaining == ZERO else "in_transit",
                        status="resolved" if remaining == ZERO else "partially_resolved",
                        version=delivery_exception_state.c.version + 1,
                        updated_at=func.now(),
                    )
                )
                result_lines.append(
                    ReturnLineResponse(
                        delivery_line_id=line_command.delivery_line_id,
                        exception_case_id=typed_case_id,
                        exception_kind=cast(Literal["refused", "damaged"], kind),
                        quantity_base=quantity,
                        custody="quarantine",
                    )
                )
        if not result_lines:
            raise AppError(409, "return_quantity_required", "Return quantity is required.")
        result = ReturnToWarehouseResponse(
            return_receipt_id=command.return_receipt_id,
            delivery_id=delivery_id,
            status="received",
            lines=result_lines,
        )
        await store_command_result(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result=result,
        )
        return result


@router.post(
    "/v1/delivery-investigations/{investigation_id}/resolutions",
    response_model=InvestigationResolutionResponse,
    status_code=201,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def resolve_delivery_investigation(
    investigation_id: UUID,
    command: InvestigationResolutionCommand,
    request: Request,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_investigation_resolver)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> InvestigationResolutionResponse:
    if not idempotency_key:
        raise AppError(400, "idempotency_key_required", "Idempotency-Key is required.")
    request_hash = _request_hash(
        "investigation-resolution", investigation_id, actor.subject, command
    )
    await session.rollback()
    async with session.begin():
        await _lock(session, f"delivery-exception:{investigation_id}")
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.status_code = 200
            return InvestigationResolutionResponse.model_validate(replay)
        case_row = await _case_row(session, investigation_id, for_update=True)
        if case_row is None or case_row["exception_kind"] != "short_missing":
            raise AppError(404, "delivery_investigation_not_found", "Investigation not found.")
        if case_row["branch_id"] not in actor.branch_ids:
            raise AppError(403, "operational_scope_required", "Branch scope is required.")
        if case_row["warehouse_id"] not in actor.warehouse_ids:
            raise AppError(403, "operational_scope_required", "Warehouse scope is required.")
        if case_row["version"] != command.expected_investigation_version:
            raise AppError(409, "investigation_version_conflict", "Investigation changed; refresh.")
        quantity = cast(Decimal, case_row["open_quantity_base"])
        if quantity <= ZERO:
            raise AppError(409, "investigation_resolved", "Investigation is already resolved.")
        await _verified_evidence(session, case_row["delivery_id"], command.evidence_ids)
        authority_id: UUID | None = None
        if command.resolution_type != "recovery":
            authority = (
                (
                    await session.execute(
                        select(approval_authorities).where(
                            approval_authorities.c.user_subject == actor.subject,
                            approval_authorities.c.capability_code
                            == "inventory:investigation-resolve",
                            approval_authorities.c.branch_id == case_row["branch_id"],
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            value = quantity * case_row["unit_cost"]
            if authority is None or (
                authority["maximum_amount"] is not None and authority["maximum_amount"] < value
            ):
                raise AppError(
                    403,
                    "approval_authority_required",
                    "Inventory loss resolution requires sufficient Approval Authority.",
                )
            if authority["maximum_percentage"] is not None and authority[
                "maximum_percentage"
            ] < Decimal("100"):
                raise AppError(
                    403,
                    "approval_authority_required",
                    "Inventory loss resolution requires authority for 100 percent "
                    "of the open case.",
                )
            if authority["maker_checker_required"] and case_row["opened_by"] == actor.subject:
                raise AppError(
                    409,
                    "maker_checker_violation",
                    "The user who opened the investigation cannot approve its loss resolution.",
                )
            authority_id = authority["approval_authority_id"]
        investigation_location_id = cast(
            UUID,
            await session.scalar(
                select(warehouse_stock_locations.c.location_id).where(
                    warehouse_stock_locations.c.warehouse_id == case_row["warehouse_id"],
                    warehouse_stock_locations.c.custody == "investigation",
                )
            ),
        )
        quarantine_id = (
            await ensure_custody_location(
                session,
                warehouse_id=case_row["warehouse_id"],
                custody="quarantine",
                actor_subject=actor.subject,
            )
            if command.resolution_type == "recovery"
            else None
        )
        out_leg = {
            "recovery": "recovery_investigation_out",
            "carrier_claim": "carrier_claim_investigation_out",
            "inventory_adjustment": "inventory_adjustment_investigation_out",
        }[command.resolution_type]
        out_id, in_id, group_id = await _movement_pair(
            session,
            case_row=case_row,
            quantity=quantity,
            movement_type="investigation_resolution",
            out_leg=out_leg,
            in_leg="recovery_quarantine_in" if command.resolution_type == "recovery" else None,
            source_location_id=investigation_location_id,
            destination_location_id=quarantine_id,
            source_reference=f"INVESTIGATION-RESOLUTION:{command.resolution_id}",
            actor=actor,
            correlation_id=request.state.correlation_id,
            idempotency_key=idempotency_key,
        )
        if command.resolution_type != "recovery":
            value = (quantity * case_row["unit_cost"]).quantize(SIX_PLACES, ROUND_HALF_UP)
            valuation = (
                (
                    await session.execute(
                        select(inventory_valuation)
                        .where(
                            inventory_valuation.c.sku_id == case_row["sku_id"],
                            inventory_valuation.c.warehouse_id == case_row["warehouse_id"],
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one()
            )
            if valuation["quantity_on_hand"] < quantity or valuation["inventory_value"] < value:
                raise AppError(
                    409,
                    "delivery_inventory_conflict",
                    "Inventory valuation changed; refresh before resolving the loss.",
                )
            remaining_quantity = cast(Decimal, valuation["quantity_on_hand"]) - quantity
            remaining_value = cast(Decimal, valuation["inventory_value"]) - value
            remaining_average = (
                (remaining_value / remaining_quantity).quantize(SIX_PLACES, ROUND_HALF_UP)
                if remaining_quantity != ZERO
                else valuation["moving_average_unit_cost"]
            )
            await session.execute(
                update(inventory_valuation)
                .where(
                    inventory_valuation.c.sku_id == case_row["sku_id"],
                    inventory_valuation.c.warehouse_id == case_row["warehouse_id"],
                )
                .values(
                    quantity_on_hand=remaining_quantity,
                    inventory_value=remaining_value,
                    moving_average_unit_cost=remaining_average,
                )
            )
        await session.execute(
            insert(investigation_resolutions).values(
                resolution_id=command.resolution_id,
                exception_case_id=investigation_id,
                resolution_type=command.resolution_type,
                quantity_base=quantity,
                reason=command.reason,
                external_reference=command.external_reference,
                approved_by=actor.subject,
                approval_authority_id=authority_id,
                movement_group_id=group_id,
                investigation_out_movement_id=out_id,
                quarantine_in_movement_id=in_id,
                correlation_id=request.state.correlation_id,
                idempotency_key=idempotency_key,
            )
        )
        event_type = {
            "recovery": "recovered",
            "carrier_claim": "carrier_claim_resolved",
            "inventory_adjustment": "inventory_adjustment_resolved",
        }[command.resolution_type]
        custody = "quarantine" if command.resolution_type == "recovery" else "outbound"
        event_id = uuid4()
        await session.execute(
            insert(delivery_exception_events).values(
                exception_event_id=event_id,
                exception_case_id=investigation_id,
                event_type=event_type,
                quantity_base=quantity,
                source_document_type="investigation_resolution",
                source_document_id=command.resolution_id,
                from_custody="investigation",
                to_custody=custody,
                reason=command.reason,
                approved_by=actor.subject,
                approval_authority_id=authority_id,
                movement_group_id=group_id,
                actor_subject=actor.subject,
                correlation_id=request.state.correlation_id,
                idempotency_key=idempotency_key,
            )
        )
        if command.evidence_ids:
            await session.execute(
                insert(delivery_exception_event_evidence),
                [
                    {"exception_event_id": event_id, "evidence_id": evidence_id}
                    for evidence_id in command.evidence_ids
                ],
            )
        await session.execute(
            update(delivery_exception_state)
            .where(delivery_exception_state.c.exception_case_id == investigation_id)
            .values(
                open_quantity_base=ZERO,
                resolved_quantity_base=(
                    delivery_exception_state.c.resolved_quantity_base + quantity
                ),
                custody=custody,
                status="resolved",
                version=delivery_exception_state.c.version + 1,
                updated_at=func.now(),
            )
        )
        result = InvestigationResolutionResponse(
            resolution_id=command.resolution_id,
            investigation_id=investigation_id,
            resolution_type=command.resolution_type,
            quantity_base=quantity,
            status="resolved",
            custody=cast(Literal["quarantine", "outbound"], custody),
        )
        await store_command_result(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result=result,
        )
        return result


@router.post(
    "/v1/deliveries/{delivery_id}/retries",
    response_model=AssignedDeliveryResponse,
    status_code=201,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def create_retry_delivery(
    delivery_id: UUID,
    command: RetryDeliveryCommand,
    request: Request,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_delivery_retrier)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> AssignedDeliveryResponse:
    if not idempotency_key:
        raise AppError(400, "idempotency_key_required", "Idempotency-Key is required.")
    request_hash = _request_hash("retry-delivery", delivery_id, actor.subject, command)
    await session.rollback()
    async with session.begin():
        await _lock(session, f"delivery:{delivery_id}")
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.status_code = 200
            return AssignedDeliveryResponse.model_validate(replay)
        source = await _delivery_row(session, delivery_id, for_update=True)
        if source is None:
            raise AppError(404, "delivery_not_found", "The Delivery does not exist.")
        if source["branch_id"] not in actor.branch_ids:
            raise AppError(403, "operational_scope_required", "Branch scope is required.")
        if source["warehouse_id"] not in actor.warehouse_ids:
            raise AppError(403, "operational_scope_required", "Warehouse scope is required.")
        if source["delivery_version"] != command.expected_delivery_version:
            raise AppError(409, "delivery_version_conflict", "The Delivery changed; refresh.")
        if not await _assignee_is_authorized(
            session,
            subject=command.assigned_to,
            branch_id=source["branch_id"],
        ):
            raise AppError(
                422,
                "delivery_assignee_not_authorized",
                "Assigned Delivery Staff must be active, capable, and scoped to the Branch.",
            )
        cases = list(
            (
                await session.execute(
                    select(
                        delivery_exception_cases,
                        delivery_exception_state.c.open_quantity_base,
                        delivery_exception_state.c.version,
                        delivery_confirmation_lines.c.delivery_line_id.label(
                            "source_delivery_line_id"
                        ),
                        delivery_lines,
                    )
                    .join(delivery_exception_state)
                    .join(delivery_confirmation_lines)
                    .join(delivery_confirmations)
                    .join(
                        delivery_lines,
                        delivery_confirmation_lines.c.delivery_line_id
                        == delivery_lines.c.delivery_line_id,
                    )
                    .where(
                        delivery_confirmations.c.delivery_id == delivery_id,
                        delivery_exception_cases.c.exception_kind == "still_undelivered",
                        delivery_exception_state.c.open_quantity_base > ZERO,
                    )
                    .with_for_update(of=delivery_exception_state)
                )
            ).mappings()
        )
        if not cases:
            raise AppError(
                409,
                "retry_quantity_unavailable",
                "No still-undelivered custody remains.",
            )
        await session.execute(
            insert(delivery_dispatches).values(
                delivery_id=command.retry_delivery_id,
                fulfillment_order_id=source["fulfillment_order_id"],
                sales_order_id=source["sales_order_id"],
                sales_order_revision_id=source["sales_order_revision_id"],
                customer_id=source["customer_id"],
                branch_id=source["branch_id"],
                warehouse_id=source["warehouse_id"],
                delivery_address_version_id=source["delivery_address_version_id"],
                delivery_address_snapshot=source["delivery_address_snapshot"],
                recipient_name_snapshot=source["recipient_name_snapshot"],
                payment_timing_policy=source["payment_timing_policy"],
                evidence_requirements=source["evidence_requirements"],
                initial_assignee_subject=command.assigned_to,
                dispatched_by=actor.subject,
                correlation_id=request.state.correlation_id,
                idempotency_key=idempotency_key,
                dispatch_kind="retry",
                parent_delivery_id=delivery_id,
            )
        )
        await session.execute(
            insert(delivery_state).values(
                delivery_id=command.retry_delivery_id,
                status="dispatched",
                assigned_to=command.assigned_to,
                version=1,
            )
        )
        for case in cases:
            retry_line_id = uuid4()
            quantity = cast(Decimal, case["open_quantity_base"])
            await session.execute(
                insert(delivery_lines).values(
                    delivery_line_id=retry_line_id,
                    delivery_id=command.retry_delivery_id,
                    pick_line_id=case["pick_line_id"],
                    line_id=case["line_id"],
                    sku_id=case["sku_id"],
                    quantity_base=quantity,
                    movement_group_id=uuid4(),
                    staging_movement_id=None,
                    transit_movement_id=None,
                    source_exception_case_id=case["exception_case_id"],
                )
            )
            await session.execute(
                insert(delivery_retry_allocations).values(
                    retry_allocation_id=uuid4(),
                    source_exception_case_id=case["exception_case_id"],
                    retry_delivery_line_id=retry_line_id,
                    quantity_base=quantity,
                    allocated_by=actor.subject,
                    reason=command.reason,
                    correlation_id=request.state.correlation_id,
                    idempotency_key=f"{idempotency_key}:{case['exception_case_id']}",
                )
            )
            source_allocations = list(
                (
                    await session.execute(
                        select(
                            delivery_line_identity_allocations,
                            delivery_confirmation_identity_partitions.c[
                                "still_undelivered_quantity_base"
                            ].label("retry_quantity_base"),
                        )
                        .join(
                            delivery_confirmation_identity_partitions,
                            delivery_line_identity_allocations.c.allocation_id
                            == delivery_confirmation_identity_partitions.c[
                                "delivery_line_identity_allocation_id"
                            ],
                        )
                        .where(
                            delivery_line_identity_allocations.c.delivery_line_id
                            == case["source_delivery_line_id"],
                            delivery_confirmation_identity_partitions.c["confirmation_line_id"]
                            == case["confirmation_line_id"],
                            delivery_confirmation_identity_partitions.c[
                                "still_undelivered_quantity_base"
                            ]
                            > ZERO,
                        )
                    )
                ).mappings()
            )
            if source_allocations:
                await session.execute(
                    insert(delivery_line_identity_allocations),
                    [
                        {
                            "allocation_id": uuid4(),
                            "delivery_line_id": retry_line_id,
                            "pick_identity_assignment_id": item["pick_identity_assignment_id"],
                            "quantity_base": item["retry_quantity_base"],
                        }
                        for item in source_allocations
                    ],
                )
            await session.execute(
                insert(delivery_exception_events).values(
                    exception_event_id=uuid4(),
                    exception_case_id=case["exception_case_id"],
                    event_type="retry_allocated",
                    quantity_base=quantity,
                    source_document_type="retry_delivery",
                    source_document_id=command.retry_delivery_id,
                    from_custody="in_transit",
                    to_custody="in_transit",
                    reason=command.reason,
                    actor_subject=actor.subject,
                    correlation_id=request.state.correlation_id,
                    idempotency_key=f"{idempotency_key}:{case['exception_case_id']}",
                )
            )
            await session.execute(
                update(delivery_exception_state)
                .where(delivery_exception_state.c.exception_case_id == case["exception_case_id"])
                .values(
                    open_quantity_base=ZERO,
                    retry_allocated_quantity_base=(
                        delivery_exception_state.c.retry_allocated_quantity_base + quantity
                    ),
                    status="resolved",
                    version=delivery_exception_state.c.version + 1,
                    updated_at=func.now(),
                )
            )
        retry = await _delivery_row(session, command.retry_delivery_id)
        result = await _assigned_delivery_response(session, cast(Mapping[str, Any], retry))
        await store_command_result(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result=result,
        )
        return result
