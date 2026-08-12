from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256
from typing import Annotated, Any, Literal, TypedDict, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import exists, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from tradeflow_api.auth import (
    AuthorizedUser,
    require_delivery_correction_authorizer,
    require_delivery_correction_reader,
    require_delivery_correction_requester,
)
from tradeflow_api.command_receipts import get_command_replay, store_command_result
from tradeflow_api.database import get_database_session
from tradeflow_api.errors import AppError, error_responses
from tradeflow_api.models import (
    approval_authorities,
    companies,
    delivery_confirmation_evidence,
    delivery_confirmation_identity_partitions,
    delivery_confirmation_lines,
    delivery_confirmations,
    delivery_correction_authorizations,
    delivery_correction_evidence,
    delivery_correction_identity_positions,
    delivery_correction_lines,
    delivery_correction_movement_effects,
    delivery_corrections,
    delivery_dispatches,
    delivery_evidence,
    delivery_exception_case_evidence,
    delivery_exception_cases,
    delivery_exception_events,
    delivery_exception_state,
    delivery_line_identity_allocations,
    delivery_lines,
    delivery_receipt_documents,
    delivery_receipts,
    document_series,
    document_series_number_audit,
    draft_invoice_lines,
    draft_invoices,
    inventory_availability,
    inventory_valuation,
    lot_identities,
    outbox_events,
    outbox_processing_state,
    pick_identity_assignments,
    skus,
    stock_movement_identity_allocations,
    stock_movements,
    stock_serial_allocations,
    warehouse_stock_locations,
)
from tradeflow_api.money import currency_quantum, scale_invoice_line_amounts

router = APIRouter(tags=["delivery corrections"])
ZERO = Decimal("0")
SIX_PLACES = Decimal("0.000001")
OUTCOME_FIELDS = (
    "accepted_quantity_base",
    "refused_quantity_base",
    "damaged_quantity_base",
    "short_missing_quantity_base",
    "still_undelivered_quantity_base",
)
NonBlankReason = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
]


def _id(kind: str, source: UUID | str) -> UUID:
    return uuid5(NAMESPACE_URL, f"tradeflow:{kind}:{source}")


class CommandModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CorrectionIdentityPosition(CommandModel):
    delivery_line_identity_allocation_id: UUID
    accepted_quantity_base: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    refused_quantity_base: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    damaged_quantity_base: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    short_missing_quantity_base: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    still_undelivered_quantity_base: Decimal = Field(ge=0, max_digits=18, decimal_places=6)


class CorrectionLine(CommandModel):
    delivery_line_id: UUID
    accepted_quantity_base: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    refused_quantity_base: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    damaged_quantity_base: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    short_missing_quantity_base: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    still_undelivered_quantity_base: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    identity_positions: list[CorrectionIdentityPosition] = Field(default_factory=list)


class RequestDeliveryCorrection(CommandModel):
    correction_id: UUID
    reason: NonBlankReason
    evidence_ids: list[UUID] = Field(min_length=1)
    lines: list[CorrectionLine] = Field(min_length=1)


class AuthorizeDeliveryCorrection(CommandModel):
    expected_correction_version: int = Field(gt=0)


class StockEffect(BaseModel):
    status: Literal["pending", "posted"]
    original_movement_ids: list[UUID]
    reversal_movement_ids: list[UUID]
    replacement_movement_ids: list[UUID]


class DraftInvoiceEffect(BaseModel):
    status: Literal["pending", "completed"]
    original_draft_invoice_id: UUID
    reversal_draft_invoice_id: UUID
    replacement_draft_invoice_id: UUID | None


class ReceiptEffect(BaseModel):
    original_delivery_receipt_id: UUID
    original_number: str
    replacement_delivery_receipt_id: UUID | None
    replacement_number: str | None
    replacement_document_status: Literal["pending_document", "ready", "unavailable"] | None


class DeliveryCorrectionResponse(BaseModel):
    correction_id: UUID
    original_delivery_receipt_id: UUID
    confirmation_id: UUID
    delivery_id: UUID
    branch_id: UUID
    warehouse_id: UUID
    status: Literal["pending_authorization", "posted"]
    version: int
    reason: str
    requested_by: str
    requested_at: datetime
    authorized_by: str | None
    authorized_at: datetime | None
    affected_value_base_currency: Decimal
    base_currency: str
    evidence_ids: list[UUID]
    lines: list[CorrectionLine]
    stock_effect: StockEffect
    draft_invoice_effect: DraftInvoiceEffect
    receipt_effect: ReceiptEffect
    outbox_event_id: UUID | None


class DeliveryCorrectionSummary(BaseModel):
    correction_id: UUID
    original_delivery_receipt_id: UUID
    delivery_id: UUID
    branch_id: UUID
    warehouse_id: UUID
    status: Literal["pending_authorization", "posted"]
    version: int
    reason: str
    requested_by: str
    requested_at: datetime
    authorized_by: str | None
    authorized_at: datetime | None
    affected_value_base_currency: Decimal
    base_currency: str


class DeliveryCorrectionList(BaseModel):
    items: list[DeliveryCorrectionSummary]
    total: int


class DeliveryReceiptCorrectionContext(TypedDict):
    correction_status: Literal["current", "corrected", "replacement"]
    correction_id: UUID | None
    created_by_correction_id: UUID | None
    superseded_by_correction_id: UUID | None
    corrects_delivery_receipt_id: UUID | None
    replacement_delivery_receipt_id: UUID | None
    confirmation_lines: list[dict[str, object]]
    evidence_ids: list[UUID]


async def delivery_receipt_correction_context(
    session: AsyncSession,
    receipt: Mapping[str, Any],
) -> DeliveryReceiptCorrectionContext:
    """Return correction links and physical source lines without changing a receipt snapshot."""
    receipt_id = cast(UUID, receipt["delivery_receipt_id"])
    correction = (
        (
            await session.execute(
                select(
                    delivery_corrections.c.correction_id,
                    delivery_correction_authorizations.c.correction_id.label("authorization_id"),
                )
                .outerjoin(
                    delivery_correction_authorizations,
                    delivery_corrections.c.correction_id
                    == delivery_correction_authorizations.c.correction_id,
                )
                .where(delivery_corrections.c.original_delivery_receipt_id == receipt_id)
            )
        )
        .mappings()
        .one_or_none()
    )
    own_correction_id = cast(UUID | None, receipt.get("correction_id"))
    successor_id = cast(UUID, correction["correction_id"]) if correction is not None else None
    successor_posted = correction is not None and correction["authorization_id"] is not None
    correction_id = successor_id or own_correction_id
    replacement_id = await session.scalar(
        select(delivery_receipts.c.delivery_receipt_id).where(
            delivery_receipts.c.corrects_delivery_receipt_id == receipt_id
        )
    )
    status: Literal["current", "corrected", "replacement"]
    if successor_posted:
        status = "corrected"
    elif own_correction_id is not None:
        status = "replacement"
    else:
        status = "current"

    confirmation_id = cast(UUID, receipt["confirmation_id"])
    source_rows = await _source_lines(
        session,
        confirmation_id,
        source_correction_id=own_correction_id,
    )
    confirmation_lines: list[dict[str, object]] = []
    for source in source_rows:
        partition_table = (
            delivery_correction_identity_positions
            if own_correction_id is not None
            else delivery_confirmation_identity_partitions
        )
        partition_owner = (
            partition_table.c.correction_line_id == source["correction_line_id"]
            if own_correction_id is not None
            else partition_table.c.confirmation_line_id == source["confirmation_line_id"]
        )
        identity_rows = list(
            (
                await session.execute(
                    select(
                        delivery_line_identity_allocations.c.allocation_id,
                        delivery_line_identity_allocations.c.quantity_base,
                        pick_identity_assignments.c.tracking_policy,
                        lot_identities.c.lot_code,
                        lot_identities.c.expiration_date.label("lot_expiration_date"),
                        stock_serial_allocations.c.serial_number,
                        stock_serial_allocations.c.expiration_date.label("serial_expiration_date"),
                        *[partition_table.c[field] for field in OUTCOME_FIELDS],
                    )
                    .join(
                        pick_identity_assignments,
                        delivery_line_identity_allocations.c.pick_identity_assignment_id
                        == pick_identity_assignments.c.pick_identity_assignment_id,
                    )
                    .join(
                        partition_table,
                        partition_table.c["delivery_line_identity_allocation_id"]
                        == delivery_line_identity_allocations.c.allocation_id,
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
                        == source["delivery_line_id"],
                        partition_owner,
                    )
                    .order_by(delivery_line_identity_allocations.c.allocation_id)
                )
            ).mappings()
        )
        identity_positions = [
            {
                "delivery_line_identity_allocation_id": item["allocation_id"],
                "tracking_policy": item["tracking_policy"],
                "lot_code": item["lot_code"],
                "serial_number": item["serial_number"],
                "expiration_date": item["lot_expiration_date"] or item["serial_expiration_date"],
                "quantity_base": item["quantity_base"],
                **{field: item[field] for field in OUTCOME_FIELDS},
            }
            for item in identity_rows
        ]
        confirmation_lines.append(
            {
                "delivery_line_id": source["delivery_line_id"],
                "line_id": source["line_id"],
                "sku_id": source["sku_id"],
                **{field: source[field] for field in OUTCOME_FIELDS},
                "unit_cost": source["unit_cost"],
                "value_delta": source["value_delta"],
                "identity_positions": identity_positions,
            }
        )
    evidence_ids = list(
        await session.scalars(
            select(delivery_evidence.c.evidence_id)
            .join(
                delivery_confirmation_evidence,
                delivery_confirmation_evidence.c.evidence_id == delivery_evidence.c.evidence_id,
            )
            .where(delivery_confirmation_evidence.c.confirmation_id == confirmation_id)
            .order_by(delivery_evidence.c.evidence_id)
        )
    )
    if own_correction_id is not None:
        evidence_ids = sorted(
            set(evidence_ids).union(
                await session.scalars(
                    select(delivery_correction_evidence.c.evidence_id).where(
                        delivery_correction_evidence.c.correction_id == own_correction_id
                    )
                )
            )
        )
    return {
        "correction_status": status,
        "correction_id": correction_id,
        "created_by_correction_id": own_correction_id,
        "superseded_by_correction_id": successor_id if successor_posted else None,
        "corrects_delivery_receipt_id": receipt.get("corrects_delivery_receipt_id"),
        "replacement_delivery_receipt_id": replacement_id,
        "confirmation_lines": confirmation_lines,
        "evidence_ids": evidence_ids,
    }


async def _lock(session: AsyncSession, key: str) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": key}
    )


def _request_hash(kind: str, source: UUID, actor: str, command: BaseModel) -> str:
    raw = f"{kind}:{source}:{actor}:{command.model_dump_json(exclude_none=False)}"
    return sha256(raw.encode()).hexdigest()


async def _source_receipt(
    session: AsyncSession, receipt_id: UUID, actor: AuthorizedUser, *, for_update: bool
) -> Mapping[str, Any]:
    query = (
        select(
            delivery_receipts,
            delivery_receipt_documents.c.status.label("document_status"),
            delivery_dispatches.c.delivery_id,
            delivery_dispatches.c.warehouse_id,
            delivery_dispatches.c.sales_order_revision_id,
        )
        .join(
            delivery_confirmations,
            delivery_receipts.c.confirmation_id == delivery_confirmations.c.confirmation_id,
        )
        .join(
            delivery_dispatches,
            delivery_confirmations.c.delivery_id == delivery_dispatches.c.delivery_id,
        )
        .join(
            delivery_receipt_documents,
            delivery_receipts.c.delivery_receipt_id
            == delivery_receipt_documents.c.delivery_receipt_id,
        )
        .where(delivery_receipts.c.delivery_receipt_id == receipt_id)
    )
    if for_update:
        query = query.with_for_update(of=delivery_receipts)
    row = (await session.execute(query)).mappings().one_or_none()
    if row is None:
        raise AppError(404, "delivery_receipt_not_found", "Delivery Receipt does not exist.")
    if row["branch_id"] not in actor.branch_ids or row["warehouse_id"] not in actor.warehouse_ids:
        raise AppError(
            403, "operational_scope_required", "Branch and Warehouse scope are required."
        )
    return cast(Mapping[str, Any], row)


async def _source_lines(
    session: AsyncSession,
    confirmation_id: UUID,
    *,
    source_correction_id: UUID | None = None,
) -> list[Mapping[str, Any]]:
    if source_correction_id is not None:
        rows = (
            (
                await session.execute(
                    select(
                        delivery_correction_lines,
                        skus.c.tracking_policy,
                    )
                    .join(skus, delivery_correction_lines.c.sku_id == skus.c.sku_id)
                    .where(delivery_correction_lines.c.correction_id == source_correction_id)
                    .order_by(delivery_correction_lines.c.delivery_line_id)
                )
            )
            .mappings()
            .all()
        )
        accepted_movements = dict(
            (
                row.correction_line_id,
                row.movement_id,
            )
            for row in (
                await session.execute(
                    select(
                        delivery_correction_movement_effects.c.correction_line_id,
                        delivery_correction_movement_effects.c.movement_id,
                    ).where(
                        delivery_correction_movement_effects.c.correction_id
                        == source_correction_id,
                        delivery_correction_movement_effects.c.effect_role == "replacement",
                        delivery_correction_movement_effects.c.outcome == "accepted",
                    )
                )
            )
        )
        return [
            {
                **dict(row),
                "dispatched_quantity_base": sum(
                    (cast(Decimal, row[field]) for field in OUTCOME_FIELDS), ZERO
                ),
                "outbound_movement_id": accepted_movements.get(row["correction_line_id"]),
            }
            for row in rows
        ]
    rows = (
        (
            await session.execute(
                select(
                    delivery_confirmation_lines,
                    delivery_lines.c.quantity_base.label("dispatched_quantity_base"),
                    skus.c.tracking_policy,
                )
                .join(
                    delivery_lines,
                    delivery_confirmation_lines.c.delivery_line_id
                    == delivery_lines.c.delivery_line_id,
                )
                .join(skus, delivery_confirmation_lines.c.sku_id == skus.c.sku_id)
                .where(delivery_confirmation_lines.c.confirmation_id == confirmation_id)
                .order_by(delivery_confirmation_lines.c.delivery_line_id)
            )
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


async def _validate_identity_positions(
    session: AsyncSession, source: Mapping[str, Any], command: CorrectionLine
) -> None:
    allocations = list(
        (
            await session.execute(
                select(
                    delivery_line_identity_allocations.c.allocation_id,
                    delivery_line_identity_allocations.c.quantity_base,
                    pick_identity_assignments.c.tracking_policy,
                )
                .join(
                    pick_identity_assignments,
                    delivery_line_identity_allocations.c.pick_identity_assignment_id
                    == pick_identity_assignments.c.pick_identity_assignment_id,
                )
                .where(
                    delivery_line_identity_allocations.c.delivery_line_id
                    == source["delivery_line_id"]
                )
            )
        ).mappings()
    )
    if source["tracking_policy"] == "untracked":
        if command.identity_positions:
            raise AppError(
                409,
                "delivery_correction_identity_conflict",
                "Untracked stock has no identity positions.",
            )
        return
    supplied = {
        item.delivery_line_identity_allocation_id: item for item in command.identity_positions
    }
    expected = {row["allocation_id"] for row in allocations}
    if len(supplied) != len(command.identity_positions) or set(supplied) != expected:
        raise AppError(
            409,
            "delivery_correction_identity_conflict",
            "Every tracked identity must be partitioned exactly once.",
        )
    totals = {field: ZERO for field in OUTCOME_FIELDS}
    by_id = {row["allocation_id"]: row for row in allocations}
    for allocation_id, item in supplied.items():
        quantities = [cast(Decimal, getattr(item, field)) for field in OUTCOME_FIELDS]
        if sum(quantities, ZERO) != by_id[allocation_id]["quantity_base"]:
            raise AppError(
                409,
                "delivery_correction_identity_conflict",
                "Identity quantity must be partitioned exactly.",
            )
        if (
            by_id[allocation_id]["tracking_policy"] == "serial"
            and sum(q > ZERO for q in quantities) != 1
        ):
            raise AppError(
                409,
                "delivery_correction_identity_conflict",
                "A serial must remain whole in one outcome.",
            )
        for field in OUTCOME_FIELDS:
            totals[field] += cast(Decimal, getattr(item, field))
    if any(totals[field] != cast(Decimal, getattr(command, field)) for field in OUTCOME_FIELDS):
        raise AppError(
            409,
            "delivery_correction_identity_conflict",
            "Identity outcomes must equal the corrected line.",
        )


async def _assert_source_eligible(
    session: AsyncSession,
    confirmation_id: UUID,
    source_correction_id: UUID | None,
) -> RowMapping:
    if source_correction_id is None:
        invoice_query = (
            select(draft_invoices)
            .where(
                draft_invoices.c.delivery_confirmation_id == confirmation_id,
                draft_invoices.c.invoice_kind == "original",
            )
            .with_for_update()
        )
    else:
        invoice_query = (
            select(draft_invoices)
            .where(
                draft_invoices.c.draft_invoice_id
                == select(delivery_corrections.c.replacement_draft_invoice_id)
                .where(delivery_corrections.c.correction_id == source_correction_id)
                .scalar_subquery()
            )
            .with_for_update()
        )
    invoice = (await session.execute(invoice_query)).mappings().one_or_none()
    if invoice is None:
        raise AppError(
            409,
            "delivery_correction_draft_invoice_pending",
            "The source Draft Invoice is not ready.",
        )
    if invoice["status"] != "draft":
        raise AppError(
            409, "delivery_correction_not_eligible", "Only a Draft Invoice source may be corrected."
        )
    source_case_filter = (
        delivery_exception_cases.c.confirmation_line_id.in_(
            select(delivery_confirmation_lines.c.confirmation_line_id).where(
                delivery_confirmation_lines.c.confirmation_id == confirmation_id
            )
        )
        & delivery_exception_cases.c.correction_line_id.is_(None)
        if source_correction_id is None
        else delivery_exception_cases.c.correction_line_id.in_(
            select(delivery_correction_lines.c.correction_line_id).where(
                delivery_correction_lines.c.correction_id == source_correction_id
            )
        )
    )
    acted = await session.scalar(
        select(
            exists().where(
                source_case_filter,
                exists().where(
                    delivery_exception_events.c.exception_case_id
                    == delivery_exception_cases.c.exception_case_id,
                    delivery_exception_events.c.event_type != "opened",
                ),
            )
        )
    )
    if acted:
        raise AppError(
            409, "delivery_correction_not_eligible", "Exception custody has downstream actions."
        )
    return invoice


async def _assert_eligible(
    session: AsyncSession,
    receipt: Mapping[str, Any],
    *,
    exclude_correction_id: UUID | None = None,
) -> RowMapping:
    successor_query = select(delivery_corrections.c.correction_id).where(
        delivery_corrections.c.original_delivery_receipt_id == receipt["delivery_receipt_id"]
    )
    if exclude_correction_id is not None:
        successor_query = successor_query.where(
            delivery_corrections.c.correction_id != exclude_correction_id
        )
    successor = await session.scalar(successor_query)
    if successor is not None:
        raise AppError(
            409, "delivery_correction_chain_conflict", "Only the current receipt may be corrected."
        )
    return await _assert_source_eligible(
        session,
        cast(UUID, receipt["confirmation_id"]),
        cast(UUID | None, receipt.get("correction_id")),
    )


async def _response(session: AsyncSession, correction_id: UUID) -> DeliveryCorrectionResponse:
    row = (
        (
            await session.execute(
                select(
                    delivery_corrections,
                    delivery_correction_authorizations.c.authorized_by,
                    delivery_correction_authorizations.c.authorized_at,
                    delivery_receipts.c.number.label("original_number"),
                    delivery_receipts.c.correction_id.label("source_correction_id"),
                )
                .join(
                    delivery_receipts,
                    delivery_corrections.c.original_delivery_receipt_id
                    == delivery_receipts.c.delivery_receipt_id,
                )
                .outerjoin(
                    delivery_correction_authorizations,
                    delivery_corrections.c.correction_id
                    == delivery_correction_authorizations.c.correction_id,
                )
                .where(delivery_corrections.c.correction_id == correction_id)
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise AppError(404, "delivery_correction_not_found", "Delivery Correction does not exist.")
    line_rows = list(
        (
            await session.execute(
                select(delivery_correction_lines)
                .where(delivery_correction_lines.c.correction_id == correction_id)
                .order_by(delivery_correction_lines.c.delivery_line_id)
            )
        ).mappings()
    )
    positions = (
        list(
            (
                await session.execute(
                    select(delivery_correction_identity_positions).where(
                        delivery_correction_identity_positions.c.correction_line_id.in_(
                            [line["correction_line_id"] for line in line_rows]
                        )
                    )
                )
            ).mappings()
        )
        if line_rows
        else []
    )
    by_line: dict[UUID, list[CorrectionIdentityPosition]] = {}
    for position in positions:
        by_line.setdefault(position["correction_line_id"], []).append(
            CorrectionIdentityPosition(
                **{
                    key: position[key]
                    for key in ("delivery_line_identity_allocation_id", *OUTCOME_FIELDS)
                }
            )
        )
    lines = [
        CorrectionLine(
            delivery_line_id=line["delivery_line_id"],
            **{field: line[field] for field in OUTCOME_FIELDS},
            identity_positions=by_line.get(line["correction_line_id"], []),
        )
        for line in line_rows
    ]
    evidence_ids = list(
        await session.scalars(
            select(delivery_correction_evidence.c.evidence_id)
            .where(delivery_correction_evidence.c.correction_id == correction_id)
            .order_by(delivery_correction_evidence.c.evidence_id)
        )
    )
    effects = list(
        (
            await session.execute(
                select(delivery_correction_movement_effects).where(
                    delivery_correction_movement_effects.c.correction_id == correction_id
                )
            )
        ).mappings()
    )
    authorized = row["authorized_by"] is not None
    source_correction_id = cast(UUID | None, row["source_correction_id"])
    if source_correction_id is None:
        planned_originals = [
            line["outbound_movement_id"]
            for line in await _source_lines(session, row["confirmation_id"])
            if line["outbound_movement_id"] is not None
        ]
    else:
        planned_originals = list(
            await session.scalars(
                select(delivery_correction_movement_effects.c.movement_id).where(
                    delivery_correction_movement_effects.c.correction_id == source_correction_id,
                    delivery_correction_movement_effects.c.effect_role == "replacement",
                )
            )
        )
    originals = [
        item["movement_id"] for item in effects if item["effect_role"] == "original"
    ] or planned_originals
    reversals = [item["movement_id"] for item in effects if item["effect_role"] == "reversal"]
    replacements = [item["movement_id"] for item in effects if item["effect_role"] == "replacement"]
    replacement = (
        await session.execute(
            select(
                delivery_receipts.c.delivery_receipt_id,
                delivery_receipts.c.number,
                delivery_receipt_documents.c.status,
            )
            .join(
                delivery_receipt_documents,
                delivery_receipts.c.delivery_receipt_id
                == delivery_receipt_documents.c.delivery_receipt_id,
            )
            .where(delivery_receipts.c.correction_id == correction_id)
        )
    ).one_or_none()
    event_id = await session.scalar(
        select(outbox_events.c.outbox_event_id).where(
            outbox_events.c.aggregate_type == "delivery_correction",
            outbox_events.c.aggregate_id == correction_id,
            outbox_events.c.event_type == "delivery.correction.posted.v1",
        )
    )
    reversal_done = await session.scalar(
        select(
            exists().where(draft_invoices.c.draft_invoice_id == row["reversal_draft_invoice_id"])
        )
    )
    replacement_invoice_id = cast(UUID | None, row["replacement_draft_invoice_id"])
    replacement_done = replacement_invoice_id is None or bool(
        await session.scalar(
            select(exists().where(draft_invoices.c.draft_invoice_id == replacement_invoice_id))
        )
    )
    return DeliveryCorrectionResponse(
        correction_id=correction_id,
        original_delivery_receipt_id=row["original_delivery_receipt_id"],
        confirmation_id=row["confirmation_id"],
        delivery_id=row["delivery_id"],
        branch_id=row["branch_id"],
        warehouse_id=row["warehouse_id"],
        status="posted" if authorized else "pending_authorization",
        version=2 if authorized else 1,
        reason=row["reason"],
        requested_by=row["requested_by"],
        requested_at=row["requested_at"],
        authorized_by=row["authorized_by"],
        authorized_at=row["authorized_at"],
        affected_value_base_currency=row["affected_value_base_currency"],
        base_currency=row["base_currency"],
        evidence_ids=evidence_ids,
        lines=lines,
        stock_effect=StockEffect(
            status="posted" if authorized else "pending",
            original_movement_ids=originals,
            reversal_movement_ids=reversals,
            replacement_movement_ids=replacements,
        ),
        draft_invoice_effect=DraftInvoiceEffect(
            status="completed" if reversal_done and replacement_done else "pending",
            original_draft_invoice_id=row["original_draft_invoice_id"],
            reversal_draft_invoice_id=row["reversal_draft_invoice_id"],
            replacement_draft_invoice_id=row["replacement_draft_invoice_id"],
        ),
        receipt_effect=ReceiptEffect(
            original_delivery_receipt_id=row["original_delivery_receipt_id"],
            original_number=row["original_number"],
            replacement_delivery_receipt_id=replacement.delivery_receipt_id
            if replacement
            else None,
            replacement_number=replacement.number if replacement else None,
            replacement_document_status=replacement.status if replacement else None,
        ),
        outbox_event_id=event_id,
    )


@router.post(
    "/v1/delivery-receipts/{receipt_id}/corrections",
    response_model=DeliveryCorrectionResponse,
    status_code=201,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def request_delivery_correction(
    receipt_id: UUID,
    command: RequestDeliveryCorrection,
    request: Request,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_delivery_correction_requester)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200, pattern=r".*\S.*"),
    ] = None,
) -> DeliveryCorrectionResponse:
    if not idempotency_key:
        raise AppError(400, "idempotency_key_required", "Idempotency-Key is required.")
    request_hash = _request_hash("request-delivery-correction", receipt_id, actor.subject, command)
    await session.rollback()
    async with session.begin():
        await _lock(session, f"delivery-receipt-correction:{receipt_id}")
        receipt = await _source_receipt(session, receipt_id, actor, for_update=True)
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.status_code = 200
            return DeliveryCorrectionResponse.model_validate(replay)
        invoice = await _assert_eligible(session, receipt)
        verified = set(
            await session.scalars(
                select(delivery_evidence.c.evidence_id).where(
                    delivery_evidence.c.delivery_id == receipt["delivery_id"],
                    delivery_evidence.c.status == "verified",
                    delivery_evidence.c.evidence_id.in_(command.evidence_ids),
                )
            )
        )
        if verified != set(command.evidence_ids) or len(verified) != len(command.evidence_ids):
            raise AppError(
                409,
                "delivery_correction_evidence_invalid",
                "All correction evidence must be verified for this Delivery.",
            )
        sources = await _source_lines(
            session,
            receipt["confirmation_id"],
            source_correction_id=cast(UUID | None, receipt.get("correction_id")),
        )
        supplied = {line.delivery_line_id: line for line in command.lines}
        if len(supplied) != len(command.lines) or set(supplied) != {
            line["delivery_line_id"] for line in sources
        }:
            raise AppError(
                409,
                "delivery_correction_partition_conflict",
                "Every Delivery Line must be corrected exactly once.",
            )
        inventory_value_delta = ZERO
        replacement_invoice_total = ZERO
        corrected_accepted_by_line: defaultdict[UUID, Decimal] = defaultdict(lambda: ZERO)
        root_invoice = invoice
        while root_invoice["replaces_draft_invoice_id"] is not None:
            root_invoice = (
                (
                    await session.execute(
                        select(draft_invoices).where(
                            draft_invoices.c.draft_invoice_id
                            == root_invoice["replaces_draft_invoice_id"]
                        )
                    )
                )
                .mappings()
                .one()
            )
        root_invoice_lines = {
            row["line_id"]: row
            for row in (
                await session.execute(
                    select(draft_invoice_lines).where(
                        draft_invoice_lines.c.draft_invoice_id == root_invoice["draft_invoice_id"]
                    )
                )
            ).mappings()
        }
        quantum = currency_quantum(cast(str, invoice["currency"]))
        for source in sources:
            line = supplied[source["delivery_line_id"]]
            if (
                sum((cast(Decimal, getattr(line, field)) for field in OUTCOME_FIELDS), ZERO)
                != source["dispatched_quantity_base"]
            ):
                raise AppError(
                    409,
                    "delivery_correction_partition_conflict",
                    "Corrected outcomes must equal dispatched quantity.",
                )
            await _validate_identity_positions(session, source, line)
            inventory_value_delta += (
                (cast(Decimal, source["accepted_quantity_base"]) - line.accepted_quantity_base)
                * cast(Decimal, source["unit_cost"])
            ).quantize(SIX_PLACES, ROUND_HALF_UP)
            corrected_accepted_by_line[cast(UUID, source["line_id"])] += line.accepted_quantity_base
        for logical_line_id, source_invoice_line in root_invoice_lines.items():
            original_accepted = cast(Decimal, source_invoice_line["accepted_quantity_base"])
            amounts = scale_invoice_line_amounts(
                source_quantity=original_accepted,
                replacement_quantity=corrected_accepted_by_line[cast(UUID, logical_line_id)],
                source_subtotal=cast(Decimal, source_invoice_line["subtotal"]),
                source_discount=cast(Decimal, source_invoice_line["discount_amount"]),
                source_tax=cast(Decimal, source_invoice_line["tax_amount"]),
                quantum=quantum,
            )
            replacement_invoice_total += amounts.total
        draft_effect = abs(
            cast(Decimal, invoice["grand_total"]) - replacement_invoice_total
        ).quantize(SIX_PLACES, ROUND_HALF_UP)
        inventory_effect = abs(inventory_value_delta).quantize(SIX_PLACES, ROUND_HALF_UP)
        affected = max(inventory_effect, draft_effect)
        has_replacement_invoice = any(line.accepted_quantity_base > ZERO for line in command.lines)
        await session.execute(
            insert(delivery_corrections).values(
                correction_id=command.correction_id,
                original_delivery_receipt_id=receipt_id,
                delivery_id=receipt["delivery_id"],
                confirmation_id=receipt["confirmation_id"],
                branch_id=receipt["branch_id"],
                warehouse_id=receipt["warehouse_id"],
                reason=command.reason,
                requested_by=actor.subject,
                correlation_id=request.state.correlation_id,
                idempotency_key=idempotency_key,
                base_currency=cast(
                    str, await session.scalar(select(companies.c.base_currency).limit(1))
                ),
                affected_inventory_value=inventory_effect,
                affected_draft_invoice_value=draft_effect,
                affected_value_base_currency=affected,
                original_draft_invoice_id=invoice["draft_invoice_id"],
                reversal_draft_invoice_id=_id(
                    "delivery-correction-invoice-reversal", command.correction_id
                ),
                replacement_draft_invoice_id=(
                    _id("delivery-correction-invoice-replacement", command.correction_id)
                    if has_replacement_invoice
                    else None
                ),
            )
        )
        line_rows = []
        position_rows = []
        for source in sources:
            line = supplied[source["delivery_line_id"]]
            correction_line_id = _id(
                "delivery-correction-line", f"{command.correction_id}:{line.delivery_line_id}"
            )
            line_rows.append(
                {
                    "correction_line_id": correction_line_id,
                    "correction_id": command.correction_id,
                    "confirmation_line_id": source["confirmation_line_id"],
                    "delivery_line_id": line.delivery_line_id,
                    "line_id": source["line_id"],
                    "sku_id": source["sku_id"],
                    **{field: getattr(line, field) for field in OUTCOME_FIELDS},
                    "unit_cost": source["unit_cost"],
                    "value_delta": -(
                        line.accepted_quantity_base * cast(Decimal, source["unit_cost"])
                    ).quantize(SIX_PLACES, ROUND_HALF_UP),
                }
            )
            for position in line.identity_positions:
                position_rows.append(
                    {
                        "correction_identity_position_id": _id(
                            "delivery-correction-identity",
                            f"{correction_line_id}:{position.delivery_line_identity_allocation_id}",
                        ),
                        "correction_line_id": correction_line_id,
                        **position.model_dump(),
                    }
                )
        await session.execute(insert(delivery_correction_lines), line_rows)
        if position_rows:
            await session.execute(insert(delivery_correction_identity_positions), position_rows)
        await session.execute(
            insert(delivery_correction_evidence),
            [
                {"correction_id": command.correction_id, "evidence_id": evidence_id}
                for evidence_id in command.evidence_ids
            ],
        )
        await session.execute(
            update(delivery_corrections)
            .where(delivery_corrections.c.correction_id == command.correction_id)
            .values(sealed_at=func.now())
        )
        result = await _response(session, command.correction_id)
        await store_command_result(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result=result,
        )
    return result


async def _add_untracked_position(
    session: AsyncSession, *, sku_id: UUID, warehouse_id: UUID, location_id: UUID, quantity: Decimal
) -> None:
    if quantity == ZERO:
        return
    await session.execute(
        pg_insert(inventory_availability)
        .values(
            sku_id=sku_id,
            warehouse_id=warehouse_id,
            location_id=location_id,
            identity_key="",
            lot_code=None,
            serial_numbers=[],
            expiration_date=None,
            on_hand=quantity,
            reserved=ZERO,
        )
        .on_conflict_do_update(
            index_elements=["sku_id", "warehouse_id", "location_id", "identity_key"],
            set_={"on_hand": inventory_availability.c.on_hand + quantity},
        )
    )


async def _identity_metadata(
    session: AsyncSession, delivery_line_id: UUID
) -> dict[UUID, Mapping[str, Any]]:
    rows = (
        await session.execute(
            select(
                delivery_line_identity_allocations.c.allocation_id,
                delivery_line_identity_allocations.c.quantity_base,
                pick_identity_assignments.c.tracking_policy,
                lot_identities.c.lot_code,
                lot_identities.c.expiration_date.label("lot_expiration_date"),
                stock_serial_allocations.c.serial_number,
                stock_serial_allocations.c.expiration_date.label("serial_expiration_date"),
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
            .where(delivery_line_identity_allocations.c.delivery_line_id == delivery_line_id)
        )
    ).mappings()
    return {cast(UUID, row["allocation_id"]): dict(row) for row in rows}


def _identity_values(identity: Mapping[str, Any]) -> tuple[str, str | None, list[str], Any]:
    is_serial = identity["tracking_policy"] == "serial"
    return (
        f"serial:{identity['serial_number']}" if is_serial else f"lot:{identity['lot_code']}",
        None if is_serial else cast(str, identity["lot_code"]),
        [cast(str, identity["serial_number"])] if is_serial else [],
        identity["serial_expiration_date"] if is_serial else identity["lot_expiration_date"],
    )


async def _add_identity_position(
    session: AsyncSession,
    *,
    sku_id: UUID,
    warehouse_id: UUID,
    location_id: UUID,
    identity: Mapping[str, Any],
    quantity: Decimal,
) -> None:
    if quantity == ZERO:
        return
    identity_key, lot_code, serials, expiration = _identity_values(identity)
    await session.execute(
        pg_insert(inventory_availability)
        .values(
            sku_id=sku_id,
            warehouse_id=warehouse_id,
            location_id=location_id,
            identity_key=identity_key,
            lot_code=lot_code,
            serial_numbers=serials,
            expiration_date=expiration,
            on_hand=quantity,
            reserved=ZERO,
        )
        .on_conflict_do_update(
            index_elements=["sku_id", "warehouse_id", "location_id", "identity_key"],
            set_={
                "on_hand": inventory_availability.c.on_hand + quantity,
                "serial_numbers": serials,
                "expiration_date": expiration,
            },
        )
    )


async def _remove_identity_position(
    session: AsyncSession,
    *,
    sku_id: UUID,
    warehouse_id: UUID,
    location_id: UUID,
    identity: Mapping[str, Any],
    quantity: Decimal,
) -> None:
    if quantity == ZERO:
        return
    identity_key, _, serials, _ = _identity_values(identity)
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
            "delivery_correction_inventory_conflict",
            "Tracked correction custody is no longer available.",
        )
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
            serial_numbers=sorted(set(position["serial_numbers"]) - set(serials)),
        )
    )


async def _move_identity_position(
    session: AsyncSession,
    *,
    sku_id: UUID,
    warehouse_id: UUID,
    source_location_id: UUID,
    destination_location_id: UUID,
    identity: Mapping[str, Any],
    quantity: Decimal,
) -> None:
    await _remove_identity_position(
        session,
        sku_id=sku_id,
        warehouse_id=warehouse_id,
        location_id=source_location_id,
        identity=identity,
        quantity=quantity,
    )
    await _add_identity_position(
        session,
        sku_id=sku_id,
        warehouse_id=warehouse_id,
        location_id=destination_location_id,
        identity=identity,
        quantity=quantity,
    )


async def _remove_untracked_position(
    session: AsyncSession, *, sku_id: UUID, warehouse_id: UUID, location_id: UUID, quantity: Decimal
) -> None:
    if quantity == ZERO:
        return
    row = (
        await session.execute(
            select(inventory_availability.c.on_hand)
            .where(
                inventory_availability.c.sku_id == sku_id,
                inventory_availability.c.warehouse_id == warehouse_id,
                inventory_availability.c.location_id == location_id,
                inventory_availability.c.identity_key == "",
            )
            .with_for_update()
        )
    ).one_or_none()
    if row is None or row.on_hand < quantity:
        raise AppError(
            409,
            "delivery_correction_inventory_conflict",
            "Correction custody is no longer available.",
        )
    await session.execute(
        update(inventory_availability)
        .where(
            inventory_availability.c.sku_id == sku_id,
            inventory_availability.c.warehouse_id == warehouse_id,
            inventory_availability.c.location_id == location_id,
            inventory_availability.c.identity_key == "",
        )
        .values(on_hand=inventory_availability.c.on_hand - quantity)
    )


async def _insert_movement(
    session: AsyncSession,
    *,
    movement_id: UUID,
    line: Mapping[str, Any],
    correction: Mapping[str, Any],
    location_id: UUID,
    quantity: Decimal,
    value_delta: Decimal,
    leg: str,
    role: str,
    outcome: str,
    original_id: UUID | None,
    actor: AuthorizedUser,
    correlation_id: str,
    idempotency_key: str,
    identity_allocations: list[tuple[UUID, Decimal]] | None = None,
) -> None:
    if quantity == ZERO:
        return
    await session.execute(
        insert(stock_movements).values(
            movement_id=movement_id,
            sku_id=line["sku_id"],
            warehouse_id=correction["warehouse_id"],
            location_id=location_id,
            movement_type="delivery_correction",
            quantity_base=quantity,
            unit_cost=line["unit_cost"],
            value_delta=value_delta,
            base_currency=correction["base_currency"],
            source_reference=f"DELIVERY-CORRECTION:{correction['correction_id']}",
            entered_unit="BASE",
            conversion_snapshot={"source": "delivery_correction", "factor": "1.000000"},
            actor_subject=actor.subject,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            movement_group_id=_id(
                "delivery-correction-movement-group",
                f"{correction['correction_id']}:{leg}:{line['delivery_line_id']}",
            ),
            movement_leg=leg,
            reversal_of_movement_id=original_id,
        )
    )
    if identity_allocations:
        await session.execute(
            insert(stock_movement_identity_allocations),
            [
                {
                    "allocation_id": _id(
                        "delivery-correction-movement-identity",
                        f"{movement_id}:{allocation_id}",
                    ),
                    "movement_id": movement_id,
                    "delivery_line_identity_allocation_id": allocation_id,
                    "quantity_base": quantity_base,
                }
                for allocation_id, quantity_base in identity_allocations
                if quantity_base > ZERO
            ],
        )
    await session.execute(
        insert(delivery_correction_movement_effects).values(
            movement_effect_id=_id("delivery-correction-effect", movement_id),
            correction_id=correction["correction_id"],
            correction_line_id=line["correction_line_id"],
            effect_role=role,
            outcome=outcome,
            movement_id=movement_id,
            original_movement_id=original_id,
        )
    )


async def _post_correction(
    session: AsyncSession,
    correction: Mapping[str, Any],
    actor: AuthorizedUser,
    correlation_id: str,
    approval_authority_id: UUID,
) -> tuple[UUID | None, UUID]:
    await session.execute(
        text("SELECT pg_advisory_xact_lock_shared(hashtext('inventory-projection-rebuild'))")
    )
    location_rows = (
        await session.execute(
            select(
                warehouse_stock_locations.c.custody, warehouse_stock_locations.c.location_id
            ).where(
                warehouse_stock_locations.c.warehouse_id == correction["warehouse_id"],
                warehouse_stock_locations.c.custody.in_(["in_transit", "investigation"]),
            )
        )
    ).all()
    locations: dict[str, UUID] = {
        cast(str, row.custody): cast(UUID, row.location_id) for row in location_rows
    }
    transit_id = locations["in_transit"]
    line_rows = (
        (
            await session.execute(
                select(
                    delivery_correction_lines,
                    delivery_confirmation_lines.c.outbound_movement_id,
                    delivery_confirmation_lines.c.accepted_quantity_base.label(
                        "original_accepted_quantity_base"
                    ),
                    skus.c.tracking_policy,
                )
                .join(
                    delivery_confirmation_lines,
                    delivery_correction_lines.c.confirmation_line_id
                    == delivery_confirmation_lines.c.confirmation_line_id,
                )
                .join(skus, delivery_correction_lines.c.sku_id == skus.c.sku_id)
                .where(delivery_correction_lines.c.correction_id == correction["correction_id"])
            )
        )
        .mappings()
        .all()
    )
    lines = [dict(row) for row in line_rows]
    source_correction_id = cast(
        UUID | None,
        await session.scalar(
            select(delivery_receipts.c.correction_id).where(
                delivery_receipts.c.delivery_receipt_id
                == correction["original_delivery_receipt_id"]
            )
        ),
    )
    prior_lines_by_delivery: dict[UUID, dict[str, Any]] = {}
    if source_correction_id is not None:
        prior_lines_by_delivery = {
            cast(UUID, row["delivery_line_id"]): dict(row)
            for row in (
                await session.execute(
                    select(delivery_correction_lines).where(
                        delivery_correction_lines.c.correction_id == source_correction_id
                    )
                )
            ).mappings()
        }
    replacement_short_movements: dict[UUID, tuple[UUID, UUID, UUID]] = {}
    for line in lines:
        source_line = prior_lines_by_delivery.get(cast(UUID, line["delivery_line_id"]))
        identity_meta = await _identity_metadata(session, line["delivery_line_id"])
        original_position_query = (
            select(delivery_confirmation_identity_partitions).where(
                delivery_confirmation_identity_partitions.c.confirmation_line_id
                == line["confirmation_line_id"]
            )
            if source_line is None
            else select(delivery_correction_identity_positions).where(
                delivery_correction_identity_positions.c.correction_line_id
                == source_line["correction_line_id"]
            )
        )
        original_positions = [
            dict(row) for row in (await session.execute(original_position_query)).mappings()
        ]
        corrected_positions = [
            dict(row)
            for row in (
                await session.execute(
                    select(delivery_correction_identity_positions).where(
                        delivery_correction_identity_positions.c.correction_line_id
                        == line["correction_line_id"]
                    )
                )
            ).mappings()
        ]

        def allocations(
            positions: Sequence[Mapping[str, Any]], outcome: str
        ) -> list[tuple[UUID, Decimal]]:
            return [
                (
                    cast(UUID, item["delivery_line_identity_allocation_id"]),
                    cast(Decimal, item[f"{outcome}_quantity_base"]),
                )
                for item in positions
                if cast(Decimal, item[f"{outcome}_quantity_base"]) > ZERO
            ]

        async def add_to_transit(
            quantity: Decimal,
            identity_quantities: list[tuple[UUID, Decimal]],
            *,
            sku_id: UUID,
            identity_metadata: dict[UUID, Mapping[str, Any]],
        ) -> None:
            if identity_metadata:
                for allocation_id, identity_quantity in identity_quantities:
                    await _add_identity_position(
                        session,
                        sku_id=sku_id,
                        warehouse_id=correction["warehouse_id"],
                        location_id=transit_id,
                        identity=identity_metadata[allocation_id],
                        quantity=identity_quantity,
                    )
            else:
                await _add_untracked_position(
                    session,
                    sku_id=sku_id,
                    warehouse_id=correction["warehouse_id"],
                    location_id=transit_id,
                    quantity=quantity,
                )

        async def remove_from_transit(
            quantity: Decimal,
            identity_quantities: list[tuple[UUID, Decimal]],
            *,
            sku_id: UUID,
            identity_metadata: dict[UUID, Mapping[str, Any]],
        ) -> None:
            if identity_metadata:
                for allocation_id, identity_quantity in identity_quantities:
                    await _remove_identity_position(
                        session,
                        sku_id=sku_id,
                        warehouse_id=correction["warehouse_id"],
                        location_id=transit_id,
                        identity=identity_metadata[allocation_id],
                        quantity=identity_quantity,
                    )
            else:
                await _remove_untracked_position(
                    session,
                    sku_id=sku_id,
                    warehouse_id=correction["warehouse_id"],
                    location_id=transit_id,
                    quantity=quantity,
                )

        async def move_short(
            quantity: Decimal,
            identity_quantities: list[tuple[UUID, Decimal]],
            source_id: UUID,
            destination_id: UUID,
            *,
            sku_id: UUID,
            identity_metadata: dict[UUID, Mapping[str, Any]],
        ) -> None:
            if identity_metadata:
                for allocation_id, identity_quantity in identity_quantities:
                    await _move_identity_position(
                        session,
                        sku_id=sku_id,
                        warehouse_id=correction["warehouse_id"],
                        source_location_id=source_id,
                        destination_location_id=destination_id,
                        identity=identity_metadata[allocation_id],
                        quantity=identity_quantity,
                    )
            else:
                await _remove_untracked_position(
                    session,
                    sku_id=sku_id,
                    warehouse_id=correction["warehouse_id"],
                    location_id=source_id,
                    quantity=quantity,
                )
                await _add_untracked_position(
                    session,
                    sku_id=sku_id,
                    warehouse_id=correction["warehouse_id"],
                    location_id=destination_id,
                    quantity=quantity,
                )

        original_qty = cast(
            Decimal,
            source_line["accepted_quantity_base"]
            if source_line is not None
            else line["original_accepted_quantity_base"],
        )
        line["source_accepted_quantity_base"] = original_qty
        original_id = (
            await session.scalar(
                select(delivery_correction_movement_effects.c.movement_id).where(
                    delivery_correction_movement_effects.c.correction_id == source_correction_id,
                    delivery_correction_movement_effects.c.correction_line_id
                    == source_line["correction_line_id"],
                    delivery_correction_movement_effects.c.effect_role == "replacement",
                    delivery_correction_movement_effects.c.outcome == "accepted",
                )
            )
            if source_line is not None
            else line["outbound_movement_id"]
        )
        if original_id is not None:
            reverse_id = _id(
                "delivery-correction-stock-reversal", f"{correction['correction_id']}:{original_id}"
            )
            value = (original_qty * line["unit_cost"]).quantize(SIX_PLACES, ROUND_HALF_UP)
            await _insert_movement(
                session,
                movement_id=reverse_id,
                line=line,
                correction=correction,
                location_id=transit_id,
                quantity=original_qty,
                value_delta=value,
                leg="correction_accepted_reversal_in",
                role="reversal",
                outcome="accepted",
                original_id=original_id,
                actor=actor,
                correlation_id=correlation_id,
                idempotency_key=f"delivery-correction:{correction['correction_id']}:{reverse_id}",
                identity_allocations=allocations(original_positions, "accepted"),
            )
            await session.execute(
                insert(delivery_correction_movement_effects).values(
                    movement_effect_id=_id("delivery-correction-effect-original", original_id),
                    correction_id=correction["correction_id"],
                    correction_line_id=line["correction_line_id"],
                    effect_role="original",
                    outcome="accepted",
                    movement_id=original_id,
                    original_movement_id=None,
                )
            )
            await add_to_transit(
                original_qty,
                allocations(original_positions, "accepted"),
                sku_id=line["sku_id"],
                identity_metadata=identity_meta,
            )

        short_case_filter = (
            delivery_exception_cases.c.correction_line_id == source_line["correction_line_id"]
            if source_line is not None
            else (
                (delivery_exception_cases.c.confirmation_line_id == line["confirmation_line_id"])
                & delivery_exception_cases.c.correction_line_id.is_(None)
            )
        )
        original_short_case = (
            (
                await session.execute(
                    select(delivery_exception_cases).where(
                        short_case_filter,
                        delivery_exception_cases.c.exception_kind == "short_missing",
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if original_short_case is not None:
            short_quantity = cast(Decimal, original_short_case["original_quantity_base"])
            short_value = (short_quantity * line["unit_cost"]).quantize(SIX_PLACES, ROUND_HALF_UP)
            original_out_id = cast(UUID, original_short_case["investigation_out_movement_id"])
            original_in_id = cast(UUID, original_short_case["investigation_in_movement_id"])
            reverse_transit_id = _id(
                "delivery-correction-stock-reversal",
                f"{correction['correction_id']}:{original_out_id}",
            )
            reverse_investigation_id = _id(
                "delivery-correction-stock-reversal",
                f"{correction['correction_id']}:{original_in_id}",
            )
            original_short_allocations = allocations(original_positions, "short_missing")
            await _insert_movement(
                session,
                movement_id=reverse_transit_id,
                line=line,
                correction=correction,
                location_id=transit_id,
                quantity=short_quantity,
                value_delta=short_value,
                leg="correction_exception_reversal_transit_in",
                role="reversal",
                outcome="short_missing",
                original_id=original_out_id,
                actor=actor,
                correlation_id=correlation_id,
                idempotency_key=(
                    f"delivery-correction:{correction['correction_id']}:{reverse_transit_id}"
                ),
                identity_allocations=original_short_allocations,
            )
            await _insert_movement(
                session,
                movement_id=reverse_investigation_id,
                line=line,
                correction=correction,
                location_id=locations["investigation"],
                quantity=short_quantity,
                value_delta=-short_value,
                leg="correction_exception_reversal_investigation_out",
                role="reversal",
                outcome="short_missing",
                original_id=original_in_id,
                actor=actor,
                correlation_id=correlation_id,
                idempotency_key=(
                    f"delivery-correction:{correction['correction_id']}:{reverse_investigation_id}"
                ),
                identity_allocations=original_short_allocations,
            )
            for original_movement_id in (original_out_id, original_in_id):
                await session.execute(
                    insert(delivery_correction_movement_effects).values(
                        movement_effect_id=_id(
                            "delivery-correction-effect-original", original_movement_id
                        ),
                        correction_id=correction["correction_id"],
                        correction_line_id=line["correction_line_id"],
                        effect_role="original",
                        outcome="short_missing",
                        movement_id=original_movement_id,
                        original_movement_id=None,
                    )
                )
            await move_short(
                short_quantity,
                original_short_allocations,
                locations["investigation"],
                transit_id,
                sku_id=line["sku_id"],
                identity_metadata=identity_meta,
            )

        corrected_qty = cast(Decimal, line["accepted_quantity_base"])
        if corrected_qty > ZERO:
            replacement_id = _id(
                "delivery-correction-stock-replacement",
                f"{correction['correction_id']}:{line['delivery_line_id']}:accepted",
            )
            value = (corrected_qty * line["unit_cost"]).quantize(SIX_PLACES, ROUND_HALF_UP)
            corrected_accepted_allocations = allocations(corrected_positions, "accepted")
            await remove_from_transit(
                corrected_qty,
                corrected_accepted_allocations,
                sku_id=line["sku_id"],
                identity_metadata=identity_meta,
            )
            await _insert_movement(
                session,
                movement_id=replacement_id,
                line=line,
                correction=correction,
                location_id=transit_id,
                quantity=corrected_qty,
                value_delta=-value,
                leg="correction_accepted_replacement_out",
                role="replacement",
                outcome="accepted",
                original_id=None,
                actor=actor,
                correlation_id=correlation_id,
                idempotency_key=(
                    f"delivery-correction:{correction['correction_id']}:{replacement_id}"
                ),
                identity_allocations=corrected_accepted_allocations,
            )

        corrected_short = cast(Decimal, line["short_missing_quantity_base"])
        if corrected_short > ZERO:
            movement_group_id = _id(
                "delivery-correction-short-group",
                f"{correction['correction_id']}:{line['delivery_line_id']}",
            )
            replacement_out_id = _id("delivery-correction-short-out", movement_group_id)
            replacement_in_id = _id("delivery-correction-short-in", movement_group_id)
            corrected_short_allocations = allocations(corrected_positions, "short_missing")
            short_value = (corrected_short * line["unit_cost"]).quantize(SIX_PLACES, ROUND_HALF_UP)
            await move_short(
                corrected_short,
                corrected_short_allocations,
                transit_id,
                locations["investigation"],
                sku_id=line["sku_id"],
                identity_metadata=identity_meta,
            )
            await _insert_movement(
                session,
                movement_id=replacement_out_id,
                line=line,
                correction=correction,
                location_id=transit_id,
                quantity=corrected_short,
                value_delta=-short_value,
                leg="correction_exception_replacement_transit_out",
                role="replacement",
                outcome="short_missing",
                original_id=None,
                actor=actor,
                correlation_id=correlation_id,
                idempotency_key=(
                    f"delivery-correction:{correction['correction_id']}:{replacement_out_id}"
                ),
                identity_allocations=corrected_short_allocations,
            )
            await _insert_movement(
                session,
                movement_id=replacement_in_id,
                line=line,
                correction=correction,
                location_id=locations["investigation"],
                quantity=corrected_short,
                value_delta=short_value,
                leg="correction_exception_replacement_investigation_in",
                role="replacement",
                outcome="short_missing",
                original_id=None,
                actor=actor,
                correlation_id=correlation_id,
                idempotency_key=(
                    f"delivery-correction:{correction['correction_id']}:{replacement_in_id}"
                ),
                identity_allocations=corrected_short_allocations,
            )
            replacement_short_movements[line["correction_line_id"]] = (
                movement_group_id,
                replacement_out_id,
                replacement_in_id,
            )
    for sku_id in {line["sku_id"] for line in lines}:
        valuation = (
            (
                await session.execute(
                    select(inventory_valuation)
                    .where(
                        inventory_valuation.c.sku_id == sku_id,
                        inventory_valuation.c.warehouse_id == correction["warehouse_id"],
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
                "delivery_correction_inventory_conflict",
                f"SKU valuation is not initialized for warehouse {correction['warehouse_id']}.",
            )
        sku_lines = [line for line in lines if line["sku_id"] == sku_id]
        quantity_delta = sum(
            (
                cast(Decimal, line["source_accepted_quantity_base"])
                - cast(Decimal, line["accepted_quantity_base"])
                for line in sku_lines
            ),
            ZERO,
        )
        value_delta = sum(
            (
                (
                    cast(Decimal, line["source_accepted_quantity_base"])
                    - cast(Decimal, line["accepted_quantity_base"])
                )
                * cast(Decimal, line["unit_cost"])
                for line in sku_lines
            ),
            ZERO,
        )
        new_quantity = valuation["quantity_on_hand"] + quantity_delta
        new_value = valuation["inventory_value"] + value_delta
        new_mac = (
            (new_value / new_quantity).quantize(SIX_PLACES, ROUND_HALF_UP)
            if new_quantity
            else valuation["moving_average_unit_cost"]
        )
        await session.execute(
            update(inventory_valuation)
            .where(
                inventory_valuation.c.sku_id == sku_id,
                inventory_valuation.c.warehouse_id == correction["warehouse_id"],
            )
            .values(
                quantity_on_hand=new_quantity,
                inventory_value=new_value,
                moving_average_unit_cost=new_mac,
            )
        )

    evidence_ids = list(
        await session.scalars(
            select(delivery_correction_evidence.c.evidence_id).where(
                delivery_correction_evidence.c.correction_id == correction["correction_id"]
            )
        )
    )
    original_case_filter = (
        delivery_exception_cases.c.correction_line_id.in_(
            [line["correction_line_id"] for line in prior_lines_by_delivery.values()]
        )
        if source_correction_id is not None
        else (
            delivery_exception_cases.c.confirmation_line_id.in_(
                [line["confirmation_line_id"] for line in lines]
            )
            & delivery_exception_cases.c.correction_line_id.is_(None)
        )
    )
    original_cases = list(
        (
            await session.execute(select(delivery_exception_cases).where(original_case_filter))
        ).mappings()
    )
    for case_row in original_cases:
        event_id = _id(
            "delivery-correction-superseded-case",
            f"{correction['correction_id']}:{case_row['exception_case_id']}",
        )
        await session.execute(
            insert(delivery_exception_events).values(
                exception_event_id=event_id,
                exception_case_id=case_row["exception_case_id"],
                event_type="superseded_by_correction",
                quantity_base=case_row["original_quantity_base"],
                source_document_type="delivery_correction",
                source_document_id=correction["correction_id"],
                from_custody=case_row["initial_custody"],
                to_custody=case_row["initial_custody"],
                reason=correction["reason"],
                approved_by=actor.subject,
                approval_authority_id=approval_authority_id,
                actor_subject=actor.subject,
                correlation_id=correlation_id,
                idempotency_key=(
                    f"delivery-correction:{correction['correction_id']}:supersede:"
                    f"{case_row['exception_case_id']}"
                ),
            )
        )
        await session.execute(
            update(delivery_exception_state)
            .where(delivery_exception_state.c.exception_case_id == case_row["exception_case_id"])
            .values(
                status="resolved",
                open_quantity_base=ZERO,
                resolved_quantity_base=case_row["original_quantity_base"],
                version=delivery_exception_state.c.version + 1,
                updated_at=func.now(),
            )
        )

    for line in lines:
        for kind in ("refused", "damaged", "short_missing", "still_undelivered"):
            quantity = cast(Decimal, line[f"{kind}_quantity_base"])
            if quantity == ZERO:
                continue
            case_id = _id(
                "delivery-correction-exception-case",
                f"{correction['correction_id']}:{line['delivery_line_id']}:{kind}",
            )
            initial_custody = "investigation" if kind == "short_missing" else "in_transit"
            short_effect = replacement_short_movements.get(line["correction_line_id"])
            await session.execute(
                insert(delivery_exception_cases).values(
                    exception_case_id=case_id,
                    confirmation_line_id=line["confirmation_line_id"],
                    correction_line_id=line["correction_line_id"],
                    exception_kind=kind,
                    original_quantity_base=quantity,
                    initial_custody=initial_custody,
                    responsible_party_type="unknown",
                    responsible_subject=None,
                    responsible_snapshot={
                        "reason": correction["reason"],
                        "delivery_correction_id": str(correction["correction_id"]),
                    },
                    investigation_movement_group_id=short_effect[0] if short_effect else None,
                    investigation_out_movement_id=short_effect[1] if short_effect else None,
                    investigation_in_movement_id=short_effect[2] if short_effect else None,
                    opened_by=correction["requested_by"],
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
            opened_event_id = _id("delivery-correction-exception-opened", case_id)
            await session.execute(
                insert(delivery_exception_events).values(
                    exception_event_id=opened_event_id,
                    exception_case_id=case_id,
                    event_type="opened",
                    quantity_base=quantity,
                    source_document_type="delivery_correction",
                    source_document_id=correction["correction_id"],
                    from_custody="in_transit",
                    to_custody=initial_custody,
                    reason=correction["reason"],
                    approved_by=actor.subject,
                    approval_authority_id=approval_authority_id,
                    movement_group_id=short_effect[0] if short_effect else None,
                    actor_subject=actor.subject,
                    correlation_id=correlation_id,
                    idempotency_key=(
                        f"delivery-correction:{correction['correction_id']}:open:{case_id}"
                    ),
                )
            )
            if evidence_ids:
                await session.execute(
                    insert(delivery_exception_case_evidence),
                    [
                        {"exception_case_id": case_id, "evidence_id": evidence_id}
                        for evidence_id in evidence_ids
                    ],
                )
    replacement_receipt_id: UUID | None = None
    if sum((cast(Decimal, line["accepted_quantity_base"]) for line in lines), ZERO) > ZERO:
        series = (
            (
                await session.execute(
                    select(document_series)
                    .where(
                        document_series.c.branch_id == correction["branch_id"],
                        document_series.c.document_type == "delivery_receipt",
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .one()
        )
        replacement_receipt_id = _id("delivery-correction-receipt", correction["correction_id"])
        number = f"{series['prefix']}-{series['next_number']:08d}"
        source_receipt_id = cast(UUID, correction["original_delivery_receipt_id"])
        original_number = await session.scalar(
            select(delivery_receipts.c.number).where(
                delivery_receipts.c.delivery_receipt_id == source_receipt_id
            )
        )
        root_receipt_id = source_receipt_id
        while True:
            prior_id = await session.scalar(
                select(delivery_receipts.c.corrects_delivery_receipt_id).where(
                    delivery_receipts.c.delivery_receipt_id == root_receipt_id
                )
            )
            if prior_id is None:
                break
            root_receipt_id = cast(UUID, prior_id)
        root_snapshot = cast(
            dict[str, Any],
            await session.scalar(
                select(delivery_receipts.c.snapshot).where(
                    delivery_receipts.c.delivery_receipt_id == root_receipt_id
                )
            ),
        )
        corrected_accepted_by_line: defaultdict[str, Decimal] = defaultdict(lambda: ZERO)
        for line in lines:
            corrected_accepted_by_line[str(line["line_id"])] += cast(
                Decimal, line["accepted_quantity_base"]
            )
        snapshot = {
            **root_snapshot,
            "correction_id": str(correction["correction_id"]),
            "corrects_delivery_receipt_id": str(source_receipt_id),
            "corrects_delivery_receipt_number": original_number,
            "lines": [
                {
                    **item,
                    "accepted_quantity_base": str(corrected_accepted_by_line[str(item["line_id"])]),
                    "accepted_quantity_entered": str(
                        (
                            corrected_accepted_by_line[str(item["line_id"])]
                            / Decimal(item["conversion_snapshot"]["base_quantity_per_unit"])
                        ).quantize(SIX_PLACES, ROUND_HALF_UP)
                    ),
                }
                for item in root_snapshot["lines"]
                if corrected_accepted_by_line[str(item["line_id"])] > ZERO
            ],
        }
        await session.execute(
            update(document_series)
            .where(document_series.c.document_series_id == series["document_series_id"])
            .values(next_number=document_series.c.next_number + 1)
        )
        await session.execute(
            insert(delivery_receipts).values(
                delivery_receipt_id=replacement_receipt_id,
                confirmation_id=correction["confirmation_id"],
                correction_id=correction["correction_id"],
                corrects_delivery_receipt_id=correction["original_delivery_receipt_id"],
                document_series_id=series["document_series_id"],
                branch_id=correction["branch_id"],
                series_number=series["next_number"],
                number=number,
                snapshot=snapshot,
            )
        )
        await session.execute(
            insert(delivery_receipt_documents).values(
                delivery_receipt_id=replacement_receipt_id,
                status="pending_document",
                object_key=f"delivery-receipts/{replacement_receipt_id}.pdf",
            )
        )
        await session.execute(
            insert(document_series_number_audit).values(
                document_series_number_audit_id=uuid4(),
                document_series_id=series["document_series_id"],
                series_number=series["next_number"],
                status="issued",
                delivery_receipt_id=replacement_receipt_id,
            )
        )
    event_id = _id("delivery-correction-outbox", correction["correction_id"])
    await session.execute(
        insert(outbox_events).values(
            outbox_event_id=event_id,
            aggregate_type="delivery_correction",
            aggregate_id=correction["correction_id"],
            event_type="delivery.correction.posted.v1",
            payload={
                "correction_id": str(correction["correction_id"]),
                "original_delivery_receipt_id": str(correction["original_delivery_receipt_id"]),
                "replacement_delivery_receipt_id": str(replacement_receipt_id)
                if replacement_receipt_id
                else None,
            },
            correlation_id=correlation_id,
        )
    )
    await session.execute(insert(outbox_processing_state).values(outbox_event_id=event_id))
    return replacement_receipt_id, event_id


@router.post(
    "/v1/delivery-corrections/{correction_id}/authorization",
    response_model=DeliveryCorrectionResponse,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def authorize_delivery_correction(
    correction_id: UUID,
    command: AuthorizeDeliveryCorrection,
    request: Request,
    actor: Annotated[AuthorizedUser, Depends(require_delivery_correction_authorizer)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200, pattern=r".*\S.*"),
    ] = None,
) -> DeliveryCorrectionResponse:
    if not idempotency_key:
        raise AppError(400, "idempotency_key_required", "Idempotency-Key is required.")
    request_hash = _request_hash(
        "authorize-delivery-correction", correction_id, actor.subject, command
    )
    await session.rollback()
    async with session.begin():
        await _lock(session, f"delivery-correction:{correction_id}")
        correction = (
            (
                await session.execute(
                    select(delivery_corrections)
                    .where(delivery_corrections.c.correction_id == correction_id)
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if correction is None:
            raise AppError(
                404, "delivery_correction_not_found", "Delivery Correction does not exist."
            )
        if (
            correction["branch_id"] not in actor.branch_ids
            or correction["warehouse_id"] not in actor.warehouse_ids
        ):
            raise AppError(
                403, "operational_scope_required", "Branch and Warehouse scope are required."
            )
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            return DeliveryCorrectionResponse.model_validate(replay)
        if command.expected_correction_version != 1:
            raise AppError(
                409, "delivery_correction_version_conflict", "Delivery Correction changed; refresh."
            )
        if correction["requested_by"] == actor.subject:
            raise AppError(
                403,
                "maker_checker_violation",
                "The requester cannot authorize the same Delivery Correction.",
            )
        authority = (
            (
                await session.execute(
                    select(approval_authorities).where(
                        approval_authorities.c.user_subject == actor.subject,
                        approval_authorities.c.capability_code
                        == "fulfillment:delivery-correction-authorize",
                        approval_authorities.c.branch_id == correction["branch_id"],
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if authority is None or (
            authority["maximum_amount"] is not None
            and authority["maximum_amount"] < correction["affected_value_base_currency"]
        ):
            raise AppError(
                403,
                "approval_authority_required",
                "Sufficient Delivery Correction Approval Authority is required.",
            )
        if await session.scalar(
            select(
                exists().where(delivery_correction_authorizations.c.correction_id == correction_id)
            )
        ):
            raise AppError(
                409, "delivery_correction_already_posted", "Delivery Correction is already posted."
            )
        source_receipt = (
            (
                await session.execute(
                    select(
                        delivery_receipts.c.delivery_receipt_id,
                        delivery_receipts.c.confirmation_id,
                        delivery_receipts.c.correction_id.label("source_correction_id"),
                    ).where(
                        delivery_receipts.c.delivery_receipt_id
                        == correction["original_delivery_receipt_id"]
                    )
                )
            )
            .mappings()
            .one()
        )
        await _assert_eligible(
            session,
            cast(Mapping[str, Any], source_receipt),
            exclude_correction_id=correction_id,
        )
        await _post_correction(
            session,
            dict(correction),
            actor,
            request.state.correlation_id,
            authority["approval_authority_id"],
        )
        await session.execute(
            insert(delivery_correction_authorizations).values(
                correction_id=correction_id,
                authorized_by=actor.subject,
                approval_authority_id=authority["approval_authority_id"],
                idempotency_key=idempotency_key,
                correlation_id=request.state.correlation_id,
            )
        )
        result = await _response(session, correction_id)
        await store_command_result(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result=result,
        )
    return result


async def _authorize_reader(
    session: AsyncSession, correction_id: UUID, actor: AuthorizedUser
) -> None:
    scope = (
        await session.execute(
            select(delivery_corrections.c.branch_id, delivery_corrections.c.warehouse_id).where(
                delivery_corrections.c.correction_id == correction_id
            )
        )
    ).one_or_none()
    if scope is None:
        raise AppError(404, "delivery_correction_not_found", "Delivery Correction does not exist.")
    if scope.branch_id not in actor.branch_ids or scope.warehouse_id not in actor.warehouse_ids:
        raise AppError(
            403, "operational_scope_required", "Branch and Warehouse scope are required."
        )


@router.get(
    "/v1/delivery-corrections/{correction_id}",
    response_model=DeliveryCorrectionResponse,
    responses=error_responses(401, 403, 404, 500),
)
async def get_delivery_correction(
    correction_id: UUID,
    actor: Annotated[AuthorizedUser, Depends(require_delivery_correction_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> DeliveryCorrectionResponse:
    await _authorize_reader(session, correction_id, actor)
    return await _response(session, correction_id)


@router.get(
    "/v1/delivery-corrections",
    response_model=DeliveryCorrectionList,
    responses=error_responses(401, 403, 500),
)
async def list_delivery_corrections(
    actor: Annotated[AuthorizedUser, Depends(require_delivery_correction_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    status: Annotated[Literal["pending_authorization", "posted"] | None, Query()] = None,
) -> DeliveryCorrectionList:
    query = (
        select(
            delivery_corrections,
            delivery_correction_authorizations.c.authorized_by,
            delivery_correction_authorizations.c.authorized_at,
        )
        .outerjoin(
            delivery_correction_authorizations,
            delivery_corrections.c.correction_id
            == delivery_correction_authorizations.c.correction_id,
        )
        .where(
            delivery_corrections.c.branch_id.in_(actor.branch_ids),
            delivery_corrections.c.warehouse_id.in_(actor.warehouse_ids),
        )
    )
    if status == "pending_authorization":
        query = query.where(delivery_correction_authorizations.c.correction_id.is_(None))
    elif status == "posted":
        query = query.where(delivery_correction_authorizations.c.correction_id.is_not(None))
    rows = list(
        (
            await session.execute(query.order_by(delivery_corrections.c.requested_at.desc()))
        ).mappings()
    )
    items = [
        DeliveryCorrectionSummary(
            correction_id=row["correction_id"],
            original_delivery_receipt_id=row["original_delivery_receipt_id"],
            delivery_id=row["delivery_id"],
            branch_id=row["branch_id"],
            warehouse_id=row["warehouse_id"],
            status="posted" if row["authorized_by"] else "pending_authorization",
            version=2 if row["authorized_by"] else 1,
            reason=row["reason"],
            requested_by=row["requested_by"],
            requested_at=row["requested_at"],
            authorized_by=row["authorized_by"],
            authorized_at=row["authorized_at"],
            affected_value_base_currency=row["affected_value_base_currency"],
            base_currency=row["base_currency"],
        )
        for row in rows
    ]
    return DeliveryCorrectionList(items=items, total=len(items))
