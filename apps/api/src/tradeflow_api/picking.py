from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256
from typing import Annotated, Any, Literal, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, insert, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tradeflow_api.auth import (
    AuthorizedUser,
    require_capability,
    require_inventory_reader,
    require_pick_reader,
    require_pick_reverser,
    require_picker,
)
from tradeflow_api.command_receipts import get_command_replay, store_command_result
from tradeflow_api.database import get_database_session
from tradeflow_api.errors import AppError, error_responses
from tradeflow_api.models import (
    barcode_mappings,
    companies,
    fulfillment_line_pick_state,
    fulfillment_order_lines,
    fulfillment_order_state,
    fulfillment_orders,
    inventory_availability,
    inventory_reservation_events,
    inventory_reserved_by_sku_warehouse,
    inventory_valuation,
    lot_identities,
    pick_identity_assignments,
    pick_lines,
    pick_postings,
    pick_releases,
    sales_order_line_commitments,
    sales_order_line_revisions,
    skus,
    stock_lot_allocations,
    stock_movements,
    stock_serial_allocations,
    unit_conversions,
    warehouse_stock_locations,
    warehouses,
)

router = APIRouter(tags=["fulfillment picking"])
SIX_PLACES = Decimal("0.000001")
ZERO = Decimal("0")


class CommandModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PickSelectionInput(CommandModel):
    lot_code: str | None = Field(default=None, min_length=1, max_length=100)
    serial_number: str | None = Field(default=None, min_length=1, max_length=100)
    quantity: Decimal | None = Field(
        default=None,
        gt=0,
        max_digits=18,
        decimal_places=6,
    )
    barcode: str | None = Field(default=None, min_length=1, max_length=100)
    manual_reason: str | None = Field(default=None, min_length=1, max_length=500)
    fefo_override_reason: str | None = Field(default=None, min_length=1, max_length=500)


class PickLineInput(CommandModel):
    line_id: UUID
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=6)
    unit_code: str = Field(pattern=r"^[A-Z][A-Z0-9_-]{0,29}$")
    selections: list[PickSelectionInput]


class PostPickCommand(CommandModel):
    pick_id: UUID
    expected_fulfillment_version: int = Field(gt=0)
    lines: list[PickLineInput] = Field(min_length=1)


class PickLineResponse(BaseModel):
    line_id: UUID
    sku_id: UUID
    quantity_base: Decimal
    conversion_snapshot: dict[str, str]
    source_movement_id: UUID
    staging_movement_id: UUID
    lot_selections: list[dict[str, str]]
    serial_selections: list[str]


class PickResponse(BaseModel):
    pick_id: UUID
    fulfillment_order_id: UUID
    status: Literal["partially_picked", "picked"]
    picked_quantity_base: Decimal
    remaining_quantity_base: Decimal
    version: int
    lines: list[PickLineResponse]


class BarcodeResolveCommand(CommandModel):
    warehouse_id: UUID
    barcode: str = Field(min_length=1, max_length=100)


class BarcodeResolutionResponse(BaseModel):
    barcode: str
    barcode_mapping_id: UUID | None
    mapping_type: Literal["catalog", "lot_identity", "serial_identity"]
    sku_id: UUID
    unit_code: str
    base_quantity_per_unit: Decimal
    lot_code: str | None
    serial_number: str | None
    expiration_date: date | None


class ReversePickCommand(CommandModel):
    reversal_pick_id: UUID
    expected_fulfillment_version: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=500)


class PickReversalResponse(BaseModel):
    reversal_pick_id: UUID
    original_pick_id: UUID
    fulfillment_order_id: UUID
    status: Literal["reversed"]
    reversed_quantity_base: Decimal
    version: int
    source_movement_ids: list[UUID]
    staging_movement_ids: list[UUID]


class FefoCandidateResponse(BaseModel):
    lot_code: str
    expiration_date: date
    available_quantity_base: Decimal
    recommended: bool


class PickingContextLineResponse(BaseModel):
    line_id: UUID
    sku_id: UUID
    sku_code: str
    sku_name: str
    base_stocking_unit: str
    tracking_policy: Literal["untracked", "lot", "serial"]
    expiration_control: bool
    released_quantity_base: Decimal
    picked_quantity_base: Decimal
    reversed_quantity_base: Decimal
    remaining_quantity_base: Decimal
    fefo_candidates: list[FefoCandidateResponse]


class PickingContextResponse(BaseModel):
    fulfillment_order_id: UUID
    version: int
    status: str
    warehouse_id: UUID
    lines: list[PickingContextLineResponse]


class PickHistoryLineResponse(BaseModel):
    line_id: UUID
    sku_id: UUID
    quantity_base: Decimal
    conversion_snapshot: dict[str, str]
    source_movement_id: UUID
    staging_movement_id: UUID
    lot_selections: list[dict[str, str]]
    serial_selections: list[str]


class PickHistoryItemResponse(BaseModel):
    pick_id: UUID
    event_type: Literal["posted", "reversed"]
    reversal_of_pick_id: UUID | None
    reason: str | None
    actor_subject: str
    correlation_id: str
    posted_at: datetime
    quantity_base: Decimal
    lines: list[PickHistoryLineResponse]


class PickHistoryListResponse(BaseModel):
    items: list[PickHistoryItemResponse]
    total: int


def _normalize_barcode(value: str) -> str:
    return "".join(value.upper().split())


def _request_hash(
    command: PostPickCommand,
    *,
    fulfillment_order_id: UUID,
    actor_subject: str,
) -> str:
    payload = (
        f"post-pick:{fulfillment_order_id}:{actor_subject}:"
        f"{command.model_dump_json(exclude_none=False)}"
    )
    return sha256(payload.encode()).hexdigest()


async def _lock_key(session: AsyncSession, key: str) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": key},
    )


async def _ensure_staging_location(
    session: AsyncSession,
    *,
    warehouse_id: UUID,
    actor_subject: str,
) -> UUID:
    location_id = await session.scalar(
        select(warehouse_stock_locations.c.location_id).where(
            warehouse_stock_locations.c.warehouse_id == warehouse_id,
            warehouse_stock_locations.c.custody == "dispatch_staging",
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
            code="DISPATCH-STAGING",
            name="Dispatch Staging",
            custody="dispatch_staging",
            is_active=True,
            created_by=actor_subject,
        )
    )
    return location_id


def _base_quantity(
    line: Mapping[str, Any],
    command: PickLineInput,
) -> tuple[Decimal, dict[str, str]]:
    snapshot = cast(dict[str, str], line["conversion_snapshot"])
    entered_unit = cast(str, line["entered_unit"])
    if command.unit_code != entered_unit:
        raise AppError(
            422,
            "pick_unit_not_approved",
            "Pick quantity must use the Sales Order line's approved Unit of Measure.",
        )
    factor = Decimal(snapshot["base_quantity_per_unit"])
    quantity_base = (command.quantity * factor).quantize(SIX_PLACES, ROUND_HALF_UP)
    return quantity_base, {
        "entered_quantity": str(command.quantity),
        "entered_unit": command.unit_code,
        "base_quantity_per_unit": str(factor),
        "base_quantity": str(quantity_base),
        "unit_conversion_id": snapshot.get("unit_conversion_id", ""),
    }


async def _available_positions(
    session: AsyncSession,
    *,
    sku_id: UUID,
    warehouse_id: UUID,
) -> list[Mapping[str, Any]]:
    positions = (
        (
            await session.execute(
                select(inventory_availability)
                .join(
                    warehouse_stock_locations,
                    inventory_availability.c.location_id == warehouse_stock_locations.c.location_id,
                )
                .where(
                    inventory_availability.c.sku_id == sku_id,
                    inventory_availability.c.warehouse_id == warehouse_id,
                    warehouse_stock_locations.c.custody == "available",
                    warehouse_stock_locations.c.is_active.is_(True),
                    inventory_availability.c.on_hand > ZERO,
                    or_(
                        inventory_availability.c.expiration_date.is_(None),
                        inventory_availability.c.expiration_date >= date.today(),
                    ),
                )
                .order_by(
                    inventory_availability.c.expiration_date.asc().nulls_last(),
                    inventory_availability.c.identity_key,
                    inventory_availability.c.location_id,
                )
                .with_for_update()
            )
        )
        .mappings()
        .all()
    )
    return [cast(Mapping[str, Any], row) for row in positions]


@router.post(
    "/v1/inventory/barcodes/resolve",
    response_model=BarcodeResolutionResponse,
    responses=error_responses(401, 403, 404, 409, 422, 500),
)
async def resolve_barcode(
    command: BarcodeResolveCommand,
    actor: Annotated[AuthorizedUser, Depends(require_inventory_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> BarcodeResolutionResponse:
    warehouse_branch_id = await session.scalar(
        select(warehouses.c.branch_id).where(warehouses.c.warehouse_id == command.warehouse_id)
    )
    if (
        warehouse_branch_id not in actor.branch_ids
        or command.warehouse_id not in actor.warehouse_ids
    ):
        raise AppError(
            403,
            "operational_scope_required",
            "Warehouse Operational Scope is required for barcode resolution.",
        )
    normalized = _normalize_barcode(command.barcode)
    candidates: list[BarcodeResolutionResponse] = []
    catalog_rows = (
        (
            await session.execute(
                select(
                    barcode_mappings.c.barcode_mapping_id,
                    barcode_mappings.c.sku_id,
                    skus.c.base_stocking_unit,
                    unit_conversions.c.unit_code,
                    unit_conversions.c.base_quantity,
                )
                .join(skus, barcode_mappings.c.sku_id == skus.c.sku_id)
                .outerjoin(
                    unit_conversions,
                    barcode_mappings.c.unit_conversion_id == unit_conversions.c.unit_conversion_id,
                )
                .where(
                    barcode_mappings.c.is_active.is_(True),
                    skus.c.is_active.is_(True),
                    func.upper(func.replace(barcode_mappings.c.barcode, " ", "")) == normalized,
                )
            )
        )
        .mappings()
        .all()
    )
    for row in catalog_rows:
        candidates.append(
            BarcodeResolutionResponse(
                barcode=normalized,
                barcode_mapping_id=row["barcode_mapping_id"],
                mapping_type="catalog",
                sku_id=row["sku_id"],
                unit_code=row["unit_code"] or row["base_stocking_unit"],
                base_quantity_per_unit=row["base_quantity"] or Decimal("1"),
                lot_code=None,
                serial_number=None,
                expiration_date=None,
            )
        )
    positions = (
        (
            await session.execute(
                select(inventory_availability)
                .join(
                    warehouse_stock_locations,
                    inventory_availability.c.location_id == warehouse_stock_locations.c.location_id,
                )
                .where(
                    inventory_availability.c.warehouse_id == command.warehouse_id,
                    inventory_availability.c.on_hand > ZERO,
                    or_(
                        inventory_availability.c.expiration_date.is_(None),
                        inventory_availability.c.expiration_date >= date.today(),
                    ),
                    warehouse_stock_locations.c.custody == "available",
                    warehouse_stock_locations.c.is_active.is_(True),
                )
            )
        )
        .mappings()
        .all()
    )
    lot_codes = {
        cast(str, row["lot_code"]): row
        for row in positions
        if row["lot_code"] is not None
        and _normalize_barcode(cast(str, row["lot_code"])) == normalized
    }
    for lot_code, position in lot_codes.items():
        candidates.append(
            BarcodeResolutionResponse(
                barcode=normalized,
                barcode_mapping_id=None,
                mapping_type="lot_identity",
                sku_id=position["sku_id"],
                unit_code=cast(
                    str,
                    await session.scalar(
                        select(skus.c.base_stocking_unit).where(skus.c.sku_id == position["sku_id"])
                    ),
                ),
                base_quantity_per_unit=Decimal("1"),
                lot_code=lot_code,
                serial_number=None,
                expiration_date=position["expiration_date"],
            )
        )
    for position in positions:
        for serial_number in position["serial_numbers"]:
            if _normalize_barcode(serial_number) != normalized:
                continue
            candidates.append(
                BarcodeResolutionResponse(
                    barcode=normalized,
                    barcode_mapping_id=None,
                    mapping_type="serial_identity",
                    sku_id=position["sku_id"],
                    unit_code=cast(
                        str,
                        await session.scalar(
                            select(skus.c.base_stocking_unit).where(
                                skus.c.sku_id == position["sku_id"]
                            )
                        ),
                    ),
                    base_quantity_per_unit=Decimal("1"),
                    lot_code=None,
                    serial_number=serial_number,
                    expiration_date=position["expiration_date"],
                )
            )
    if not candidates:
        inactive_mapping = await session.scalar(
            select(barcode_mappings.c.barcode_mapping_id).where(
                func.upper(func.replace(barcode_mappings.c.barcode, " ", "")) == normalized,
                barcode_mappings.c.is_active.is_(False),
            )
        )
        if inactive_mapping is not None:
            raise AppError(
                422,
                "barcode_mapping_inactive",
                "The Barcode Mapping is inactive and cannot authorize a Pick.",
            )
        raise AppError(
            404,
            "barcode_mapping_not_found",
            "No active eligible Barcode Mapping matches this scan.",
        )
    if len(candidates) > 1:
        raise AppError(
            409,
            "barcode_mapping_ambiguous",
            "The barcode resolves to more than one active eligible identity.",
        )
    return candidates[0]


@router.get(
    "/v1/fulfillment/orders/{fulfillment_order_id}/picking-context",
    response_model=PickingContextResponse,
    responses=error_responses(401, 403, 404, 500),
)
async def get_picking_context(
    fulfillment_order_id: UUID,
    actor: Annotated[AuthorizedUser, Depends(require_pick_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> PickingContextResponse:
    order = (
        (
            await session.execute(
                select(fulfillment_orders, fulfillment_order_state)
                .join(
                    fulfillment_order_state,
                    fulfillment_orders.c.fulfillment_order_id
                    == fulfillment_order_state.c.fulfillment_order_id,
                )
                .where(fulfillment_orders.c.fulfillment_order_id == fulfillment_order_id)
            )
        )
        .mappings()
        .one_or_none()
    )
    if order is None:
        raise AppError(
            404,
            "fulfillment_order_not_found",
            "The Fulfillment Order does not exist.",
        )
    warehouse_id = cast(UUID, order["warehouse_id"])
    if order["branch_id"] not in actor.branch_ids or warehouse_id not in actor.warehouse_ids:
        raise AppError(
            403,
            "operational_scope_required",
            "Warehouse Operational Scope is required for this picking context.",
        )
    rows = (
        (
            await session.execute(
                select(
                    fulfillment_order_lines.c.line_id,
                    fulfillment_order_lines.c.sku_id,
                    fulfillment_line_pick_state,
                    skus.c.code.label("sku_code"),
                    skus.c.name.label("sku_name"),
                    skus.c.base_stocking_unit,
                    skus.c.tracking_policy,
                    skus.c.expiration_control,
                )
                .join(
                    fulfillment_line_pick_state,
                    (
                        fulfillment_order_lines.c.fulfillment_order_id
                        == fulfillment_line_pick_state.c.fulfillment_order_id
                    )
                    & (fulfillment_order_lines.c.line_id == fulfillment_line_pick_state.c.line_id),
                )
                .join(skus, fulfillment_order_lines.c.sku_id == skus.c.sku_id)
                .where(fulfillment_order_lines.c.fulfillment_order_id == fulfillment_order_id)
                .order_by(fulfillment_order_lines.c.line_id)
            )
        )
        .mappings()
        .all()
    )
    position_rows = (
        (
            await session.execute(
                select(inventory_availability)
                .join(
                    warehouse_stock_locations,
                    inventory_availability.c.location_id == warehouse_stock_locations.c.location_id,
                )
                .where(
                    inventory_availability.c.warehouse_id == warehouse_id,
                    inventory_availability.c.on_hand > ZERO,
                    warehouse_stock_locations.c.custody == "available",
                    warehouse_stock_locations.c.is_active.is_(True),
                    or_(
                        inventory_availability.c.expiration_date.is_(None),
                        inventory_availability.c.expiration_date >= date.today(),
                    ),
                )
            )
        )
        .mappings()
        .all()
    )
    lines: list[PickingContextLineResponse] = []
    for row in rows:
        lot_totals: dict[tuple[str, date], Decimal] = {}
        for position in position_rows:
            if position["sku_id"] != row["sku_id"] or position["lot_code"] is None:
                continue
            key = (
                cast(str, position["lot_code"]),
                cast(date, position["expiration_date"]),
            )
            lot_totals[key] = lot_totals.get(key, ZERO) + position["on_hand"]
        earliest = min(
            (expiration for _, expiration in lot_totals),
            default=None,
        )
        candidates = [
            FefoCandidateResponse(
                lot_code=lot_code,
                expiration_date=expiration,
                available_quantity_base=quantity,
                recommended=expiration == earliest,
            )
            for (lot_code, expiration), quantity in sorted(
                lot_totals.items(),
                key=lambda item: (item[0][1], item[0][0]),
            )
        ]
        picked = row["picked_quantity_base"] - row["reversed_quantity_base"]
        lines.append(
            PickingContextLineResponse(
                line_id=row["line_id"],
                sku_id=row["sku_id"],
                sku_code=row["sku_code"],
                sku_name=row["sku_name"],
                base_stocking_unit=row["base_stocking_unit"],
                tracking_policy=row["tracking_policy"],
                expiration_control=row["expiration_control"],
                released_quantity_base=row["released_quantity_base"],
                picked_quantity_base=picked,
                reversed_quantity_base=row["reversed_quantity_base"],
                remaining_quantity_base=row["released_quantity_base"] - picked,
                fefo_candidates=candidates,
            )
        )
    return PickingContextResponse(
        fulfillment_order_id=fulfillment_order_id,
        version=order["version"],
        status=order["status"],
        warehouse_id=warehouse_id,
        lines=lines,
    )


@router.get(
    "/v1/fulfillment/orders/{fulfillment_order_id}/picks",
    response_model=PickHistoryListResponse,
    responses=error_responses(401, 403, 404, 500),
)
async def list_picks(
    fulfillment_order_id: UUID,
    actor: Annotated[AuthorizedUser, Depends(require_pick_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> PickHistoryListResponse:
    order = (
        (
            await session.execute(
                select(fulfillment_orders).where(
                    fulfillment_orders.c.fulfillment_order_id == fulfillment_order_id
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if order is None:
        raise AppError(
            404,
            "fulfillment_order_not_found",
            "The Fulfillment Order does not exist.",
        )
    if (
        order["branch_id"] not in actor.branch_ids
        or order["warehouse_id"] not in actor.warehouse_ids
    ):
        raise AppError(
            403,
            "operational_scope_required",
            "Warehouse Operational Scope is required for Pick history.",
        )
    postings = (
        (
            await session.execute(
                select(pick_postings)
                .where(pick_postings.c.fulfillment_order_id == fulfillment_order_id)
                .order_by(pick_postings.c.posted_at, pick_postings.c.pick_id)
            )
        )
        .mappings()
        .all()
    )
    items: list[PickHistoryItemResponse] = []
    for posting in postings:
        history_lines: list[PickHistoryLineResponse] = []
        line_rows = (
            (
                await session.execute(
                    select(pick_lines)
                    .where(pick_lines.c.pick_id == posting["pick_id"])
                    .order_by(pick_lines.c.pick_line_id)
                )
            )
            .mappings()
            .all()
        )
        for line in line_rows:
            assignments = (
                (
                    await session.execute(
                        select(
                            pick_identity_assignments,
                            lot_identities.c.lot_code,
                            lot_identities.c.expiration_date,
                            stock_serial_allocations.c.serial_number,
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
                        .where(pick_identity_assignments.c.pick_line_id == line["pick_line_id"])
                    )
                )
                .mappings()
                .all()
            )
            lots = [
                {
                    "lot_code": assignment["lot_code"],
                    "expiration_date": assignment["expiration_date"].isoformat(),
                    "quantity_base": str(assignment["quantity_base"]),
                    "recommended": ("false" if line["fefo_override_reason"] else "true"),
                }
                for assignment in assignments
                if assignment["tracking_policy"] == "lot"
            ]
            serials = sorted(
                assignment["serial_number"]
                for assignment in assignments
                if assignment["tracking_policy"] == "serial"
            )
            history_lines.append(
                PickHistoryLineResponse(
                    line_id=line["line_id"],
                    sku_id=line["sku_id"],
                    quantity_base=line["quantity_base"],
                    conversion_snapshot=line["conversion_snapshot"],
                    source_movement_id=line["source_movement_id"],
                    staging_movement_id=line["staging_movement_id"],
                    lot_selections=lots,
                    serial_selections=serials,
                )
            )
        items.append(
            PickHistoryItemResponse(
                pick_id=posting["pick_id"],
                event_type=posting["event_type"],
                reversal_of_pick_id=posting["reversal_of_pick_id"],
                reason=posting["reason"],
                actor_subject=posting["actor_subject"],
                correlation_id=posting["correlation_id"],
                posted_at=posting["posted_at"],
                quantity_base=sum(
                    (line.quantity_base for line in history_lines),
                    ZERO,
                ),
                lines=history_lines,
            )
        )
    return PickHistoryListResponse(items=items, total=len(items))


@router.post(
    "/v1/fulfillment/picks/{pick_id}/reversal",
    response_model=PickReversalResponse,
    status_code=201,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def reverse_pick(
    pick_id: UUID,
    command: ReversePickCommand,
    request: Request,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_pick_reverser)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> PickReversalResponse:
    if idempotency_key is None:
        raise AppError(400, "idempotency_key_required", "Idempotency-Key is required.")
    request_hash = sha256(
        (
            f"reverse-pick:{pick_id}:{actor.subject}:{command.model_dump_json(exclude_none=False)}"
        ).encode()
    ).hexdigest()
    await session.rollback()
    async with session.begin():
        original = (
            (
                await session.execute(
                    select(pick_postings, fulfillment_orders.c.branch_id)
                    .join(
                        fulfillment_orders,
                        pick_postings.c.fulfillment_order_id
                        == fulfillment_orders.c.fulfillment_order_id,
                    )
                    .where(pick_postings.c.pick_id == pick_id)
                )
            )
            .mappings()
            .one_or_none()
        )
        if original is None or original["event_type"] != "posted":
            raise AppError(404, "pick_not_found", "The original Pick does not exist.")
        fulfillment_order_id = cast(UUID, original["fulfillment_order_id"])
        await _lock_key(session, f"fulfillment:{fulfillment_order_id}")
        state = (
            (
                await session.execute(
                    select(fulfillment_order_state)
                    .where(fulfillment_order_state.c.fulfillment_order_id == fulfillment_order_id)
                    .with_for_update()
                )
            )
            .mappings()
            .one()
        )
        warehouse_id = cast(UUID, original["warehouse_id"])
        if original["branch_id"] not in actor.branch_ids or warehouse_id not in actor.warehouse_ids:
            raise AppError(
                403,
                "operational_scope_required",
                "Warehouse Operational Scope is required for Pick Reversal.",
            )
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.status_code = 200
            return PickReversalResponse.model_validate(replay)
        if state["version"] != command.expected_fulfillment_version:
            raise AppError(
                409,
                "fulfillment_version_conflict",
                "The Fulfillment Order changed; refresh before reversal.",
            )
        existing_reversal = await session.scalar(
            select(pick_postings.c.pick_id).where(pick_postings.c.reversal_of_pick_id == pick_id)
        )
        if existing_reversal is not None:
            raise AppError(
                409,
                "pick_already_reversed",
                "The Pick already has an immutable reversal.",
            )
        original_lines = (
            (
                await session.execute(
                    select(pick_lines)
                    .where(pick_lines.c.pick_id == pick_id)
                    .order_by(pick_lines.c.pick_line_id)
                )
            )
            .mappings()
            .all()
        )
        if not original_lines:
            raise AppError(409, "pick_empty", "The Pick has no posted lines.")
        await session.execute(
            text("SELECT pg_advisory_xact_lock_shared(hashtext('inventory-projection-rebuild'))")
        )
        for locked_sku_id in sorted({str(row["sku_id"]) for row in original_lines}):
            await _lock_key(session, f"stock:{warehouse_id}:{locked_sku_id}")
        await session.execute(
            insert(pick_postings).values(
                pick_id=command.reversal_pick_id,
                fulfillment_order_id=fulfillment_order_id,
                pick_release_id=original["pick_release_id"],
                warehouse_id=warehouse_id,
                event_type="reversed",
                reversal_of_pick_id=pick_id,
                reason=command.reason,
                actor_subject=actor.subject,
                correlation_id=request.state.correlation_id,
                idempotency_key=idempotency_key,
            )
        )
        source_movement_ids: list[UUID] = []
        staging_movement_ids: list[UUID] = []
        total_reversed = ZERO
        for original_line in original_lines:
            sku_id = cast(UUID, original_line["sku_id"])
            quantity_base = cast(Decimal, original_line["quantity_base"])
            original_source = (
                (
                    await session.execute(
                        select(stock_movements).where(
                            stock_movements.c.movement_id == original_line["source_movement_id"]
                        )
                    )
                )
                .mappings()
                .one()
            )
            original_staging = (
                (
                    await session.execute(
                        select(stock_movements).where(
                            stock_movements.c.movement_id == original_line["staging_movement_id"]
                        )
                    )
                )
                .mappings()
                .one()
            )
            assignments = (
                (
                    await session.execute(
                        select(
                            pick_identity_assignments,
                            lot_identities.c.lot_code,
                            stock_serial_allocations.c.serial_number,
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
                            pick_identity_assignments.c.pick_line_id
                            == original_line["pick_line_id"]
                        )
                    )
                )
                .mappings()
                .all()
            )
            identity_key = ""
            if assignments and assignments[0]["tracking_policy"] == "lot":
                identity_key = f"lot:{assignments[0]['lot_code']}"
            staging_positions = (
                (
                    await session.execute(
                        select(inventory_availability)
                        .where(
                            inventory_availability.c.sku_id == sku_id,
                            inventory_availability.c.warehouse_id == warehouse_id,
                            inventory_availability.c.location_id
                            == original_line["staging_location_id"],
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .all()
            )
            if assignments and assignments[0]["tracking_policy"] == "serial":
                serial_numbers = sorted(
                    cast(str, assignment["serial_number"]) for assignment in assignments
                )
                serial_rows = [
                    row
                    for row in staging_positions
                    if set(row["serial_numbers"]) & set(serial_numbers)
                ]
                if sum((row["on_hand"] for row in serial_rows), ZERO) < quantity_base:
                    raise AppError(
                        409,
                        "pick_reversal_stock_conflict",
                        "The staged Serial Identities are no longer reversible.",
                    )
                source_position = (
                    (
                        await session.execute(
                            select(inventory_availability)
                            .where(
                                inventory_availability.c.sku_id == sku_id,
                                inventory_availability.c.warehouse_id == warehouse_id,
                                inventory_availability.c.location_id
                                == original_line["source_location_id"],
                            )
                            .order_by(inventory_availability.c.identity_key)
                            .with_for_update()
                            .limit(1)
                        )
                    )
                    .mappings()
                    .one()
                )
                for row in serial_rows:
                    await session.execute(
                        update(inventory_availability)
                        .where(
                            inventory_availability.c.sku_id == sku_id,
                            inventory_availability.c.warehouse_id == warehouse_id,
                            inventory_availability.c.location_id == row["location_id"],
                            inventory_availability.c.identity_key == row["identity_key"],
                        )
                        .values(on_hand=ZERO, serial_numbers=[])
                    )
                await session.execute(
                    update(inventory_availability)
                    .where(
                        inventory_availability.c.sku_id == sku_id,
                        inventory_availability.c.warehouse_id == warehouse_id,
                        inventory_availability.c.location_id == source_position["location_id"],
                        inventory_availability.c.identity_key == source_position["identity_key"],
                    )
                    .values(
                        on_hand=inventory_availability.c.on_hand + quantity_base,
                        serial_numbers=sorted(
                            set(source_position["serial_numbers"]) | set(serial_numbers)
                        ),
                    )
                )
            else:
                staging_position = next(
                    (row for row in staging_positions if row["identity_key"] == identity_key),
                    None,
                )
                if staging_position is None or staging_position["on_hand"] < quantity_base:
                    raise AppError(
                        409,
                        "pick_reversal_stock_conflict",
                        "The staged quantity is no longer reversible.",
                    )
                await session.execute(
                    update(inventory_availability)
                    .where(
                        inventory_availability.c.sku_id == sku_id,
                        inventory_availability.c.warehouse_id == warehouse_id,
                        inventory_availability.c.location_id
                        == original_line["staging_location_id"],
                        inventory_availability.c.identity_key == identity_key,
                    )
                    .values(on_hand=inventory_availability.c.on_hand - quantity_base)
                )
                await session.execute(
                    pg_insert(inventory_availability)
                    .values(
                        sku_id=sku_id,
                        warehouse_id=warehouse_id,
                        location_id=original_line["source_location_id"],
                        identity_key=identity_key,
                        lot_code=staging_position["lot_code"],
                        serial_numbers=[],
                        expiration_date=staging_position["expiration_date"],
                        on_hand=quantity_base,
                        reserved=ZERO,
                    )
                    .on_conflict_do_update(
                        index_elements=[
                            "sku_id",
                            "warehouse_id",
                            "location_id",
                            "identity_key",
                        ],
                        set_={"on_hand": inventory_availability.c.on_hand + quantity_base},
                    )
                )
            movement_group_id = uuid4()
            staging_out_id = uuid4()
            available_in_id = uuid4()
            movement_common = {
                "sku_id": sku_id,
                "warehouse_id": warehouse_id,
                "quantity_base": quantity_base,
                "unit_cost": original_staging["unit_cost"],
                "base_currency": original_staging["base_currency"],
                "source_reference": f"PICK-REVERSAL:{command.reversal_pick_id}",
                "entered_unit": original_line["entered_unit"],
                "conversion_snapshot": original_line["conversion_snapshot"],
                "actor_subject": actor.subject,
                "correlation_id": request.state.correlation_id,
                "movement_type": "pick_reversal",
                "movement_group_id": movement_group_id,
            }
            await session.execute(
                insert(stock_movements),
                [
                    {
                        **movement_common,
                        "movement_id": staging_out_id,
                        "location_id": original_line["staging_location_id"],
                        "value_delta": -original_staging["value_delta"],
                        "idempotency_key": (
                            f"{idempotency_key}:{original_line['line_id']}:staging-out"
                        ),
                        "movement_leg": "pick_reversal_staging_out",
                        "reversal_of_movement_id": original_line["staging_movement_id"],
                    },
                    {
                        **movement_common,
                        "movement_id": available_in_id,
                        "location_id": original_line["source_location_id"],
                        "value_delta": -original_source["value_delta"],
                        "idempotency_key": (
                            f"{idempotency_key}:{original_line['line_id']}:available-in"
                        ),
                        "movement_leg": "pick_reversal_available_in",
                        "reversal_of_movement_id": original_line["source_movement_id"],
                    },
                ],
            )
            reversal_line_id = uuid4()
            await session.execute(
                insert(pick_lines).values(
                    pick_line_id=reversal_line_id,
                    pick_id=command.reversal_pick_id,
                    fulfillment_order_id=fulfillment_order_id,
                    line_id=original_line["line_id"],
                    sku_id=sku_id,
                    warehouse_id=warehouse_id,
                    source_location_id=original_line["source_location_id"],
                    staging_location_id=original_line["staging_location_id"],
                    quantity_base=quantity_base,
                    entered_quantity=original_line["entered_quantity"],
                    entered_unit=original_line["entered_unit"],
                    conversion_snapshot=original_line["conversion_snapshot"],
                    capture_mode="manual",
                    barcode_mapping_id=None,
                    manual_reason=command.reason,
                    fefo_override_reason=None,
                    movement_group_id=movement_group_id,
                    source_movement_id=staging_out_id,
                    staging_movement_id=available_in_id,
                )
            )
            for assignment in assignments:
                await session.execute(
                    insert(pick_identity_assignments).values(
                        pick_identity_assignment_id=uuid4(),
                        pick_line_id=reversal_line_id,
                        tracking_policy=assignment["tracking_policy"],
                        lot_identity_id=assignment["lot_identity_id"],
                        serial_allocation_id=assignment["serial_allocation_id"],
                        quantity_base=assignment["quantity_base"],
                    )
                )
            fulfillment_line = (
                (
                    await session.execute(
                        select(fulfillment_order_lines).where(
                            fulfillment_order_lines.c.fulfillment_order_id == fulfillment_order_id,
                            fulfillment_order_lines.c.line_id == original_line["line_id"],
                        )
                    )
                )
                .mappings()
                .one()
            )
            await session.execute(
                insert(inventory_reservation_events).values(
                    reservation_event_id=uuid4(),
                    commercial_approval_id=fulfillment_line["commercial_approval_id"],
                    sales_order_id=fulfillment_line["sales_order_id"],
                    sales_order_revision_id=fulfillment_line["sales_order_revision_id"],
                    line_id=original_line["line_id"],
                    sku_id=sku_id,
                    warehouse_id=warehouse_id,
                    event_type="restored",
                    quantity_base=quantity_base,
                    reason=command.reason,
                    actor_subject=actor.subject,
                    correlation_id=request.state.correlation_id,
                    idempotency_key=idempotency_key,
                )
            )
            await session.execute(
                update(inventory_reserved_by_sku_warehouse)
                .where(
                    inventory_reserved_by_sku_warehouse.c.sku_id == sku_id,
                    inventory_reserved_by_sku_warehouse.c.warehouse_id == warehouse_id,
                )
                .values(
                    reserved_quantity_base=(
                        inventory_reserved_by_sku_warehouse.c.reserved_quantity_base + quantity_base
                    ),
                    version=inventory_reserved_by_sku_warehouse.c.version + 1,
                )
            )
            await session.execute(
                update(sales_order_line_commitments)
                .where(
                    sales_order_line_commitments.c.sales_order_id
                    == fulfillment_line["sales_order_id"],
                    sales_order_line_commitments.c.line_id == original_line["line_id"],
                )
                .values(
                    reserved_quantity_base=(
                        sales_order_line_commitments.c.reserved_quantity_base + quantity_base
                    ),
                    picked_quantity_base=(
                        sales_order_line_commitments.c.picked_quantity_base - quantity_base
                    ),
                )
            )
            await session.execute(
                update(fulfillment_line_pick_state)
                .where(
                    fulfillment_line_pick_state.c.fulfillment_order_id == fulfillment_order_id,
                    fulfillment_line_pick_state.c.line_id == original_line["line_id"],
                )
                .values(
                    reversed_quantity_base=(
                        fulfillment_line_pick_state.c.reversed_quantity_base + quantity_base
                    ),
                    version=fulfillment_line_pick_state.c.version + 1,
                )
            )
            source_movement_ids.append(staging_out_id)
            staging_movement_ids.append(available_in_id)
            total_reversed += quantity_base
        next_picked = state["picked_quantity_base"] - total_reversed
        next_version = state["version"] + 1
        await session.execute(
            update(fulfillment_order_state)
            .where(fulfillment_order_state.c.fulfillment_order_id == fulfillment_order_id)
            .values(
                status=("pick_released" if next_picked == ZERO else "partially_picked"),
                picked_quantity_base=next_picked,
                version=next_version,
                updated_at=func.now(),
            )
        )
        result = PickReversalResponse(
            reversal_pick_id=command.reversal_pick_id,
            original_pick_id=pick_id,
            fulfillment_order_id=fulfillment_order_id,
            status="reversed",
            reversed_quantity_base=total_reversed,
            version=next_version,
            source_movement_ids=source_movement_ids,
            staging_movement_ids=staging_movement_ids,
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
    "/v1/fulfillment/orders/{fulfillment_order_id}/picks",
    response_model=PickResponse,
    status_code=201,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def post_pick(
    fulfillment_order_id: UUID,
    command: PostPickCommand,
    request: Request,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_picker)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> PickResponse:
    if idempotency_key is None:
        raise AppError(400, "idempotency_key_required", "Idempotency-Key is required.")
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
                    select(fulfillment_orders, fulfillment_order_state)
                    .join(
                        fulfillment_order_state,
                        fulfillment_orders.c.fulfillment_order_id
                        == fulfillment_order_state.c.fulfillment_order_id,
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
                404,
                "fulfillment_order_not_found",
                "The Fulfillment Order does not exist.",
            )
        warehouse_id = cast(UUID, order["warehouse_id"])
        if order["branch_id"] not in actor.branch_ids or warehouse_id not in actor.warehouse_ids:
            raise AppError(
                403,
                "operational_scope_required",
                "Warehouse Operational Scope is required for this Pick.",
            )
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.status_code = 200
            return PickResponse.model_validate(replay)
        if order["version"] != command.expected_fulfillment_version:
            raise AppError(
                409,
                "fulfillment_version_conflict",
                "The Fulfillment Order changed; refresh before posting the Pick.",
            )
        if order["status"] not in {"pick_released", "partially_picked"}:
            code = (
                "fulfillment_already_picked"
                if order["status"] == "picked"
                else "pick_release_required"
            )
            raise AppError(
                409,
                code,
                "The Fulfillment Order is not eligible for picking.",
            )
        release = (
            (
                await session.execute(
                    select(pick_releases)
                    .where(pick_releases.c.fulfillment_order_id == fulfillment_order_id)
                    .with_for_update()
                )
            )
            .mappings()
            .one()
        )
        line_ids = [line.line_id for line in command.lines]
        if len(line_ids) != len(set(line_ids)):
            raise AppError(422, "duplicate_pick_line", "Each Pick line may appear once.")
        source_lines = (
            (
                await session.execute(
                    select(
                        fulfillment_order_lines,
                        fulfillment_line_pick_state,
                        sales_order_line_revisions.c.entered_unit,
                        sales_order_line_revisions.c.conversion_snapshot,
                        skus.c.tracking_policy,
                        skus.c.expiration_control,
                    )
                    .join(
                        fulfillment_line_pick_state,
                        (
                            fulfillment_order_lines.c.fulfillment_order_id
                            == fulfillment_line_pick_state.c.fulfillment_order_id
                        )
                        & (
                            fulfillment_order_lines.c.line_id
                            == fulfillment_line_pick_state.c.line_id
                        ),
                    )
                    .join(
                        sales_order_line_revisions,
                        (
                            fulfillment_order_lines.c.sales_order_revision_id
                            == sales_order_line_revisions.c.sales_order_revision_id
                        )
                        & (
                            fulfillment_order_lines.c.line_id
                            == sales_order_line_revisions.c.line_id
                        ),
                    )
                    .join(
                        skus,
                        fulfillment_order_lines.c.sku_id == skus.c.sku_id,
                    )
                    .where(
                        fulfillment_order_lines.c.fulfillment_order_id == fulfillment_order_id,
                        fulfillment_order_lines.c.line_id.in_(line_ids),
                    )
                    .order_by(fulfillment_order_lines.c.line_id)
                    .with_for_update()
                )
            )
            .mappings()
            .all()
        )
        source_by_line = {row["line_id"]: row for row in source_lines}
        if set(source_by_line) != set(line_ids):
            raise AppError(
                404,
                "fulfillment_line_not_found",
                "A requested Fulfillment Order line does not exist.",
            )
        await session.execute(
            text("SELECT pg_advisory_xact_lock_shared(hashtext('inventory-projection-rebuild'))")
        )
        for locked_sku_id in sorted({str(row["sku_id"]) for row in source_lines}):
            await _lock_key(session, f"stock:{warehouse_id}:{locked_sku_id}")
        staging_location_id = await _ensure_staging_location(
            session,
            warehouse_id=warehouse_id,
            actor_subject=actor.subject,
        )
        valuation_by_sku: dict[UUID, Mapping[str, Any]] = {}
        result_lines: list[PickLineResponse] = []
        total_quantity = ZERO
        pick_id = command.pick_id
        await session.execute(
            insert(pick_postings).values(
                pick_id=pick_id,
                fulfillment_order_id=fulfillment_order_id,
                pick_release_id=release["pick_release_id"],
                warehouse_id=warehouse_id,
                event_type="posted",
                reversal_of_pick_id=None,
                reason=None,
                actor_subject=actor.subject,
                correlation_id=request.state.correlation_id,
                idempotency_key=idempotency_key,
            )
        )
        for input_line in command.lines:
            line = cast(Mapping[str, Any], source_by_line[input_line.line_id])
            quantity_base, conversion_snapshot = _base_quantity(line, input_line)
            already_picked = line["picked_quantity_base"] - line["reversed_quantity_base"]
            remaining = line["released_quantity_base"] - already_picked
            if quantity_base > remaining:
                raise AppError(
                    409,
                    "pick_quantity_exceeds_released",
                    "Pick quantity exceeds the remaining released quantity.",
                )
            tracking_policy = cast(str, line["tracking_policy"])
            sku_id = cast(UUID, line["sku_id"])
            serial_numbers: list[str] = []
            selection_by_serial: dict[str, PickSelectionInput] = {}
            barcode_resolutions: dict[str, BarcodeResolutionResponse] = {}
            for captured_selection in input_line.selections:
                if captured_selection.barcode is None:
                    continue
                resolved = await resolve_barcode(
                    command=BarcodeResolveCommand(
                        warehouse_id=warehouse_id,
                        barcode=captured_selection.barcode,
                    ),
                    actor=actor,
                    session=session,
                )
                if resolved.sku_id != sku_id:
                    raise AppError(
                        422,
                        "barcode_sku_mismatch",
                        "The scanned Barcode does not belong to this Fulfillment line SKU.",
                    )
                if resolved.unit_code != input_line.unit_code:
                    raise AppError(
                        422,
                        "barcode_unit_mismatch",
                        "The scanned Barcode Unit does not match the Pick line Unit.",
                    )
                barcode_resolutions[captured_selection.barcode] = resolved
                if captured_selection.lot_code is None:
                    captured_selection.lot_code = resolved.lot_code
                if captured_selection.serial_number is None:
                    captured_selection.serial_number = resolved.serial_number
                if captured_selection.quantity is None:
                    captured_selection.quantity = (
                        Decimal("1") if resolved.serial_number is not None else input_line.quantity
                    )
            if tracking_policy == "untracked" and input_line.selections:
                if (
                    len(input_line.selections) != 1
                    or input_line.selections[0].barcode is None
                    or input_line.selections[0].lot_code is not None
                    or input_line.selections[0].serial_number is not None
                    or barcode_resolutions[input_line.selections[0].barcode].mapping_type
                    != "catalog"
                ):
                    raise AppError(
                        422,
                        "untracked_selection_not_allowed",
                        "Untracked stock accepts only a resolved catalog Barcode scan.",
                    )
            available_positions = await _available_positions(
                session,
                sku_id=sku_id,
                warehouse_id=warehouse_id,
            )
            allocations: list[
                tuple[
                    Mapping[str, Any],
                    Decimal,
                    list[PickSelectionInput],
                    list[str],
                    bool,
                ]
            ] = []
            if tracking_policy == "lot" and input_line.selections:
                if (
                    any(
                        item.lot_code is None
                        or item.serial_number is not None
                        or item.quantity is None
                        for item in input_line.selections
                    )
                    or sum(
                        (cast(Decimal, item.quantity) for item in input_line.selections),
                        ZERO,
                    )
                    != input_line.quantity
                ):
                    raise AppError(
                        422,
                        "lot_selection_quantity_mismatch",
                        "Lot selection quantities must completely assign the Pick line quantity.",
                    )
                factor = Decimal(conversion_snapshot["base_quantity_per_unit"])
                remaining_by_position = {
                    (row["location_id"], row["identity_key"]): cast(Decimal, row["on_hand"])
                    for row in available_positions
                }
                for item in input_line.selections:
                    if item.barcode is not None:
                        resolved = barcode_resolutions[item.barcode]
                        if (
                            resolved.mapping_type != "lot_identity"
                            or resolved.lot_code != item.lot_code
                        ):
                            raise AppError(
                                422,
                                "barcode_identity_mismatch",
                                "The scanned Barcode does not resolve to the selected "
                                "Lot Identity.",
                            )
                    else:
                        if item.manual_reason is None:
                            raise AppError(
                                422,
                                "manual_pick_reason_required",
                                "Manual Pick Selection requires a reason.",
                            )
                        require_capability(actor, "fulfillment:pick-manual")
                    allocation_quantity = (cast(Decimal, item.quantity) * factor).quantize(
                        SIX_PLACES, ROUND_HALF_UP
                    )
                    quantity_left = allocation_quantity
                    for position in available_positions:
                        position_key = (position["location_id"], position["identity_key"])
                        position_remaining = remaining_by_position[position_key]
                        if position["lot_code"] != item.lot_code or position_remaining <= ZERO:
                            continue
                        current_eligible = [
                            row
                            for row in available_positions
                            if remaining_by_position[(row["location_id"], row["identity_key"])]
                            > ZERO
                        ]
                        earliest_expiration = (
                            current_eligible[0]["expiration_date"] if current_eligible else None
                        )
                        recommended = position["expiration_date"] == earliest_expiration
                        if not recommended:
                            if item.fefo_override_reason is None:
                                raise AppError(
                                    422,
                                    "fefo_override_reason_required",
                                    "Selecting a later-expiring lot requires a "
                                    "FEFO Override reason.",
                                )
                            require_capability(actor, "fulfillment:fefo-override")
                        segment_quantity = min(quantity_left, position_remaining)
                        allocations.append((position, segment_quantity, [item], [], recommended))
                        remaining_by_position[position_key] -= segment_quantity
                        quantity_left -= segment_quantity
                        if quantity_left == ZERO:
                            break
                    if quantity_left > ZERO:
                        raise AppError(
                            409,
                            "eligible_pick_stock_insufficient",
                            "Eligible Available stock is insufficient for this Pick.",
                        )
            elif tracking_policy == "lot":
                quantity_left = quantity_base
                for position in available_positions:
                    allocation_quantity = min(quantity_left, cast(Decimal, position["on_hand"]))
                    if allocation_quantity > ZERO:
                        allocations.append(
                            (
                                position,
                                allocation_quantity,
                                [],
                                [],
                                True,
                            )
                        )
                        quantity_left -= allocation_quantity
                    if quantity_left == ZERO:
                        break
                if quantity_left > ZERO:
                    raise AppError(
                        409,
                        "eligible_pick_stock_insufficient",
                        "Eligible Available stock is insufficient for this Pick.",
                    )
            if tracking_policy == "serial":
                if quantity_base != quantity_base.to_integral_value():
                    raise AppError(
                        422,
                        "serial_quantity_mismatch",
                        "Serial-tracked Pick quantity must be a whole Base Stocking Unit.",
                    )
                serial_numbers = [
                    item.serial_number
                    for item in input_line.selections
                    if item.serial_number is not None
                ]
                selection_by_serial = {
                    item.serial_number: item
                    for item in input_line.selections
                    if item.serial_number is not None
                }
                if (
                    len(serial_numbers) != int(quantity_base)
                    or len(serial_numbers) != len(input_line.selections)
                    or len(serial_numbers) != len(set(serial_numbers))
                    or any(
                        item.lot_code is not None or item.quantity != Decimal("1")
                        for item in input_line.selections
                    )
                ):
                    raise AppError(
                        422,
                        "identity_assignment_incomplete",
                        "Serial Identity count must exactly match the Pick quantity.",
                    )
                if any(
                    item.barcode is None and item.manual_reason is None
                    for item in input_line.selections
                ):
                    raise AppError(
                        422,
                        "manual_pick_reason_required",
                        "Manual Pick Selection requires a reason.",
                    )
                if any(item.barcode is None for item in input_line.selections):
                    require_capability(actor, "fulfillment:pick-manual")
                for serial_selection in input_line.selections:
                    if serial_selection.barcode is None:
                        continue
                    resolved = barcode_resolutions[serial_selection.barcode]
                    if (
                        resolved.mapping_type != "serial_identity"
                        or resolved.serial_number != serial_selection.serial_number
                    ):
                        raise AppError(
                            422,
                            "barcode_identity_mismatch",
                            "The scanned Barcode does not resolve to the selected Serial Identity.",
                        )
                unmatched_serials = set(serial_numbers)
                for position in available_positions:
                    position_serials = sorted(
                        unmatched_serials.intersection(position["serial_numbers"])
                    )
                    if position_serials:
                        allocations.append(
                            (
                                position,
                                Decimal(len(position_serials)).quantize(SIX_PLACES),
                                [selection_by_serial[number] for number in position_serials],
                                position_serials,
                                True,
                            )
                        )
                        unmatched_serials.difference_update(position_serials)
                if unmatched_serials:
                    raise AppError(
                        409,
                        "serial_already_picked",
                        "A selected Serial Identity is no longer Available.",
                    )
            if tracking_policy == "untracked":
                quantity_left = quantity_base
                for position in available_positions:
                    allocation_quantity = min(quantity_left, cast(Decimal, position["on_hand"]))
                    if allocation_quantity > ZERO:
                        allocations.append(
                            (
                                position,
                                allocation_quantity,
                                input_line.selections,
                                [],
                                True,
                            )
                        )
                        quantity_left -= allocation_quantity
                    if quantity_left == ZERO:
                        break
                if quantity_left > ZERO:
                    raise AppError(
                        409,
                        "eligible_pick_stock_insufficient",
                        "Eligible Available stock is insufficient for this Pick.",
                    )
            for allocation_index, (
                position,
                allocation_quantity,
                allocation_inputs,
                allocation_serials,
                recommended,
            ) in enumerate(allocations, start=1):
                selection = allocation_inputs[0] if allocation_inputs else None
                serial_numbers = allocation_serials
                allocation_entered_quantity = (
                    allocation_quantity / Decimal(conversion_snapshot["base_quantity_per_unit"])
                ).quantize(SIX_PLACES, ROUND_HALF_UP)
                allocation_conversion_snapshot = {
                    **conversion_snapshot,
                    "entered_quantity": str(allocation_entered_quantity),
                    "base_quantity": str(allocation_quantity),
                }
                valuation = valuation_by_sku.get(sku_id)
                if valuation is None:
                    valuation = cast(
                        Mapping[str, Any],
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
                        .one(),
                    )
                    valuation_by_sku[sku_id] = valuation
                unit_cost = cast(Decimal, valuation["moving_average_unit_cost"])
                value = (allocation_quantity * unit_cost).quantize(
                    SIX_PLACES,
                    ROUND_HALF_UP,
                )
                movement_group_id = uuid4()
                source_movement_id = uuid4()
                staging_movement_id = uuid4()
                pick_line_id = uuid4()
                movement_common = {
                    "sku_id": sku_id,
                    "warehouse_id": warehouse_id,
                    "quantity_base": allocation_quantity,
                    "unit_cost": unit_cost,
                    "base_currency": cast(
                        str,
                        await session.scalar(select(companies.c.base_currency).limit(1)),
                    ),
                    "source_reference": f"PICK:{pick_id}:{input_line.line_id}",
                    "entered_unit": input_line.unit_code,
                    "conversion_snapshot": allocation_conversion_snapshot,
                    "actor_subject": actor.subject,
                    "correlation_id": request.state.correlation_id,
                    "movement_type": "pick",
                    "movement_group_id": movement_group_id,
                    "reversal_of_movement_id": None,
                }
                await session.execute(
                    insert(stock_movements),
                    [
                        {
                            **movement_common,
                            "movement_id": source_movement_id,
                            "location_id": position["location_id"],
                            "value_delta": -value,
                            "idempotency_key": (
                                f"{idempotency_key}:{input_line.line_id}:"
                                f"{allocation_index}:available-out"
                            ),
                            "movement_leg": "pick_available_out",
                        },
                        {
                            **movement_common,
                            "movement_id": staging_movement_id,
                            "location_id": staging_location_id,
                            "value_delta": value,
                            "idempotency_key": (
                                f"{idempotency_key}:{input_line.line_id}:"
                                f"{allocation_index}:staging-in"
                            ),
                            "movement_leg": "pick_staging_in",
                        },
                    ],
                )
                await session.execute(
                    insert(pick_lines).values(
                        pick_line_id=pick_line_id,
                        pick_id=pick_id,
                        fulfillment_order_id=fulfillment_order_id,
                        line_id=input_line.line_id,
                        sku_id=sku_id,
                        warehouse_id=warehouse_id,
                        source_location_id=position["location_id"],
                        staging_location_id=staging_location_id,
                        quantity_base=allocation_quantity,
                        entered_quantity=allocation_entered_quantity,
                        entered_unit=input_line.unit_code,
                        conversion_snapshot=allocation_conversion_snapshot,
                        capture_mode=(
                            "automatic"
                            if not allocation_inputs
                            else "barcode"
                            if all(item.barcode is not None for item in allocation_inputs)
                            else "manual"
                        ),
                        barcode_mapping_id=(
                            next(
                                (
                                    resolution.barcode_mapping_id
                                    for resolution in barcode_resolutions.values()
                                    if resolution.barcode_mapping_id is not None
                                ),
                                None,
                            )
                        ),
                        manual_reason=(
                            selection.manual_reason
                            if selection is not None
                            else selection.manual_reason
                            if tracking_policy == "serial" and selection is not None
                            else None
                        ),
                        fefo_override_reason=(
                            selection.fefo_override_reason if selection is not None else None
                        ),
                        movement_group_id=movement_group_id,
                        source_movement_id=source_movement_id,
                        staging_movement_id=staging_movement_id,
                    )
                )
                lot_selections: list[dict[str, str]] = []
                if tracking_policy == "lot":
                    lot_identity = (
                        (
                            await session.execute(
                                select(lot_identities).where(
                                    lot_identities.c.sku_id == sku_id,
                                    lot_identities.c.lot_code == position["lot_code"],
                                )
                            )
                        )
                        .mappings()
                        .one()
                    )
                    await session.execute(
                        insert(pick_identity_assignments).values(
                            pick_identity_assignment_id=uuid4(),
                            pick_line_id=pick_line_id,
                            tracking_policy="lot",
                            lot_identity_id=lot_identity["lot_identity_id"],
                            serial_allocation_id=None,
                            captured_barcode=(selection.barcode if selection is not None else None),
                            quantity_base=allocation_quantity,
                        )
                    )
                    await session.execute(
                        insert(stock_lot_allocations),
                        [
                            {
                                "lot_allocation_id": uuid4(),
                                "movement_id": source_movement_id,
                                "lot_identity_id": lot_identity["lot_identity_id"],
                                "quantity_base": allocation_quantity,
                            },
                            {
                                "lot_allocation_id": uuid4(),
                                "movement_id": staging_movement_id,
                                "lot_identity_id": lot_identity["lot_identity_id"],
                                "quantity_base": allocation_quantity,
                            },
                        ],
                    )
                    lot_selections.append(
                        {
                            "lot_code": cast(str, position["lot_code"]),
                            "expiration_date": cast(date, position["expiration_date"]).isoformat(),
                            "quantity_base": str(allocation_quantity),
                            "recommended": "true" if recommended else "false",
                        }
                    )
                serial_selections: list[str] = []
                if tracking_policy == "serial":
                    serial_allocation_rows = (
                        (
                            await session.execute(
                                select(stock_serial_allocations).where(
                                    stock_serial_allocations.c.sku_id == sku_id,
                                    stock_serial_allocations.c.serial_number.in_(serial_numbers),
                                )
                            )
                        )
                        .mappings()
                        .all()
                    )
                    allocation_by_number = {
                        row["serial_number"]: row for row in serial_allocation_rows
                    }
                    if set(allocation_by_number) != set(serial_numbers):
                        raise AppError(
                            409,
                            "serial_sku_mismatch",
                            "A selected Serial Identity does not belong to this SKU.",
                        )
                    for serial_number in sorted(serial_numbers):
                        await session.execute(
                            insert(pick_identity_assignments).values(
                                pick_identity_assignment_id=uuid4(),
                                pick_line_id=pick_line_id,
                                tracking_policy="serial",
                                lot_identity_id=None,
                                serial_allocation_id=allocation_by_number[serial_number][
                                    "serial_allocation_id"
                                ],
                                captured_barcode=selection_by_serial[serial_number].barcode,
                                quantity_base=Decimal("1"),
                            )
                        )
                        await session.execute(
                            pg_insert(inventory_availability)
                            .values(
                                sku_id=sku_id,
                                warehouse_id=warehouse_id,
                                location_id=staging_location_id,
                                identity_key=f"serial:{serial_number}",
                                lot_code=None,
                                serial_numbers=[serial_number],
                                expiration_date=allocation_by_number[serial_number][
                                    "expiration_date"
                                ],
                                on_hand=Decimal("1"),
                                reserved=ZERO,
                            )
                            .on_conflict_do_update(
                                index_elements=[
                                    "sku_id",
                                    "warehouse_id",
                                    "location_id",
                                    "identity_key",
                                ],
                                set_={"on_hand": Decimal("1")},
                            )
                        )
                    remaining_serials = sorted(
                        set(position["serial_numbers"]) - set(serial_numbers)
                    )
                    await session.execute(
                        update(inventory_availability)
                        .where(
                            inventory_availability.c.sku_id == sku_id,
                            inventory_availability.c.warehouse_id == warehouse_id,
                            inventory_availability.c.location_id == position["location_id"],
                            inventory_availability.c.identity_key == position["identity_key"],
                        )
                        .values(
                            on_hand=(inventory_availability.c.on_hand - allocation_quantity),
                            serial_numbers=remaining_serials,
                        )
                    )
                    serial_selections = sorted(serial_numbers)
                else:
                    await session.execute(
                        update(inventory_availability)
                        .where(
                            inventory_availability.c.sku_id == sku_id,
                            inventory_availability.c.warehouse_id == warehouse_id,
                            inventory_availability.c.location_id == position["location_id"],
                            inventory_availability.c.identity_key == position["identity_key"],
                        )
                        .values(on_hand=inventory_availability.c.on_hand - allocation_quantity)
                    )
                    await session.execute(
                        pg_insert(inventory_availability)
                        .values(
                            sku_id=sku_id,
                            warehouse_id=warehouse_id,
                            location_id=staging_location_id,
                            identity_key=position["identity_key"],
                            lot_code=position["lot_code"],
                            serial_numbers=position["serial_numbers"],
                            expiration_date=position["expiration_date"],
                            on_hand=allocation_quantity,
                            reserved=ZERO,
                        )
                        .on_conflict_do_update(
                            index_elements=[
                                "sku_id",
                                "warehouse_id",
                                "location_id",
                                "identity_key",
                            ],
                            set_={
                                "on_hand": (inventory_availability.c.on_hand + allocation_quantity)
                            },
                        )
                    )
                await session.execute(
                    insert(inventory_reservation_events).values(
                        reservation_event_id=uuid4(),
                        commercial_approval_id=line["commercial_approval_id"],
                        sales_order_id=line["sales_order_id"],
                        sales_order_revision_id=line["sales_order_revision_id"],
                        line_id=input_line.line_id,
                        sku_id=sku_id,
                        warehouse_id=warehouse_id,
                        event_type="consumed",
                        quantity_base=allocation_quantity,
                        reason="Reserved quantity moved to Dispatch Staging",
                        actor_subject=actor.subject,
                        correlation_id=request.state.correlation_id,
                        idempotency_key=(
                            f"{idempotency_key}:{input_line.line_id}:"
                            f"{allocation_index}:reservation-consumed"
                        ),
                    )
                )
                await session.execute(
                    update(inventory_reserved_by_sku_warehouse)
                    .where(
                        inventory_reserved_by_sku_warehouse.c.sku_id == sku_id,
                        inventory_reserved_by_sku_warehouse.c.warehouse_id == warehouse_id,
                    )
                    .values(
                        reserved_quantity_base=(
                            inventory_reserved_by_sku_warehouse.c.reserved_quantity_base
                            - allocation_quantity
                        ),
                        version=inventory_reserved_by_sku_warehouse.c.version + 1,
                    )
                )
                await session.execute(
                    update(sales_order_line_commitments)
                    .where(
                        sales_order_line_commitments.c.sales_order_id == line["sales_order_id"],
                        sales_order_line_commitments.c.line_id == input_line.line_id,
                    )
                    .values(
                        reserved_quantity_base=(
                            sales_order_line_commitments.c.reserved_quantity_base
                            - allocation_quantity
                        ),
                        picked_quantity_base=(
                            sales_order_line_commitments.c.picked_quantity_base
                            + allocation_quantity
                        ),
                    )
                )
                await session.execute(
                    update(fulfillment_line_pick_state)
                    .where(
                        fulfillment_line_pick_state.c.fulfillment_order_id == fulfillment_order_id,
                        fulfillment_line_pick_state.c.line_id == input_line.line_id,
                    )
                    .values(
                        picked_quantity_base=(
                            fulfillment_line_pick_state.c.picked_quantity_base + allocation_quantity
                        ),
                        version=fulfillment_line_pick_state.c.version + 1,
                    )
                )
                result_lines.append(
                    PickLineResponse(
                        line_id=input_line.line_id,
                        sku_id=sku_id,
                        quantity_base=allocation_quantity,
                        conversion_snapshot=allocation_conversion_snapshot,
                        source_movement_id=source_movement_id,
                        staging_movement_id=staging_movement_id,
                        lot_selections=lot_selections,
                        serial_selections=serial_selections,
                    )
                )
                total_quantity += allocation_quantity
        next_picked = order["picked_quantity_base"] + total_quantity
        remaining_total = order["reserved_quantity_base"] - next_picked
        next_status: Literal["partially_picked", "picked"] = (
            "picked" if remaining_total == ZERO else "partially_picked"
        )
        next_version = order["version"] + 1
        await session.execute(
            update(fulfillment_order_state)
            .where(fulfillment_order_state.c.fulfillment_order_id == fulfillment_order_id)
            .values(
                status=next_status,
                picked_quantity_base=next_picked,
                version=next_version,
                updated_at=func.now(),
            )
        )
        result = PickResponse(
            pick_id=pick_id,
            fulfillment_order_id=fulfillment_order_id,
            status=next_status,
            picked_quantity_base=total_quantity,
            remaining_quantity_base=remaining_total,
            version=next_version,
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
