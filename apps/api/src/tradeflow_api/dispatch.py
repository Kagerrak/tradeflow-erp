from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from decimal import Decimal
from hashlib import sha256
from typing import Annotated, Any, Literal, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import exists, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tradeflow_api.auth import AuthorizedUser, require_delivery_reader, require_dispatcher
from tradeflow_api.cod_settlement import calculate_cod_amount_due
from tradeflow_api.command_receipts import get_command_replay, store_command_result
from tradeflow_api.database import get_database_session
from tradeflow_api.errors import AppError, error_responses
from tradeflow_api.models import (
    customer_accounts,
    delivery_assignment_events,
    delivery_dispatches,
    delivery_line_identity_allocations,
    delivery_lines,
    delivery_state,
    fulfillment_order_state,
    fulfillment_orders,
    inventory_availability,
    lot_identities,
    pick_identity_assignments,
    pick_lines,
    pick_postings,
    role_template_capabilities,
    role_templates,
    sales_order_line_revisions,
    sales_order_revisions,
    skus,
    stock_lot_allocations,
    stock_movements,
    stock_serial_allocations,
    user_branch_scopes,
    user_role_templates,
    users,
    warehouse_stock_locations,
)

router = APIRouter(tags=["fulfillment dispatch"])
ZERO = Decimal("0")


class CommandModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DispatchCommand(CommandModel):
    delivery_id: UUID
    expected_fulfillment_version: int = Field(gt=0)
    assigned_to: str = Field(min_length=1, max_length=200)
    pick_ids: list[UUID] = Field(min_length=1)


class LotSelectionResponse(BaseModel):
    lot_code: str
    expiration_date: str
    quantity_base: Decimal


class DispatchLineResponse(BaseModel):
    line_id: UUID
    sku_id: UUID
    quantity_base: Decimal
    lot_selections: list[LotSelectionResponse]
    serial_numbers: list[str]
    staging_movement_ids: list[UUID]
    transit_movement_ids: list[UUID]


class DispatchResponse(BaseModel):
    delivery_id: UUID
    fulfillment_order_id: UUID
    status: Literal["dispatched"]
    assigned_to: str
    payment_timing_policy: Literal["prepaid", "cash_on_delivery", "on_account"]
    version: int
    lines: list[DispatchLineResponse]


class DeliveryIdentityPositionResponse(BaseModel):
    delivery_line_identity_allocation_id: UUID
    tracking_policy: Literal["lot", "serial"]
    quantity_base: Decimal
    lot_code: str | None
    serial_number: str | None


class AssignedDeliveryLineResponse(BaseModel):
    delivery_line_id: UUID
    line_id: UUID
    sku_id: UUID
    sku_code: str
    sku_name: str
    quantity_base: Decimal
    lot_selections: list[LotSelectionResponse]
    serial_numbers: list[str]
    identity_positions: list[DeliveryIdentityPositionResponse]


class AssignedDeliveryResponse(BaseModel):
    delivery_id: UUID
    fulfillment_order_id: UUID
    status: Literal["dispatched", "confirmed"]
    version: int
    assigned_to: str
    recipient_name: str
    delivery_address: dict[str, object]
    payment_timing_policy: Literal["prepaid", "cash_on_delivery", "on_account"]
    collection_required: bool
    collection_amount_due: Decimal | None
    evidence_requirements: list[str]
    lines: list[AssignedDeliveryLineResponse]


class AssignedDeliveryListResponse(BaseModel):
    items: list[AssignedDeliveryResponse]
    total: int


class AssignDeliveryCommand(CommandModel):
    expected_delivery_version: int = Field(gt=0)
    assigned_to: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=500)


class DeliveryAssignmentResponse(BaseModel):
    delivery_id: UUID
    status: Literal["dispatched"]
    assigned_to: str
    version: int


def _request_hash(
    command: DispatchCommand,
    *,
    fulfillment_order_id: UUID,
    actor_subject: str,
) -> str:
    payload = (
        f"dispatch:{fulfillment_order_id}:{actor_subject}:"
        f"{command.model_dump_json(exclude_none=False)}"
    )
    return sha256(payload.encode()).hexdigest()


async def _lock_key(session: AsyncSession, key: str) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": key},
    )


async def _ensure_transit_location(
    session: AsyncSession,
    *,
    warehouse_id: UUID,
    actor_subject: str,
) -> UUID:
    location_id = await session.scalar(
        select(warehouse_stock_locations.c.location_id).where(
            warehouse_stock_locations.c.warehouse_id == warehouse_id,
            warehouse_stock_locations.c.custody == "in_transit",
            warehouse_stock_locations.c.is_active.is_(True),
        )
    )
    if location_id is not None:
        return cast(UUID, location_id)
    location_id = uuid4()
    await session.execute(
        insert(warehouse_stock_locations).values(
            location_id=location_id,
            warehouse_id=warehouse_id,
            code="IN-TRANSIT",
            name="In Transit",
            custody="in_transit",
            is_active=True,
            created_by=actor_subject,
        )
    )
    return location_id


async def _assignee_is_authorized(
    session: AsyncSession,
    *,
    subject: str,
    branch_id: UUID,
) -> bool:
    return bool(
        await session.scalar(
            select(
                exists().where(
                    users.c.subject == subject,
                    users.c.is_active.is_(True),
                    exists().where(
                        user_branch_scopes.c.user_subject == subject,
                        user_branch_scopes.c.branch_id == branch_id,
                    ),
                    exists().where(
                        user_role_templates.c.user_subject == subject,
                        user_role_templates.c.role_template_id == role_templates.c.role_template_id,
                        role_templates.c.is_active.is_(True),
                        role_template_capabilities.c.role_template_id
                        == role_templates.c.role_template_id,
                        role_template_capabilities.c.capability_code == "fulfillment:delivery-read",
                    ),
                )
            )
        )
    )


async def _pick_assignments(
    session: AsyncSession,
    pick_line_id: UUID,
) -> list[Mapping[str, Any]]:
    rows = (
        await session.execute(
            select(
                pick_identity_assignments,
                lot_identities.c.lot_code,
                lot_identities.c.expiration_date.label("lot_expiration_date"),
                stock_serial_allocations.c.serial_number,
                stock_serial_allocations.c.expiration_date.label("serial_expiration_date"),
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
            .where(pick_identity_assignments.c.pick_line_id == pick_line_id)
            .order_by(
                lot_identities.c.lot_code,
                stock_serial_allocations.c.serial_number,
            )
        )
    ).mappings()
    return [cast(Mapping[str, Any], row) for row in rows]


async def _move_position_to_transit(
    session: AsyncSession,
    *,
    pick_line: Mapping[str, Any],
    transit_location_id: UUID,
    assignments: list[Mapping[str, Any]],
) -> None:
    positions: list[tuple[str, Decimal, str | None, list[str], object]] = []
    lot_assignments = [row for row in assignments if row["tracking_policy"] == "lot"]
    serial_assignments = [row for row in assignments if row["tracking_policy"] == "serial"]
    if lot_assignments:
        positions = [
            (
                f"lot:{row['lot_code']}",
                cast(Decimal, row["quantity_base"]),
                cast(str, row["lot_code"]),
                [],
                row["lot_expiration_date"],
            )
            for row in lot_assignments
        ]
    elif serial_assignments:
        positions = [
            (
                f"serial:{row['serial_number']}",
                Decimal("1"),
                None,
                [cast(str, row["serial_number"])],
                row["serial_expiration_date"],
            )
            for row in serial_assignments
        ]
    else:
        positions = [("", cast(Decimal, pick_line["quantity_base"]), None, [], None)]

    for identity_key, quantity, lot_code, serial_numbers, expiration_date in positions:
        source = (
            (
                await session.execute(
                    select(inventory_availability)
                    .where(
                        inventory_availability.c.sku_id == pick_line["sku_id"],
                        inventory_availability.c.warehouse_id == pick_line["warehouse_id"],
                        inventory_availability.c.location_id == pick_line["staging_location_id"],
                        inventory_availability.c.identity_key == identity_key,
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if source is None or source["on_hand"] < quantity:
            raise AppError(
                409,
                "dispatch_staging_conflict",
                "Dispatch Staging quantity or tracked identity changed; refresh before dispatch.",
            )
        await session.execute(
            update(inventory_availability)
            .where(
                inventory_availability.c.sku_id == pick_line["sku_id"],
                inventory_availability.c.warehouse_id == pick_line["warehouse_id"],
                inventory_availability.c.location_id == pick_line["staging_location_id"],
                inventory_availability.c.identity_key == identity_key,
            )
            .values(
                on_hand=inventory_availability.c.on_hand - quantity,
                serial_numbers=[] if serial_numbers else source["serial_numbers"],
            )
        )
        await session.execute(
            pg_insert(inventory_availability)
            .values(
                sku_id=pick_line["sku_id"],
                warehouse_id=pick_line["warehouse_id"],
                location_id=transit_location_id,
                identity_key=identity_key,
                lot_code=lot_code,
                serial_numbers=serial_numbers,
                expiration_date=expiration_date,
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


def _validate_identity_assignments(
    pick_line: Mapping[str, Any],
    assignments: list[Mapping[str, Any]],
) -> None:
    policy = cast(str, pick_line["tracking_policy"])
    quantity = cast(Decimal, pick_line["quantity_base"])
    if policy == "untracked":
        complete = not assignments
    elif policy == "lot":
        complete = bool(assignments) and all(row["tracking_policy"] == "lot" for row in assignments)
        complete = (
            complete
            and sum((cast(Decimal, row["quantity_base"]) for row in assignments), ZERO) == quantity
        )
    else:
        complete = (
            quantity == quantity.to_integral_value()
            and Decimal(len(assignments)) == quantity
            and all(
                row["tracking_policy"] == "serial"
                and row["serial_number"] is not None
                and cast(Decimal, row["quantity_base"]) == Decimal("1")
                for row in assignments
            )
        )
    if not complete:
        raise AppError(
            409,
            "dispatch_identity_conflict",
            "Tracked identity assignments no longer cover the complete staged Pick quantity.",
        )


async def _assigned_delivery_response(
    session: AsyncSession,
    delivery: Mapping[str, Any],
) -> AssignedDeliveryResponse:
    line_rows = list(
        (
            await session.execute(
                select(
                    delivery_lines.c.delivery_line_id,
                    delivery_lines.c.line_id,
                    delivery_lines.c.sku_id,
                    delivery_lines.c.quantity_base,
                    delivery_lines.c.pick_line_id,
                    skus.c.code.label("sku_code"),
                    skus.c.name.label("sku_name"),
                    sales_order_line_revisions.c.quantity_base.label("source_quantity_base"),
                    sales_order_line_revisions.c.allocated_discount.label(
                        "source_allocated_discount"
                    ),
                    sales_order_line_revisions.c.tax_amount.label("source_tax_amount"),
                    sales_order_line_revisions.c.line_total.label("source_line_total"),
                    sales_order_line_revisions.c.calculation_snapshot.label(
                        "source_calculation_snapshot"
                    ),
                )
                .join(skus, delivery_lines.c.sku_id == skus.c.sku_id)
                .join(
                    sales_order_line_revisions,
                    (
                        sales_order_line_revisions.c.sales_order_revision_id
                        == delivery["sales_order_revision_id"]
                    )
                    & (sales_order_line_revisions.c.line_id == delivery_lines.c.line_id),
                )
                .where(delivery_lines.c.delivery_id == delivery["delivery_id"])
                .order_by(delivery_lines.c.line_id, delivery_lines.c.pick_line_id)
            )
        ).mappings()
    )
    aggregates: dict[tuple[UUID, UUID, UUID, str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "quantity_base": ZERO,
            "lot_selections": [],
            "serial_numbers": [],
            "identity_positions": [],
        }
    )
    for row in line_rows:
        key = (
            row["delivery_line_id"],
            row["line_id"],
            row["sku_id"],
            row["sku_code"],
            row["sku_name"],
        )
        aggregate = aggregates[key]
        aggregate["quantity_base"] += row["quantity_base"]
        assignments = list(
            (
                await session.execute(
                    select(
                        delivery_line_identity_allocations.c.allocation_id,
                        delivery_line_identity_allocations.c.quantity_base,
                        pick_identity_assignments.c.tracking_policy,
                        lot_identities.c.lot_code,
                        lot_identities.c.expiration_date.label("lot_expiration_date"),
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
                        delivery_line_identity_allocations.c.delivery_line_id
                        == row["delivery_line_id"]
                    )
                    .order_by(
                        lot_identities.c.lot_code,
                        stock_serial_allocations.c.serial_number,
                    )
                )
            ).mappings()
        )
        for assignment in assignments:
            if assignment["tracking_policy"] == "lot":
                aggregate["lot_selections"].append(
                    {
                        "lot_code": assignment["lot_code"],
                        "expiration_date": (
                            assignment["lot_expiration_date"].isoformat()
                            if assignment["lot_expiration_date"] is not None
                            else ""
                        ),
                        "quantity_base": str(assignment["quantity_base"]),
                    }
                )
            elif assignment["tracking_policy"] == "serial":
                aggregate["serial_numbers"].append(assignment["serial_number"])
            aggregate["identity_positions"].append(
                DeliveryIdentityPositionResponse(
                    delivery_line_identity_allocation_id=assignment["allocation_id"],
                    tracking_policy=assignment["tracking_policy"],
                    quantity_base=assignment["quantity_base"],
                    lot_code=assignment["lot_code"],
                    serial_number=assignment["serial_number"],
                )
            )
    collection_amount_due: Decimal | None = None
    if delivery["payment_timing_policy"] == "cash_on_delivery":
        currency = cast(
            str,
            await session.scalar(
                select(sales_order_revisions.c.currency).where(
                    sales_order_revisions.c.sales_order_revision_id
                    == delivery["sales_order_revision_id"]
                )
            ),
        )
        accepted: dict[UUID, Decimal] = defaultdict(lambda: ZERO)
        for (_, line_id, _, _, _), data in aggregates.items():
            accepted[line_id] += cast(Decimal, data["quantity_base"])
        collection_amount_due = await calculate_cod_amount_due(
            session,
            sales_order_revision_id=delivery["sales_order_revision_id"],
            lines=cast(list[Mapping[str, Any]], line_rows),
            accepted=accepted,
            currency=currency,
        )
    return AssignedDeliveryResponse(
        delivery_id=delivery["delivery_id"],
        fulfillment_order_id=delivery["fulfillment_order_id"],
        status=delivery["delivery_status"],
        version=delivery["delivery_version"],
        assigned_to=delivery["assigned_to"],
        recipient_name=delivery["recipient_name_snapshot"],
        delivery_address=dict(delivery["delivery_address_snapshot"]),
        payment_timing_policy=delivery["payment_timing_policy"],
        collection_required=delivery["payment_timing_policy"] == "cash_on_delivery",
        collection_amount_due=collection_amount_due,
        evidence_requirements=list(delivery["evidence_requirements"]),
        lines=[
            AssignedDeliveryLineResponse(
                delivery_line_id=delivery_line_id,
                line_id=line_id,
                sku_id=sku_id,
                sku_code=sku_code,
                sku_name=sku_name,
                quantity_base=data["quantity_base"],
                lot_selections=data["lot_selections"],
                serial_numbers=sorted(data["serial_numbers"]),
                identity_positions=data["identity_positions"],
            )
            for (delivery_line_id, line_id, sku_id, sku_code, sku_name), data in sorted(
                aggregates.items(), key=lambda item: str(item[0][0])
            )
        ],
    )


@router.get(
    "/v1/deliveries/assigned",
    response_model=AssignedDeliveryListResponse,
    responses=error_responses(401, 403, 500),
)
async def list_assigned_deliveries(
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_delivery_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> AssignedDeliveryListResponse:
    rows = list(
        (
            await session.execute(
                select(
                    delivery_dispatches,
                    delivery_state.c.status.label("delivery_status"),
                    delivery_state.c.assigned_to,
                    delivery_state.c.version.label("delivery_version"),
                )
                .join(
                    delivery_state,
                    delivery_dispatches.c.delivery_id == delivery_state.c.delivery_id,
                )
                .where(delivery_state.c.assigned_to == actor.subject)
                .order_by(delivery_dispatches.c.dispatched_at, delivery_dispatches.c.delivery_id)
            )
        ).mappings()
    )
    if any(row["branch_id"] not in actor.branch_ids for row in rows):
        raise AppError(
            403,
            "operational_scope_required",
            "Assigned Delivery is outside the user's current Operational Scope.",
        )
    items = [
        await _assigned_delivery_response(session, cast(Mapping[str, Any], row)) for row in rows
    ]
    etag_payload = ":".join(f"{item.delivery_id}:{item.version}" for item in items)
    response.headers["Cache-Control"] = "private, max-age=0, must-revalidate"
    response.headers["ETag"] = f'"{sha256(etag_payload.encode()).hexdigest()}"'
    return AssignedDeliveryListResponse(items=items, total=len(items))


async def _delivery_row(
    session: AsyncSession,
    delivery_id: UUID,
    *,
    for_update: bool = False,
) -> Mapping[str, Any] | None:
    statement = (
        select(
            delivery_dispatches,
            delivery_state.c.status.label("delivery_status"),
            delivery_state.c.assigned_to,
            delivery_state.c.version.label("delivery_version"),
        )
        .join(
            delivery_state,
            delivery_dispatches.c.delivery_id == delivery_state.c.delivery_id,
        )
        .where(delivery_dispatches.c.delivery_id == delivery_id)
    )
    if for_update:
        statement = statement.with_for_update()
    row = (await session.execute(statement)).mappings().one_or_none()
    return cast(Mapping[str, Any] | None, row)


@router.get(
    "/v1/deliveries/{delivery_id}",
    response_model=AssignedDeliveryResponse,
    responses=error_responses(401, 403, 404, 500),
)
async def get_assigned_delivery(
    delivery_id: UUID,
    actor: Annotated[AuthorizedUser, Depends(require_delivery_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> AssignedDeliveryResponse:
    delivery = await _delivery_row(session, delivery_id)
    if delivery is None:
        raise AppError(404, "delivery_not_found", "The Delivery does not exist.")
    if delivery["assigned_to"] != actor.subject:
        raise AppError(
            403,
            "delivery_assignment_required",
            "The Delivery is not assigned to this Delivery Staff user.",
        )
    if delivery["branch_id"] not in actor.branch_ids:
        raise AppError(
            403,
            "operational_scope_required",
            "Assigned Delivery is outside the user's current Operational Scope.",
        )
    return await _assigned_delivery_response(session, delivery)


@router.post(
    "/v1/deliveries/{delivery_id}/assignment",
    response_model=DeliveryAssignmentResponse,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def assign_delivery(
    delivery_id: UUID,
    command: AssignDeliveryCommand,
    request: Request,
    actor: Annotated[AuthorizedUser, Depends(require_dispatcher)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> DeliveryAssignmentResponse:
    if idempotency_key is None:
        raise AppError(400, "idempotency_key_required", "Idempotency-Key is required.")
    request_hash = sha256(
        (
            f"assign-delivery:{delivery_id}:{actor.subject}:"
            f"{command.model_dump_json(exclude_none=False)}"
        ).encode()
    ).hexdigest()
    await session.rollback()
    async with session.begin():
        await _lock_key(session, f"delivery:{delivery_id}")
        delivery = await _delivery_row(session, delivery_id, for_update=True)
        if delivery is None:
            raise AppError(404, "delivery_not_found", "The Delivery does not exist.")
        if (
            delivery["branch_id"] not in actor.branch_ids
            or delivery["warehouse_id"] not in actor.warehouse_ids
        ):
            raise AppError(
                403,
                "operational_scope_required",
                "Warehouse Operational Scope is required for Delivery assignment.",
            )
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            return DeliveryAssignmentResponse.model_validate(replay)
        if delivery["delivery_version"] != command.expected_delivery_version:
            raise AppError(
                409,
                "delivery_version_conflict",
                "The Delivery assignment changed; refresh before retrying.",
            )
        if delivery["assigned_to"] == command.assigned_to:
            raise AppError(
                409,
                "delivery_assignment_unchanged",
                "The Delivery is already assigned to that user.",
            )
        if not await _assignee_is_authorized(
            session,
            subject=command.assigned_to,
            branch_id=delivery["branch_id"],
        ):
            raise AppError(
                422,
                "delivery_assignee_not_authorized",
                "Assigned Delivery Staff must be active, capable, and scoped to the Branch.",
            )
        next_version = delivery["delivery_version"] + 1
        await session.execute(
            insert(delivery_assignment_events).values(
                delivery_assignment_event_id=uuid4(),
                delivery_id=delivery_id,
                previous_assignee_subject=delivery["assigned_to"],
                assigned_to=command.assigned_to,
                delivery_version=next_version,
                reason=command.reason,
                assigned_by=actor.subject,
                correlation_id=request.state.correlation_id,
                idempotency_key=idempotency_key,
            )
        )
        await session.execute(
            update(delivery_state)
            .where(delivery_state.c.delivery_id == delivery_id)
            .values(
                assigned_to=command.assigned_to,
                version=next_version,
                updated_at=func.now(),
            )
        )
        result = DeliveryAssignmentResponse(
            delivery_id=delivery_id,
            status=delivery["delivery_status"],
            assigned_to=command.assigned_to,
            version=next_version,
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
    "/v1/fulfillment/orders/{fulfillment_order_id}/dispatch",
    response_model=DispatchResponse,
    status_code=201,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def dispatch_fulfillment(
    fulfillment_order_id: UUID,
    command: DispatchCommand,
    request: Request,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_dispatcher)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> DispatchResponse:
    if idempotency_key is None:
        raise AppError(400, "idempotency_key_required", "Idempotency-Key is required.")
    if len(command.pick_ids) != len(set(command.pick_ids)):
        raise AppError(422, "duplicate_pick", "Each Pick may be dispatched once per command.")
    request_hash = _request_hash(
        command,
        fulfillment_order_id=fulfillment_order_id,
        actor_subject=actor.subject,
    )
    await session.rollback()
    async with session.begin():
        await _lock_key(session, f"fulfillment:{fulfillment_order_id}")
        order = (
            (
                await session.execute(
                    select(
                        fulfillment_orders,
                        fulfillment_order_state.c.status.label("fulfillment_status"),
                        fulfillment_order_state.c.reserved_quantity_base,
                        fulfillment_order_state.c.picked_quantity_base,
                        fulfillment_order_state.c.dispatched_quantity_base,
                        fulfillment_order_state.c.version.label("fulfillment_version"),
                        sales_order_revisions.c.delivery_address_version_id,
                        sales_order_revisions.c.delivery_address_snapshot,
                        customer_accounts.c.legal_name.label("recipient_name_snapshot"),
                    )
                    .join(
                        fulfillment_order_state,
                        fulfillment_orders.c.fulfillment_order_id
                        == fulfillment_order_state.c.fulfillment_order_id,
                    )
                    .join(
                        sales_order_revisions,
                        fulfillment_orders.c.sales_order_revision_id
                        == sales_order_revisions.c.sales_order_revision_id,
                    )
                    .join(
                        customer_accounts,
                        fulfillment_orders.c.customer_id == customer_accounts.c.customer_id,
                    )
                    .where(fulfillment_orders.c.fulfillment_order_id == fulfillment_order_id)
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if order is None:
            raise AppError(
                404, "fulfillment_order_not_found", "The Fulfillment Order does not exist."
            )
        warehouse_id = cast(UUID, order["warehouse_id"])
        branch_id = cast(UUID, order["branch_id"])
        if branch_id not in actor.branch_ids or warehouse_id not in actor.warehouse_ids:
            raise AppError(
                403,
                "operational_scope_required",
                "Warehouse Operational Scope is required for Dispatch.",
            )
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.status_code = 200
            return DispatchResponse.model_validate(replay)
        if order["fulfillment_version"] != command.expected_fulfillment_version:
            raise AppError(
                409,
                "fulfillment_version_conflict",
                "The Fulfillment Order changed; refresh before Dispatch.",
            )
        if order["fulfillment_status"] not in {
            "partially_picked",
            "picked",
            "partially_dispatched",
        }:
            raise AppError(
                409,
                "dispatch_not_ready",
                "The Fulfillment Order has no eligible staged Pick for Dispatch.",
            )
        if not await _assignee_is_authorized(
            session,
            subject=command.assigned_to,
            branch_id=branch_id,
        ):
            raise AppError(
                422,
                "delivery_assignee_not_authorized",
                "Assigned Delivery Staff must be active, capable, and scoped to the Branch.",
            )
        reversed_pick_exists = exists().where(
            pick_postings.c.reversal_of_pick_id.in_(command.pick_ids),
            pick_postings.c.event_type == "reversed",
        )
        picked_rows = list(
            (
                await session.execute(
                    select(pick_lines, pick_postings.c.pick_id, skus.c.tracking_policy)
                    .join(pick_postings, pick_lines.c.pick_id == pick_postings.c.pick_id)
                    .join(skus, pick_lines.c.sku_id == skus.c.sku_id)
                    .where(
                        pick_postings.c.pick_id.in_(command.pick_ids),
                        pick_postings.c.fulfillment_order_id == fulfillment_order_id,
                        pick_postings.c.event_type == "posted",
                        ~reversed_pick_exists,
                        ~exists().where(delivery_lines.c.pick_line_id == pick_lines.c.pick_line_id),
                    )
                    .order_by(pick_lines.c.line_id, pick_lines.c.pick_line_id)
                    .with_for_update()
                )
            ).mappings()
        )
        found_pick_ids = {row["pick_id"] for row in picked_rows}
        if found_pick_ids != set(command.pick_ids):
            raise AppError(
                409,
                "dispatch_pick_conflict",
                "A selected Pick is reversed, already dispatched, or belongs to other work.",
            )
        transit_location_id = await _ensure_transit_location(
            session,
            warehouse_id=warehouse_id,
            actor_subject=actor.subject,
        )
        await session.execute(
            insert(delivery_dispatches).values(
                delivery_id=command.delivery_id,
                fulfillment_order_id=fulfillment_order_id,
                sales_order_id=order["sales_order_id"],
                sales_order_revision_id=order["sales_order_revision_id"],
                customer_id=order["customer_id"],
                branch_id=branch_id,
                warehouse_id=warehouse_id,
                delivery_address_version_id=order["delivery_address_version_id"],
                delivery_address_snapshot=order["delivery_address_snapshot"],
                recipient_name_snapshot=order["recipient_name_snapshot"],
                payment_timing_policy=order["payment_timing_policy"],
                evidence_requirements=["recipient_name", "signature"],
                initial_assignee_subject=command.assigned_to,
                dispatched_by=actor.subject,
                correlation_id=request.state.correlation_id,
                idempotency_key=idempotency_key,
            )
        )
        await session.execute(
            insert(delivery_state).values(
                delivery_id=command.delivery_id,
                status="dispatched",
                assigned_to=command.assigned_to,
                version=1,
            )
        )
        response_lines: dict[tuple[UUID, UUID], dict[str, Any]] = defaultdict(
            lambda: {
                "quantity_base": ZERO,
                "lot_selections": [],
                "serial_numbers": [],
                "staging_movement_ids": [],
                "transit_movement_ids": [],
            }
        )
        total_quantity = ZERO
        for row in picked_rows:
            pick_line = cast(Mapping[str, Any], row)
            assignments = await _pick_assignments(session, pick_line["pick_line_id"])
            _validate_identity_assignments(pick_line, assignments)
            await _move_position_to_transit(
                session,
                pick_line=pick_line,
                transit_location_id=transit_location_id,
                assignments=assignments,
            )
            source_movement = (
                (
                    await session.execute(
                        select(stock_movements).where(
                            stock_movements.c.movement_id == pick_line["staging_movement_id"]
                        )
                    )
                )
                .mappings()
                .one()
            )
            movement_group_id = uuid4()
            staging_movement_id = uuid4()
            transit_movement_id = uuid4()
            movement_key = f"{idempotency_key}:{pick_line['pick_line_id']}"
            common = {
                "sku_id": pick_line["sku_id"],
                "warehouse_id": warehouse_id,
                "quantity_base": pick_line["quantity_base"],
                "unit_cost": source_movement["unit_cost"],
                "base_currency": source_movement["base_currency"],
                "source_reference": f"DELIVERY:{command.delivery_id}",
                "entered_unit": pick_line["entered_unit"],
                "conversion_snapshot": pick_line["conversion_snapshot"],
                "actor_subject": actor.subject,
                "correlation_id": request.state.correlation_id,
                "movement_type": "dispatch",
                "movement_group_id": movement_group_id,
                "reversal_of_movement_id": None,
            }
            value = cast(Decimal, source_movement["value_delta"])
            await session.execute(
                insert(stock_movements),
                [
                    {
                        **common,
                        "movement_id": staging_movement_id,
                        "location_id": pick_line["staging_location_id"],
                        "value_delta": -value,
                        "idempotency_key": f"{movement_key}:staging-out",
                        "movement_leg": "dispatch_staging_out",
                    },
                    {
                        **common,
                        "movement_id": transit_movement_id,
                        "location_id": transit_location_id,
                        "value_delta": value,
                        "idempotency_key": f"{movement_key}:transit-in",
                        "movement_leg": "dispatch_transit_in",
                    },
                ],
            )
            for assignment in assignments:
                if assignment["tracking_policy"] == "lot":
                    await session.execute(
                        insert(stock_lot_allocations),
                        [
                            {
                                "lot_allocation_id": uuid4(),
                                "movement_id": staging_movement_id,
                                "lot_identity_id": assignment["lot_identity_id"],
                                "quantity_base": assignment["quantity_base"],
                            },
                            {
                                "lot_allocation_id": uuid4(),
                                "movement_id": transit_movement_id,
                                "lot_identity_id": assignment["lot_identity_id"],
                                "quantity_base": assignment["quantity_base"],
                            },
                        ],
                    )
            delivery_line_id = uuid4()
            await session.execute(
                insert(delivery_lines).values(
                    delivery_line_id=delivery_line_id,
                    delivery_id=command.delivery_id,
                    pick_line_id=pick_line["pick_line_id"],
                    line_id=pick_line["line_id"],
                    sku_id=pick_line["sku_id"],
                    quantity_base=pick_line["quantity_base"],
                    movement_group_id=movement_group_id,
                    staging_movement_id=staging_movement_id,
                    transit_movement_id=transit_movement_id,
                )
            )
            if assignments:
                await session.execute(
                    insert(delivery_line_identity_allocations),
                    [
                        {
                            "allocation_id": uuid4(),
                            "delivery_line_id": delivery_line_id,
                            "pick_identity_assignment_id": assignment[
                                "pick_identity_assignment_id"
                            ],
                            "quantity_base": assignment["quantity_base"],
                        }
                        for assignment in assignments
                    ],
                )
            key = (pick_line["line_id"], pick_line["sku_id"])
            aggregate = response_lines[key]
            aggregate["quantity_base"] += pick_line["quantity_base"]
            aggregate["staging_movement_ids"].append(staging_movement_id)
            aggregate["transit_movement_ids"].append(transit_movement_id)
            for assignment in assignments:
                if assignment["tracking_policy"] == "lot":
                    aggregate["lot_selections"].append(
                        {
                            "lot_code": assignment["lot_code"],
                            "expiration_date": (
                                assignment["lot_expiration_date"].isoformat()
                                if assignment["lot_expiration_date"] is not None
                                else ""
                            ),
                            "quantity_base": str(assignment["quantity_base"]),
                        }
                    )
                elif assignment["tracking_policy"] == "serial":
                    aggregate["serial_numbers"].append(assignment["serial_number"])
            total_quantity += pick_line["quantity_base"]
        next_dispatched = order["dispatched_quantity_base"] + total_quantity
        next_status = (
            "dispatched"
            if next_dispatched == order["picked_quantity_base"]
            and order["picked_quantity_base"] == order["reserved_quantity_base"]
            else "partially_dispatched"
        )
        await session.execute(
            update(fulfillment_order_state)
            .where(fulfillment_order_state.c.fulfillment_order_id == fulfillment_order_id)
            .values(
                status=next_status,
                dispatched_quantity_base=next_dispatched,
                version=order["fulfillment_version"] + 1,
                updated_at=func.now(),
            )
        )
        result = DispatchResponse(
            delivery_id=command.delivery_id,
            fulfillment_order_id=fulfillment_order_id,
            status="dispatched",
            assigned_to=command.assigned_to,
            payment_timing_policy=order["payment_timing_policy"],
            version=1,
            lines=[
                DispatchLineResponse(
                    line_id=line_id,
                    sku_id=sku_id,
                    quantity_base=data["quantity_base"],
                    lot_selections=data["lot_selections"],
                    serial_numbers=sorted(data["serial_numbers"]),
                    staging_movement_ids=data["staging_movement_ids"],
                    transit_movement_ids=data["transit_movement_ids"],
                )
                for (line_id, sku_id), data in sorted(
                    response_lines.items(), key=lambda item: str(item[0][0])
                )
            ],
        )
        await store_command_result(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result=result,
        )
        return result
