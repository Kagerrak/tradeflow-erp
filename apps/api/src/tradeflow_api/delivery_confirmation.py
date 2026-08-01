from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256
from typing import Annotated, Any, Literal, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from tradeflow_api.auth import AuthorizedUser, require_delivery_confirmer
from tradeflow_api.command_receipts import get_command_replay, store_command_result
from tradeflow_api.database import get_database_session
from tradeflow_api.errors import AppError, error_responses
from tradeflow_api.models import (
    branches,
    companies,
    delivery_confirmation_evidence,
    delivery_confirmation_lines,
    delivery_confirmations,
    delivery_dispatches,
    delivery_evidence,
    delivery_lines,
    delivery_receipts,
    delivery_state,
    document_series,
    fulfillment_order_state,
    inventory_availability,
    inventory_valuation,
    outbox_events,
    outbox_processing_state,
    pick_identity_assignments,
    pick_lines,
    stock_movements,
    warehouse_stock_locations,
)
from tradeflow_api.object_storage import ObjectStorage, get_object_storage

router = APIRouter(tags=["delivery confirmation"])
ZERO = Decimal("0")
SIX_PLACES = Decimal("0.000001")


class CommandModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConfirmationLineCommand(CommandModel):
    line_id: UUID
    accepted_quantity_base: Decimal = Field(gt=0)


class ConfirmDeliveryCommand(CommandModel):
    confirmation_id: UUID
    expected_delivery_version: int = Field(gt=0)
    recipient_name: str = Field(min_length=1, max_length=300)
    device_captured_at: datetime
    notes: str | None = Field(default=None, max_length=2000)
    evidence_ids: list[UUID] = Field(min_length=1)
    lines: list[ConfirmationLineCommand] = Field(min_length=1)


class ConfirmationLineResponse(BaseModel):
    line_id: UUID
    sku_id: UUID
    accepted_quantity_base: Decimal
    unit_cost: Decimal
    value_delta: Decimal
    outbound_movement_id: UUID


class DeliveryReceiptResponse(BaseModel):
    delivery_receipt_id: UUID
    number: str
    status: Literal["pending_document", "ready", "unavailable"]


class DeliveryConfirmationResponse(BaseModel):
    confirmation_id: UUID
    delivery_id: UUID
    status: Literal["confirmed"]
    version: int
    lines: list[ConfirmationLineResponse]
    delivery_receipt: DeliveryReceiptResponse
    outbox_event_id: UUID


class EvidenceUploadIntent(CommandModel):
    evidence_id: UUID
    kind: Literal["signature", "photo"]
    content_type: Literal["image/png", "image/jpeg", "image/webp"]
    size_bytes: int = Field(gt=0, le=10 * 1024 * 1024)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    device_captured_at: datetime


class EvidenceUploadResponse(BaseModel):
    evidence_id: UUID
    status: Literal["uploading", "verified"]
    upload_url: str | None
    upload_headers: dict[str, str]
    expires_at: datetime | None


async def _authorize_assigned_delivery(
    session: AsyncSession,
    *,
    delivery_id: UUID,
    actor: AuthorizedUser,
) -> Mapping[str, Any]:
    delivery = await _delivery(session, delivery_id)
    if delivery is None:
        raise AppError(404, "delivery_not_found", "The Delivery does not exist.")
    if delivery["assigned_to"] != actor.subject:
        raise AppError(
            403,
            "delivery_assignment_required",
            "The Delivery is no longer assigned to this user.",
        )
    if delivery["branch_id"] not in actor.branch_ids:
        raise AppError(
            403,
            "operational_scope_required",
            "Branch Operational Scope is required.",
        )
    return delivery


@router.post(
    "/v1/deliveries/{delivery_id}/evidence/uploads",
    response_model=EvidenceUploadResponse,
    status_code=201,
    responses=error_responses(401, 403, 404, 409, 422, 503),
)
async def create_evidence_upload(
    delivery_id: UUID,
    command: EvidenceUploadIntent,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_delivery_confirmer)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
) -> EvidenceUploadResponse:
    await session.rollback()
    async with session.begin():
        await _lock(session, f"delivery:{delivery_id}")
        delivery = await _authorize_assigned_delivery(
            session,
            delivery_id=delivery_id,
            actor=actor,
        )
        if delivery["delivery_status"] != "dispatched":
            raise AppError(409, "delivery_version_conflict", "The Delivery is no longer open.")
        existing = (
            (
                await session.execute(
                    select(delivery_evidence).where(
                        delivery_evidence.c.evidence_id == command.evidence_id
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        object_key = f"deliveries/{delivery_id}/evidence/{command.evidence_id}"
        if existing is not None:
            same = (
                existing["delivery_id"] == delivery_id
                and existing["captured_by"] == actor.subject
                and existing["kind"] == command.kind
                and existing["content_type"] == command.content_type
                and existing["size_bytes"] == command.size_bytes
                and existing["sha256"] == command.sha256
            )
            if not same:
                raise AppError(
                    409,
                    "delivery_evidence_identity_conflict",
                    "Evidence identity was already used for different proof.",
                )
            if existing["status"] == "verified":
                response.status_code = 200
                return EvidenceUploadResponse(
                    evidence_id=command.evidence_id,
                    status="verified",
                    upload_url=None,
                    upload_headers={},
                    expires_at=None,
                )
            response.status_code = 200
        else:
            await session.execute(
                insert(delivery_evidence).values(
                    evidence_id=command.evidence_id,
                    delivery_id=delivery_id,
                    kind=command.kind,
                    object_key=object_key,
                    content_type=command.content_type,
                    size_bytes=command.size_bytes,
                    sha256=command.sha256,
                    captured_by=actor.subject,
                    device_captured_at=command.device_captured_at,
                    status="uploading",
                )
            )
        try:
            await storage.ensure_bucket()
        except Exception as error:
            raise AppError(
                503,
                "evidence_storage_unavailable",
                "Evidence storage could not prepare the private proof bucket.",
            ) from error
        return EvidenceUploadResponse(
            evidence_id=command.evidence_id,
            status="uploading",
            upload_url=storage.signed_put_url(
                content_type=command.content_type,
                object_key=object_key,
                sha256=command.sha256,
            ),
            upload_headers={
                "Content-Type": command.content_type,
                "x-amz-meta-sha256": command.sha256,
            },
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )


@router.post(
    "/v1/deliveries/{delivery_id}/evidence/{evidence_id}/complete",
    response_model=EvidenceUploadResponse,
    responses=error_responses(401, 403, 404, 409, 422, 503),
)
async def complete_evidence_upload(
    delivery_id: UUID,
    evidence_id: UUID,
    actor: Annotated[AuthorizedUser, Depends(require_delivery_confirmer)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
) -> EvidenceUploadResponse:
    await session.rollback()
    async with session.begin():
        await _lock(session, f"delivery:{delivery_id}")
        await _authorize_assigned_delivery(
            session,
            delivery_id=delivery_id,
            actor=actor,
        )
        evidence = (
            (
                await session.execute(
                    select(delivery_evidence)
                    .where(
                        delivery_evidence.c.evidence_id == evidence_id,
                        delivery_evidence.c.delivery_id == delivery_id,
                        delivery_evidence.c.captured_by == actor.subject,
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if evidence is None:
            raise AppError(404, "delivery_evidence_not_found", "Evidence does not exist.")
        if evidence["status"] != "verified":
            try:
                stored = await storage.head(evidence["object_key"])
            except Exception as error:
                raise AppError(
                    503,
                    "evidence_storage_unavailable",
                    "Evidence storage could not verify the uploaded object.",
                ) from error
            if (
                stored.content_type != evidence["content_type"]
                or stored.size_bytes != evidence["size_bytes"]
                or stored.sha256 != evidence["sha256"]
            ):
                raise AppError(
                    409,
                    "delivery_evidence_integrity_conflict",
                    "Uploaded evidence type, size, or SHA-256 did not match the intent.",
                )
            await session.execute(
                update(delivery_evidence)
                .where(delivery_evidence.c.evidence_id == evidence_id)
                .values(status="verified", verified_at=func.now())
            )
        return EvidenceUploadResponse(
            evidence_id=evidence_id,
            status="verified",
            upload_url=None,
            upload_headers={},
            expires_at=None,
        )


def _request_hash(command: ConfirmDeliveryCommand, delivery_id: UUID, actor: str) -> str:
    value = f"confirm-delivery:{delivery_id}:{actor}:{command.model_dump_json(exclude_none=False)}"
    return sha256(value.encode()).hexdigest()


async def _lock(session: AsyncSession, key: str) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": key},
    )


async def _delivery(
    session: AsyncSession,
    delivery_id: UUID,
) -> Mapping[str, Any] | None:
    row = (
        (
            await session.execute(
                select(
                    delivery_dispatches,
                    delivery_state.c.status.label("delivery_status"),
                    delivery_state.c.assigned_to,
                    delivery_state.c.version.label("delivery_version"),
                    branches.c.code.label("branch_code"),
                )
                .join(
                    delivery_state,
                    delivery_dispatches.c.delivery_id == delivery_state.c.delivery_id,
                )
                .join(branches, delivery_dispatches.c.branch_id == branches.c.branch_id)
                .where(delivery_dispatches.c.delivery_id == delivery_id)
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    return cast(Mapping[str, Any] | None, row)


async def _validated_evidence(
    session: AsyncSession,
    *,
    delivery_id: UUID,
    actor_subject: str,
    evidence_ids: list[UUID],
) -> list[Mapping[str, Any]]:
    if len(set(evidence_ids)) != len(evidence_ids):
        raise AppError(422, "delivery_evidence_invalid", "Evidence identifiers must be unique.")
    rows = list(
        (
            await session.execute(
                select(delivery_evidence)
                .where(delivery_evidence.c.evidence_id.in_(evidence_ids))
                .with_for_update()
            )
        ).mappings()
    )
    if (
        len(rows) != len(evidence_ids)
        or any(
            row["delivery_id"] != delivery_id
            or row["captured_by"] != actor_subject
            or row["status"] != "verified"
            for row in rows
        )
        or not any(row["kind"] == "signature" for row in rows)
    ):
        raise AppError(
            409,
            "delivery_evidence_conflict",
            "Verified signature evidence owned by this assigned Delivery Staff user is required.",
        )
    return [cast(Mapping[str, Any], row) for row in rows]


async def _decrement_transit(
    session: AsyncSession,
    *,
    delivery_line: Mapping[str, Any],
    transit_location_id: UUID,
) -> None:
    assignments = list(
        (
            await session.execute(
                select(pick_identity_assignments).where(
                    pick_identity_assignments.c.pick_line_id == delivery_line["pick_line_id"]
                )
            )
        ).mappings()
    )
    positions: list[tuple[str, Decimal]]
    if not assignments:
        positions = [("", cast(Decimal, delivery_line["quantity_base"]))]
    else:
        positions = []
        for assignment in assignments:
            if assignment["tracking_policy"] == "lot":
                lot_code = await session.scalar(
                    text("SELECT lot_code FROM lot_identities WHERE lot_identity_id = :id"),
                    {"id": assignment["lot_identity_id"]},
                )
                positions.append((f"lot:{lot_code}", assignment["quantity_base"]))
            else:
                serial_number = await session.scalar(
                    text(
                        "SELECT serial_number FROM stock_serial_allocations "
                        "WHERE serial_allocation_id = :id"
                    ),
                    {"id": assignment["serial_allocation_id"]},
                )
                positions.append((f"serial:{serial_number}", Decimal("1")))
    for identity_key, quantity in positions:
        current = (
            (
                await session.execute(
                    select(inventory_availability)
                    .where(
                        inventory_availability.c.sku_id == delivery_line["sku_id"],
                        inventory_availability.c.warehouse_id == delivery_line["warehouse_id"],
                        inventory_availability.c.location_id == transit_location_id,
                        inventory_availability.c.identity_key == identity_key,
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if current is None or current["on_hand"] < quantity:
            raise AppError(
                409,
                "delivery_inventory_conflict",
                "In Transit quantity or tracked identity changed; refresh before confirming.",
            )
        await session.execute(
            update(inventory_availability)
            .where(
                inventory_availability.c.sku_id == delivery_line["sku_id"],
                inventory_availability.c.warehouse_id == delivery_line["warehouse_id"],
                inventory_availability.c.location_id == transit_location_id,
                inventory_availability.c.identity_key == identity_key,
            )
            .values(
                on_hand=inventory_availability.c.on_hand - quantity,
                serial_numbers=[]
                if identity_key.startswith("serial:")
                else current["serial_numbers"],
            )
        )


@router.post(
    "/v1/deliveries/{delivery_id}/confirmations",
    response_model=DeliveryConfirmationResponse,
    status_code=201,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def confirm_delivery(
    delivery_id: UUID,
    command: ConfirmDeliveryCommand,
    request: Request,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_delivery_confirmer)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> DeliveryConfirmationResponse:
    if idempotency_key is None:
        raise AppError(400, "idempotency_key_required", "Idempotency-Key is required.")
    request_hash = _request_hash(command, delivery_id, actor.subject)
    await session.rollback()
    async with session.begin():
        await _lock(session, f"delivery:{delivery_id}")
        delivery = await _delivery(session, delivery_id)
        if delivery is None:
            raise AppError(404, "delivery_not_found", "The Delivery does not exist.")
        if delivery["assigned_to"] != actor.subject:
            raise AppError(
                403,
                "delivery_assignment_required",
                "The Delivery is no longer assigned to this user.",
            )
        if delivery["branch_id"] not in actor.branch_ids:
            raise AppError(
                403, "operational_scope_required", "Branch Operational Scope is required."
            )
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.status_code = 200
            return DeliveryConfirmationResponse.model_validate(replay)
        if delivery["payment_timing_policy"] == "cash_on_delivery":
            raise AppError(
                409,
                "cod_collection_required",
                "Cash collection must be posted atomically with acceptance.",
            )
        if (
            delivery["delivery_status"] != "dispatched"
            or delivery["delivery_version"] != command.expected_delivery_version
        ):
            raise AppError(
                409, "delivery_version_conflict", "The Delivery changed; refresh before retrying."
            )
        await _validated_evidence(
            session,
            delivery_id=delivery_id,
            actor_subject=actor.subject,
            evidence_ids=command.evidence_ids,
        )
        lines = list(
            (
                await session.execute(
                    select(delivery_lines, pick_lines.c.entered_unit)
                    .join(pick_lines, delivery_lines.c.pick_line_id == pick_lines.c.pick_line_id)
                    .where(delivery_lines.c.delivery_id == delivery_id)
                    .order_by(delivery_lines.c.line_id, delivery_lines.c.delivery_line_id)
                    .with_for_update()
                )
            ).mappings()
        )
        expected: dict[UUID, Decimal] = defaultdict(lambda: ZERO)
        for line in lines:
            expected[line["line_id"]] += line["quantity_base"]
        supplied = {line.line_id: line.accepted_quantity_base for line in command.lines}
        if len(supplied) != len(command.lines) or supplied != expected:
            raise AppError(
                409,
                "delivery_quantity_conflict",
                "Issue #10 requires full acceptance of every dispatched Delivery line.",
            )
        await session.execute(
            text("SELECT pg_advisory_xact_lock_shared(hashtext('inventory-projection-rebuild'))")
        )
        for sku_id in sorted({line["sku_id"] for line in lines}, key=str):
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:stock_key))"),
                {"stock_key": f"{sku_id}:{delivery['warehouse_id']}"},
            )
        transit_location_id = await session.scalar(
            select(warehouse_stock_locations.c.location_id).where(
                warehouse_stock_locations.c.warehouse_id == delivery["warehouse_id"],
                warehouse_stock_locations.c.custody == "in_transit",
            )
        )
        if transit_location_id is None:
            raise AppError(
                409, "delivery_inventory_conflict", "The In Transit location is unavailable."
            )
        base_currency = cast(str, await session.scalar(select(companies.c.base_currency).limit(1)))
        next_version = delivery["delivery_version"] + 1
        await session.execute(
            insert(delivery_confirmations).values(
                confirmation_id=command.confirmation_id,
                delivery_id=delivery_id,
                recipient_name=command.recipient_name,
                device_captured_at=command.device_captured_at,
                notes=command.notes,
                confirmed_by=actor.subject,
                delivery_version=next_version,
                correlation_id=request.state.correlation_id,
                idempotency_key=idempotency_key,
            )
        )
        response_lines: dict[tuple[UUID, UUID], dict[str, Any]] = {}
        sku_totals: dict[UUID, Decimal] = defaultdict(lambda: ZERO)
        for line in lines:
            valuation = (
                (
                    await session.execute(
                        select(inventory_valuation)
                        .where(
                            inventory_valuation.c.sku_id == line["sku_id"],
                            inventory_valuation.c.warehouse_id == delivery["warehouse_id"],
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one()
            )
            unit_cost = cast(Decimal, valuation["moving_average_unit_cost"]).quantize(SIX_PLACES)
            quantity = cast(Decimal, line["quantity_base"])
            value_delta = -(quantity * unit_cost).quantize(SIX_PLACES, rounding=ROUND_HALF_UP)
            movement_id = uuid4()
            await _decrement_transit(
                session,
                delivery_line={**line, "warehouse_id": delivery["warehouse_id"]},
                transit_location_id=cast(UUID, transit_location_id),
            )
            await session.execute(
                insert(stock_movements).values(
                    movement_id=movement_id,
                    sku_id=line["sku_id"],
                    warehouse_id=delivery["warehouse_id"],
                    location_id=transit_location_id,
                    movement_type="delivery_confirmation",
                    quantity_base=quantity,
                    unit_cost=unit_cost,
                    value_delta=value_delta,
                    base_currency=base_currency,
                    source_reference=f"DELIVERY-CONFIRMATION:{command.confirmation_id}",
                    entered_unit=line["entered_unit"],
                    conversion_snapshot={"source": "delivery_confirmation", "factor": "1.000000"},
                    actor_subject=actor.subject,
                    correlation_id=request.state.correlation_id,
                    idempotency_key=f"{idempotency_key}:{line['delivery_line_id']}:outbound",
                    movement_group_id=uuid4(),
                    movement_leg="delivery_outbound",
                    reversal_of_movement_id=None,
                )
            )
            await session.execute(
                insert(delivery_confirmation_lines).values(
                    confirmation_line_id=uuid4(),
                    confirmation_id=command.confirmation_id,
                    delivery_line_id=line["delivery_line_id"],
                    line_id=line["line_id"],
                    sku_id=line["sku_id"],
                    accepted_quantity_base=quantity,
                    unit_cost=unit_cost,
                    value_delta=value_delta,
                    outbound_movement_id=movement_id,
                )
            )
            sku_totals[line["sku_id"]] += quantity
            key = (line["line_id"], line["sku_id"])
            aggregate = response_lines.setdefault(
                key,
                {
                    "quantity": ZERO,
                    "value": ZERO,
                    "unit_cost": unit_cost,
                    "movement_id": movement_id,
                },
            )
            aggregate["quantity"] += quantity
            aggregate["value"] += value_delta
        for sku_id, quantity in sku_totals.items():
            await session.execute(
                update(inventory_valuation)
                .where(
                    inventory_valuation.c.sku_id == sku_id,
                    inventory_valuation.c.warehouse_id == delivery["warehouse_id"],
                )
                .values(
                    quantity_on_hand=inventory_valuation.c.quantity_on_hand - quantity,
                    inventory_value=inventory_valuation.c.inventory_value
                    - quantity * inventory_valuation.c.moving_average_unit_cost,
                )
            )
        await session.execute(
            insert(delivery_confirmation_evidence),
            [
                {"confirmation_id": command.confirmation_id, "evidence_id": evidence_id}
                for evidence_id in command.evidence_ids
            ],
        )
        series = (
            (
                await session.execute(
                    select(document_series)
                    .where(
                        document_series.c.branch_id == delivery["branch_id"],
                        document_series.c.document_type == "delivery_receipt",
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if series is None:
            series_id = uuid4()
            series_number = 1
            prefix = f"DR-{delivery['branch_code']}"
            await session.execute(
                insert(document_series).values(
                    document_series_id=series_id,
                    branch_id=delivery["branch_id"],
                    document_type="delivery_receipt",
                    prefix=prefix,
                    next_number=2,
                )
            )
        else:
            series_id = series["document_series_id"]
            series_number = series["next_number"]
            prefix = series["prefix"]
            await session.execute(
                update(document_series)
                .where(document_series.c.document_series_id == series_id)
                .values(next_number=document_series.c.next_number + 1)
            )
        receipt_id = uuid4()
        receipt_number = f"{prefix}-{series_number:08d}"
        receipt_snapshot = {
            "delivery_id": str(delivery_id),
            "confirmation_id": str(command.confirmation_id),
            "sales_order_id": str(delivery["sales_order_id"]),
            "customer_id": str(delivery["customer_id"]),
            "recipient_name": command.recipient_name,
            "delivery_address": dict(delivery["delivery_address_snapshot"]),
            "evidence_ids": [str(value) for value in command.evidence_ids],
            "lines": [
                {
                    "line_id": str(line_id),
                    "sku_id": str(sku_id),
                    "accepted_quantity_base": str(data["quantity"]),
                }
                for (line_id, sku_id), data in sorted(
                    response_lines.items(), key=lambda item: str(item[0][0])
                )
            ],
        }
        await session.execute(
            insert(delivery_receipts).values(
                delivery_receipt_id=receipt_id,
                confirmation_id=command.confirmation_id,
                document_series_id=series_id,
                branch_id=delivery["branch_id"],
                series_number=series_number,
                number=receipt_number,
                snapshot=receipt_snapshot,
                document_status="pending_document",
            )
        )
        event_id = uuid4()
        await session.execute(
            insert(outbox_events).values(
                outbox_event_id=event_id,
                aggregate_type="delivery",
                aggregate_id=delivery_id,
                event_type="delivery.confirmed.v1",
                payload={
                    "delivery_id": str(delivery_id),
                    "confirmation_id": str(command.confirmation_id),
                    "delivery_receipt_id": str(receipt_id),
                },
                correlation_id=request.state.correlation_id,
            )
        )
        await session.execute(insert(outbox_processing_state).values(outbox_event_id=event_id))
        total = sum((cast(Decimal, line["quantity_base"]) for line in lines), ZERO)
        await session.execute(
            update(delivery_state)
            .where(delivery_state.c.delivery_id == delivery_id)
            .values(status="confirmed", version=next_version, updated_at=func.now())
        )
        await session.execute(
            update(fulfillment_order_state)
            .where(
                fulfillment_order_state.c.fulfillment_order_id == delivery["fulfillment_order_id"]
            )
            .values(
                status="delivered",
                delivered_quantity_base=fulfillment_order_state.c.delivered_quantity_base + total,
                version=fulfillment_order_state.c.version + 1,
                updated_at=func.now(),
            )
        )
        result = DeliveryConfirmationResponse(
            confirmation_id=command.confirmation_id,
            delivery_id=delivery_id,
            status="confirmed",
            version=next_version,
            lines=[
                ConfirmationLineResponse(
                    line_id=line_id,
                    sku_id=sku_id,
                    accepted_quantity_base=data["quantity"],
                    unit_cost=data["unit_cost"],
                    value_delta=data["value"],
                    outbound_movement_id=data["movement_id"],
                )
                for (line_id, sku_id), data in sorted(
                    response_lines.items(), key=lambda item: str(item[0][0])
                )
            ],
            delivery_receipt=DeliveryReceiptResponse(
                delivery_receipt_id=receipt_id,
                number=receipt_number,
                status="pending_document",
            ),
            outbox_event_id=event_id,
        )
        await store_command_result(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result=result,
        )
        return result
