from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256
from typing import Annotated, Any, Literal, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import case, delete, func, insert, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tradeflow_api.auth import (
    AuthorizedUser,
    require_cash_reconciler,
    require_check_clearer,
    require_payment_deadline_processor,
    require_payment_reader,
    require_payment_recorder,
    require_payment_reverser,
    require_payment_verifier,
    require_pick_reader,
    require_pick_releaser,
    require_reservation_retrier,
)
from tradeflow_api.command_receipts import get_command_replay, store_command_result
from tradeflow_api.database import get_database_session
from tradeflow_api.errors import AppError, error_responses
from tradeflow_api.models import (
    active_sales_order_holds,
    branch_payment_deadline_policies,
    cash_reconciliation_events,
    cash_reconciliation_items,
    cod_collections,
    commercial_approval_invalidations,
    commercial_approvals,
    companies,
    customer_accounts,
    fulfillment_line_pick_state,
    fulfillment_order_lines,
    fulfillment_order_state,
    fulfillment_orders,
    inventory_availability,
    inventory_reservation_events,
    inventory_reserved_by_sku_warehouse,
    payment_methods,
    payment_receipt_balances,
    payment_receipt_events,
    payment_receipt_status,
    payment_receipts,
    pick_releases,
    prepayment_coverage_events,
    sales_order_hold_events,
    sales_order_line_commitments,
    sales_order_line_revisions,
    sales_order_revisions,
    sales_orders,
    warehouse_stock_locations,
    warehouses,
)
from tradeflow_api.money import currency_quantum
from tradeflow_api.payment_balance import (
    PaymentApplicationState,
    payment_application_state,
    unapplied_payment_amount,
)

router = APIRouter(tags=["finance", "fulfillment"])
ZERO = Decimal("0")


class CommandModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PaymentEvidence(CommandModel):
    account_or_provider: str = Field(min_length=1, max_length=200)
    value_date: str = Field(min_length=1, max_length=30)
    document_url: str = Field(min_length=1, max_length=1000)

    @field_validator("account_or_provider", "value_date", "document_url")
    @classmethod
    def nonblank(cls, value: str) -> str:
        normalized = value.strip()
        if normalized == "":
            raise ValueError("Payment evidence must contain non-whitespace text.")
        return normalized


class RecordPaymentReceiptCommand(CommandModel):
    payment_receipt_id: UUID
    branch_id: UUID
    customer_id: UUID
    sales_order_id: UUID | None = None
    payment_method: Literal["cash", "bank_transfer", "check", "electronic"]
    amount: Decimal = Field(gt=0, max_digits=24, decimal_places=6)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    received_at: datetime
    external_reference: str | None = Field(default=None, max_length=200)
    evidence: PaymentEvidence | None = None


class PaymentVerificationCommand(CommandModel):
    decision: Literal["cleared", "evidence_verified", "rejected"]
    verified_at: datetime
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def nonblank_reason(cls, value: str) -> str:
        normalized = value.strip()
        if normalized == "":
            raise ValueError("A verification reason is required.")
        return normalized


class CheckClearanceCommand(CommandModel):
    cleared_at: datetime
    bank_reference: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=500)


class ProviderConfirmationCommand(CommandModel):
    confirmed_at: datetime
    provider_reference: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=500)


class CashReconciliationCommand(CommandModel):
    cash_reconciliation_id: UUID
    counted_amount: Decimal = Field(ge=0, max_digits=24, decimal_places=6)
    reconciled_at: datetime
    reason: str = Field(min_length=1, max_length=500)


class CashReconciliationAdjustmentCommand(CashReconciliationCommand):
    pass


class CashReconciliationReversalCommand(CommandModel):
    cash_reconciliation_id: UUID
    reversed_at: datetime
    reason: str = Field(min_length=1, max_length=500)


class PaymentReversalCommand(CommandModel):
    payment_reversal_id: UUID
    reason: str = Field(min_length=1, max_length=500)
    reversed_at: datetime


class PickReleaseCommand(CommandModel):
    reason: str = Field(min_length=1, max_length=500)


class ProcessPaymentDeadlineCommand(CommandModel):
    fulfillment_order_id: UUID
    as_of: datetime | None = None


class ReservationRetryCommand(CommandModel):
    warehouse_id: UUID
    reason: str = Field(min_length=1, max_length=500)


class PaymentReceiptResponse(BaseModel):
    payment_receipt_id: UUID
    branch_id: UUID
    customer_id: UUID
    sales_order_id: UUID | None
    payment_method: str
    amount: Decimal
    currency: str
    received_at: datetime
    external_reference: str | None
    external_reference_normalized: str | None
    status: str
    cleared_amount: Decimal
    allocated_amount: Decimal
    unapplied_amount: Decimal
    application_state: PaymentApplicationState
    balance_version: int
    available_for_coverage: Decimal
    cash_reconciliation_status: str | None
    recorded_by: str
    verified_by: str | None
    reversal_id: UUID | None


class PaymentReceiptListResponse(BaseModel):
    items: list[PaymentReceiptResponse]
    total: int


class CashReconciliationResponse(BaseModel):
    cash_reconciliation_id: UUID
    payment_receipt_id: UUID
    status: Literal["reconciled"]
    counted_amount: Decimal
    variance_amount: Decimal


class CashReconciliationChangeResponse(BaseModel):
    cash_reconciliation_id: UUID
    payment_receipt_id: UUID
    event_type: Literal["adjusted", "reversed"]
    status: Literal["pending", "reconciled"]
    counted_amount: Decimal
    variance_amount: Decimal


class PaymentReversalResponse(BaseModel):
    payment_reversal_id: UUID
    original_payment_receipt_id: UUID
    amount: Decimal
    reason: str


class FulfillmentOrderResponse(BaseModel):
    fulfillment_order_id: UUID
    sales_order_id: UUID
    warehouse_id: UUID
    reservation_generation: int
    payment_timing_policy: Literal["prepaid", "cash_on_delivery", "on_account"]
    status: str
    currency: str
    order_value: Decimal
    payment_required: Decimal
    cleared_payment: Decimal
    reserved_quantity_base: Decimal
    backorder_quantity_base: Decimal
    payment_deadline_at: datetime | None
    payment_hold: bool


class FulfillmentOrderListResponse(BaseModel):
    items: list[FulfillmentOrderResponse]
    total: int


class PickReleaseResponse(BaseModel):
    pick_release_id: UUID
    fulfillment_order_id: UUID
    status: Literal["released"]
    quantity_base: Decimal
    payment_required: Decimal
    cleared_payment: Decimal


class PaymentDeadlineResponse(BaseModel):
    fulfillment_order_id: UUID
    status: Literal["not_due", "payment_satisfied", "payment_hold"]
    released_quantity_base: Decimal
    backorder_quantity_base: Decimal


class ReservationRetryResponse(BaseModel):
    sales_order_id: UUID
    fulfillment_order_id: UUID
    status: Literal["approved"]
    payment_hold: bool
    reserved_quantity_base: Decimal
    backorder_quantity_base: Decimal


def _money(value: Decimal, currency: str) -> Decimal:
    return value.quantize(currency_quantum(currency), ROUND_HALF_UP)


def _normalize_reference(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = "".join(value.upper().split())
    return normalized or None


def _request_hash(operation: str, command: BaseModel, context: str) -> str:
    payload = f"{operation}:{context}:{command.model_dump_json(exclude_none=False)}"
    return sha256(payload.encode()).hexdigest()


async def _advisory_lock(session: AsyncSession, key: str) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": key},
    )


async def _company(session: AsyncSession) -> tuple[UUID, str]:
    row = (
        await session.execute(select(companies.c.company_id, companies.c.base_currency).limit(1))
    ).one_or_none()
    if row is None:
        raise AppError(409, "company_not_configured", "The Company is not configured.")
    return row.company_id, row.base_currency


async def _receipt_response(
    session: AsyncSession,
    payment_receipt_id: UUID,
) -> PaymentReceiptResponse:
    row = (
        (
            await session.execute(
                select(
                    payment_receipts,
                    payment_receipt_status.c.state,
                    payment_receipt_status.c.verified_by,
                    payment_receipt_status.c.reversal_id,
                    payment_receipt_balances.c.cleared_amount,
                    payment_receipt_balances.c.reversed_amount,
                    payment_receipt_balances.c.refunded_amount,
                    payment_receipt_balances.c.allocated_amount,
                    payment_receipt_balances.c.coverage_designated_amount,
                    payment_receipt_balances.c.version.label("balance_version"),
                    cash_reconciliation_items.c.status.label("cash_status"),
                )
                .join(
                    payment_receipt_status,
                    payment_receipts.c.payment_receipt_id
                    == payment_receipt_status.c.payment_receipt_id,
                )
                .join(
                    payment_receipt_balances,
                    payment_receipts.c.payment_receipt_id
                    == payment_receipt_balances.c.payment_receipt_id,
                )
                .outerjoin(
                    cash_reconciliation_items,
                    payment_receipts.c.payment_receipt_id
                    == cash_reconciliation_items.c.payment_receipt_id,
                )
                .where(payment_receipts.c.payment_receipt_id == payment_receipt_id)
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise AppError(404, "payment_receipt_not_found", "The Payment Receipt does not exist.")
    receipt = dict(row)
    unapplied = unapplied_payment_amount(receipt)
    currency = row["currency"]
    return PaymentReceiptResponse(
        payment_receipt_id=row["payment_receipt_id"],
        branch_id=row["branch_id"],
        customer_id=row["customer_id"],
        sales_order_id=row["intended_sales_order_id"],
        payment_method=row["payment_method_kind"],
        amount=_money(row["amount"], currency),
        currency=currency,
        received_at=row["received_at"],
        external_reference=row["external_reference"],
        external_reference_normalized=row["external_reference_normalized"],
        status=("awaiting_bank_clearance" if row["state"] == "pending_clearance" else row["state"]),
        cleared_amount=_money(row["cleared_amount"], currency),
        allocated_amount=_money(row["allocated_amount"], currency),
        unapplied_amount=_money(unapplied, currency),
        application_state=payment_application_state(receipt),
        balance_version=row["balance_version"],
        available_for_coverage=_money(
            unapplied - row["coverage_designated_amount"],
            currency,
        ),
        cash_reconciliation_status=row["cash_status"],
        recorded_by=row["recorded_by"],
        verified_by=row["verified_by"],
        reversal_id=row["reversal_id"],
    )


async def _fulfillment_response(
    session: AsyncSession,
    fulfillment_order_id: UUID,
) -> FulfillmentOrderResponse:
    row = (
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
    if row is None:
        raise AppError(404, "fulfillment_order_not_found", "The Fulfillment Order does not exist.")
    return FulfillmentOrderResponse(
        fulfillment_order_id=row["fulfillment_order_id"],
        sales_order_id=row["sales_order_id"],
        warehouse_id=row["warehouse_id"],
        reservation_generation=row["reservation_generation"],
        payment_timing_policy=row["payment_timing_policy"],
        status=row["status"],
        currency=row["currency"],
        order_value=_money(row["order_value"], row["currency"]),
        payment_required=_money(row["payment_required"], row["currency"]),
        cleared_payment=_money(row["covered_amount"], row["currency"]),
        reserved_quantity_base=row["reserved_quantity_base"],
        backorder_quantity_base=row["backorder_quantity_base"],
        payment_deadline_at=row["payment_deadline_at"],
        payment_hold=row["payment_hold"],
    )


async def _release_coverage(
    session: AsyncSession,
    *,
    fulfillment_order_id: UUID,
    actor_subject: str,
    correlation_id: str,
    idempotency_key: str,
    reason: str,
) -> None:
    rows = (
        (
            await session.execute(
                select(
                    prepayment_coverage_events.c.payment_receipt_id,
                    func.sum(
                        case(
                            (
                                prepayment_coverage_events.c.event_type == "designated",
                                prepayment_coverage_events.c.amount,
                            ),
                            else_=-prepayment_coverage_events.c.amount,
                        )
                    ).label("amount"),
                )
                .where(prepayment_coverage_events.c.fulfillment_order_id == fulfillment_order_id)
                .group_by(prepayment_coverage_events.c.payment_receipt_id)
            )
        )
        .mappings()
        .all()
    )
    for row in rows:
        amount = row["amount"] or ZERO
        if amount <= ZERO:
            continue
        await session.execute(
            insert(prepayment_coverage_events).values(
                coverage_event_id=uuid4(),
                fulfillment_order_id=fulfillment_order_id,
                payment_receipt_id=row["payment_receipt_id"],
                event_type="released",
                amount=amount,
                reason=reason,
                actor_subject=actor_subject,
                source_id=fulfillment_order_id,
                correlation_id=correlation_id,
                idempotency_key=f"{idempotency_key}:coverage-release",
            )
        )
        await session.execute(
            update(payment_receipt_balances)
            .where(payment_receipt_balances.c.payment_receipt_id == row["payment_receipt_id"])
            .values(
                coverage_designated_amount=(
                    payment_receipt_balances.c.coverage_designated_amount - amount
                ),
                version=payment_receipt_balances.c.version + 1,
            )
        )
    await session.execute(
        update(fulfillment_order_state)
        .where(fulfillment_order_state.c.fulfillment_order_id == fulfillment_order_id)
        .values(
            covered_amount=ZERO,
            version=fulfillment_order_state.c.version + 1,
            updated_at=func.now(),
        )
    )


async def _designate_available_payment(
    session: AsyncSession,
    *,
    payment_receipt_id: UUID,
    fulfillment_order_id: UUID,
    actor_subject: str,
    correlation_id: str,
    idempotency_key: str,
) -> Decimal:
    await _advisory_lock(session, f"prepayment:{fulfillment_order_id}")
    state = (
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
    if state is None or state["status"] not in {"reserved", "payment_ready"}:
        return ZERO
    balance = (
        (
            await session.execute(
                select(payment_receipt_balances)
                .where(payment_receipt_balances.c.payment_receipt_id == payment_receipt_id)
                .with_for_update()
            )
        )
        .mappings()
        .one()
    )
    available = (
        balance["cleared_amount"]
        - balance["reversed_amount"]
        - balance["refunded_amount"]
        - balance["allocated_amount"]
        - balance["coverage_designated_amount"]
    )
    needed = max(state["payment_required"] - state["covered_amount"], ZERO)
    amount = min(available, needed)
    if amount <= ZERO:
        return ZERO
    await session.execute(
        insert(prepayment_coverage_events).values(
            coverage_event_id=uuid4(),
            fulfillment_order_id=fulfillment_order_id,
            payment_receipt_id=payment_receipt_id,
            event_type="designated",
            amount=amount,
            reason="Cleared Customer Prepayment",
            actor_subject=actor_subject,
            source_id=payment_receipt_id,
            correlation_id=correlation_id,
            idempotency_key=f"{idempotency_key}:coverage",
        )
    )
    await session.execute(
        update(payment_receipt_balances)
        .where(payment_receipt_balances.c.payment_receipt_id == payment_receipt_id)
        .values(
            coverage_designated_amount=(
                payment_receipt_balances.c.coverage_designated_amount + amount
            ),
            version=payment_receipt_balances.c.version + 1,
        )
    )
    covered = state["covered_amount"] + amount
    await session.execute(
        update(fulfillment_order_state)
        .where(fulfillment_order_state.c.fulfillment_order_id == fulfillment_order_id)
        .values(
            covered_amount=covered,
            status=("payment_ready" if covered >= state["payment_required"] else "reserved"),
            version=fulfillment_order_state.c.version + 1,
            updated_at=func.now(),
        )
    )
    return cast(Decimal, amount)


async def create_fulfillment_for_approval(
    session: AsyncSession,
    *,
    approval_id: UUID,
    actor_subject: str,
    correlation_id: str,
    idempotency_key: str,
) -> UUID | None:
    approval = (
        (
            await session.execute(
                select(commercial_approvals).where(
                    commercial_approvals.c.commercial_approval_id == approval_id
                )
            )
        )
        .mappings()
        .one()
    )
    commitments = (
        (
            await session.execute(
                select(sales_order_line_commitments)
                .where(sales_order_line_commitments.c.commercial_approval_id == approval_id)
                .order_by(sales_order_line_commitments.c.line_id)
            )
        )
        .mappings()
        .all()
    )
    if not any(row["reserved_quantity_base"] > ZERO for row in commitments):
        return None
    revision = (
        (
            await session.execute(
                select(sales_order_revisions).where(
                    sales_order_revisions.c.sales_order_revision_id
                    == approval["sales_order_revision_id"]
                )
            )
        )
        .mappings()
        .one()
    )
    line_rows = {
        row["line_id"]: row
        for row in (
            (
                await session.execute(
                    select(sales_order_line_revisions).where(
                        sales_order_line_revisions.c.sales_order_revision_id
                        == approval["sales_order_revision_id"]
                    )
                )
            )
            .mappings()
            .all()
        )
    }
    generation = (
        cast(
            int,
            await session.scalar(
                select(
                    func.coalesce(func.max(fulfillment_orders.c.reservation_generation), 0)
                ).where(fulfillment_orders.c.sales_order_id == approval["sales_order_id"])
            ),
        )
        + 1
    )
    policy = (
        (
            await session.execute(
                select(branch_payment_deadline_policies)
                .where(
                    branch_payment_deadline_policies.c.branch_id == revision["branch_id"],
                    branch_payment_deadline_policies.c.is_active.is_(True),
                )
                .order_by(branch_payment_deadline_policies.c.version.desc())
                .limit(1)
            )
        )
        .mappings()
        .one_or_none()
    )
    now = cast(datetime, await session.scalar(select(func.clock_timestamp())))
    if revision["payment_timing_policy"] == "prepaid" and policy is None:
        raise AppError(
            409,
            "payment_deadline_policy_required",
            "Prepaid reservation requires an active Branch Payment Deadline policy.",
        )
    deadline = (
        now + timedelta(minutes=policy["deadline_minutes"])
        if policy is not None and revision["payment_timing_policy"] == "prepaid"
        else None
    )
    fulfillment_order_id = uuid4()
    required = ZERO
    line_values: list[tuple[Mapping[str, Any], Mapping[str, Any], Decimal]] = []
    for commitment in commitments:
        line = line_rows[commitment["line_id"]]
        reserved_value = (
            line["line_total"]
            if commitment["reserved_quantity_base"] == commitment["ordered_quantity_base"]
            else _money(
                line["line_total"]
                * commitment["reserved_quantity_base"]
                / commitment["ordered_quantity_base"],
                revision["currency"],
            )
        )
        if revision["payment_timing_policy"] == "prepaid":
            required += reserved_value
        line_values.append((dict(commitment), dict(line), cast(Decimal, reserved_value)))
    payment_deadline_policy_id = None
    payment_deadline_minutes = None
    if deadline is not None and policy is not None:
        payment_deadline_policy_id = policy["policy_id"]
        payment_deadline_minutes = policy["deadline_minutes"]
    await session.execute(
        insert(fulfillment_orders).values(
            fulfillment_order_id=fulfillment_order_id,
            sales_order_id=approval["sales_order_id"],
            sales_order_revision_id=approval["sales_order_revision_id"],
            commercial_approval_id=approval_id,
            customer_id=approval["customer_id"],
            branch_id=revision["branch_id"],
            warehouse_id=approval["warehouse_id"],
            reservation_generation=generation,
            payment_timing_policy=revision["payment_timing_policy"],
            currency=revision["currency"],
            order_value=revision["grand_total"],
            payment_required=required,
            payment_deadline_at=deadline,
            payment_deadline_policy_id=payment_deadline_policy_id,
            payment_deadline_minutes=payment_deadline_minutes,
            created_by=actor_subject,
            correlation_id=correlation_id,
            idempotency_key=f"{idempotency_key}:fulfillment:{generation}",
        )
    )
    for commitment_row, line_row, reserved_value in line_values:
        await session.execute(
            insert(fulfillment_order_lines).values(
                fulfillment_order_id=fulfillment_order_id,
                line_id=commitment_row["line_id"],
                sales_order_id=approval["sales_order_id"],
                sales_order_revision_id=approval["sales_order_revision_id"],
                commercial_approval_id=approval_id,
                sku_id=commitment_row["sku_id"],
                warehouse_id=commitment_row["warehouse_id"],
                ordered_quantity_base=commitment_row["ordered_quantity_base"],
                reserved_quantity_base=commitment_row["reserved_quantity_base"],
                backorder_quantity_base=commitment_row["backorder_quantity_base"],
                approved_line_total=line_row["line_total"],
                reserved_value=reserved_value,
                calculation_snapshot={
                    "approved_line_total": str(line_row["line_total"]),
                    "currency": revision["currency"],
                    "ordered_quantity_base": str(commitment_row["ordered_quantity_base"]),
                    "reserved_quantity_base": str(commitment_row["reserved_quantity_base"]),
                    "rounding": "ROUND_HALF_UP",
                },
            )
        )
    reserved_quantity = sum(
        (row["reserved_quantity_base"] for row in commitments),
        ZERO,
    )
    backorder_quantity = sum(
        (row["backorder_quantity_base"] for row in commitments),
        ZERO,
    )
    initial_status = (
        "reserved" if revision["payment_timing_policy"] == "prepaid" else "payment_ready"
    )
    await session.execute(
        insert(fulfillment_order_state).values(
            fulfillment_order_id=fulfillment_order_id,
            status=initial_status,
            reserved_quantity_base=reserved_quantity,
            backorder_quantity_base=backorder_quantity,
            covered_amount=ZERO,
            payment_hold=False,
        )
    )
    return fulfillment_order_id


async def cancel_fulfillment_for_approval(
    session: AsyncSession,
    *,
    approval_id: UUID,
    actor_subject: str,
    correlation_id: str,
    idempotency_key: str,
    reason: str,
) -> None:
    ids = (
        await session.execute(
            select(fulfillment_orders.c.fulfillment_order_id).where(
                fulfillment_orders.c.commercial_approval_id == approval_id
            )
        )
    ).scalars()
    for fulfillment_order_id in ids:
        await _advisory_lock(session, f"prepayment:{fulfillment_order_id}")
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
        if state["status"] == "cancelled":
            continue
        if state["status"] == "pick_released":
            raise AppError(
                409,
                "picked_fulfillment_change_requires_resolution",
                "A picked Fulfillment Order requires an explicit fulfillment exception before "
                "its Commercial Approval can be invalidated.",
            )
        await _release_coverage(
            session,
            fulfillment_order_id=fulfillment_order_id,
            actor_subject=actor_subject,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            reason=reason,
        )
        await session.execute(
            update(fulfillment_order_state)
            .where(fulfillment_order_state.c.fulfillment_order_id == fulfillment_order_id)
            .values(
                status="cancelled",
                payment_hold=False,
                version=fulfillment_order_state.c.version + 1,
                updated_at=func.now(),
            )
        )


async def _latest_fulfillment_for_order(
    session: AsyncSession,
    sales_order_id: UUID,
) -> UUID | None:
    return await session.scalar(
        select(fulfillment_orders.c.fulfillment_order_id)
        .where(fulfillment_orders.c.sales_order_id == sales_order_id)
        .order_by(fulfillment_orders.c.reservation_generation.desc())
        .limit(1)
    )


async def _clear_receipt(
    session: AsyncSession,
    *,
    receipt: Mapping[str, Any],
    actor_subject: str,
    correlation_id: str,
    idempotency_key: str,
    occurred_at: datetime,
    verified_by: str | None = None,
) -> None:
    receipt_id = cast(UUID, receipt["payment_receipt_id"])
    await session.execute(
        insert(payment_receipt_events).values(
            payment_receipt_event_id=uuid4(),
            payment_receipt_id=receipt_id,
            event_type="cleared",
            actor_subject=actor_subject,
            reason="Payment method clearance controls satisfied",
            evidence=None,
            source_id=receipt_id,
            correlation_id=correlation_id,
            idempotency_key=f"{idempotency_key}:cleared",
            occurred_at=occurred_at,
        )
    )
    await session.execute(
        update(payment_receipt_status)
        .where(payment_receipt_status.c.payment_receipt_id == receipt_id)
        .values(
            state="cleared",
            verified_by=verified_by,
            cleared_at=occurred_at,
            version=payment_receipt_status.c.version + 1,
            updated_at=func.now(),
        )
    )
    await session.execute(
        update(payment_receipt_balances)
        .where(payment_receipt_balances.c.payment_receipt_id == receipt_id)
        .values(
            cleared_amount=receipt["amount"],
            version=payment_receipt_balances.c.version + 1,
        )
    )
    fulfillment_order_id = cast(UUID | None, receipt["intended_fulfillment_order_id"])
    if fulfillment_order_id is not None:
        await _designate_available_payment(
            session,
            payment_receipt_id=receipt_id,
            fulfillment_order_id=fulfillment_order_id,
            actor_subject=actor_subject,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
        )


async def _batch_receipt_responses(
    session: AsyncSession,
    receipt_ids: list[UUID],
) -> dict[UUID, PaymentReceiptResponse]:
    if not receipt_ids:
        return {}
    rows = (
        (
            await session.execute(
                select(
                    payment_receipts,
                    payment_receipt_status.c.state,
                    payment_receipt_status.c.verified_by,
                    payment_receipt_status.c.reversal_id,
                    payment_receipt_balances.c.cleared_amount,
                    payment_receipt_balances.c.reversed_amount,
                    payment_receipt_balances.c.refunded_amount,
                    payment_receipt_balances.c.allocated_amount,
                    payment_receipt_balances.c.coverage_designated_amount,
                    payment_receipt_balances.c.version.label("balance_version"),
                    cash_reconciliation_items.c.status.label("cash_status"),
                )
                .join(
                    payment_receipt_status,
                    payment_receipts.c.payment_receipt_id
                    == payment_receipt_status.c.payment_receipt_id,
                )
                .join(
                    payment_receipt_balances,
                    payment_receipts.c.payment_receipt_id
                    == payment_receipt_balances.c.payment_receipt_id,
                )
                .outerjoin(
                    cash_reconciliation_items,
                    payment_receipts.c.payment_receipt_id
                    == cash_reconciliation_items.c.payment_receipt_id,
                )
                .where(payment_receipts.c.payment_receipt_id.in_(receipt_ids))
            )
        )
        .mappings()
        .all()
    )
    result: dict[UUID, PaymentReceiptResponse] = {}
    for row in rows:
        receipt = dict(row)
        unapplied = unapplied_payment_amount(receipt)
        currency = row["currency"]
        result[row["payment_receipt_id"]] = PaymentReceiptResponse(
            payment_receipt_id=row["payment_receipt_id"],
            branch_id=row["branch_id"],
            customer_id=row["customer_id"],
            sales_order_id=row["intended_sales_order_id"],
            payment_method=row["payment_method_kind"],
            amount=_money(row["amount"], currency),
            currency=currency,
            received_at=row["received_at"],
            external_reference=row["external_reference"],
            external_reference_normalized=row["external_reference_normalized"],
            status=(
                "awaiting_bank_clearance" if row["state"] == "pending_clearance" else row["state"]
            ),
            cleared_amount=_money(row["cleared_amount"], currency),
            allocated_amount=_money(row["allocated_amount"], currency),
            unapplied_amount=_money(unapplied, currency),
            application_state=payment_application_state(receipt),
            balance_version=row["balance_version"],
            available_for_coverage=_money(
                unapplied - row["coverage_designated_amount"],
                currency,
            ),
            cash_reconciliation_status=row["cash_status"],
            recorded_by=row["recorded_by"],
            verified_by=row["verified_by"],
            reversal_id=row["reversal_id"],
        )
    return result


@router.get(
    "/v1/finance/payment-receipts",
    response_model=PaymentReceiptListResponse,
    responses=error_responses(401, 403, 422, 500),
)
async def list_payment_receipts(
    actor: Annotated[AuthorizedUser, Depends(require_payment_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    branch_id: Annotated[UUID | None, Query()] = None,
    customer_id: Annotated[UUID | None, Query()] = None,
    application_state: Annotated[PaymentApplicationState | None, Query()] = None,
    status: Annotated[
        Literal[
            "pending_verification",
            "awaiting_bank_clearance",
            "cleared",
            "rejected",
            "reversed",
        ]
        | None,
        Query(),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaymentReceiptListResponse:
    scoped_branches = actor.branch_ids
    if branch_id is not None:
        if branch_id not in scoped_branches:
            raise AppError(403, "operational_scope_required", "Branch scope is required.")
        scoped_branches = (branch_id,)
    state = "pending_clearance" if status == "awaiting_bank_clearance" else status

    unapplied_expr = (
        payment_receipt_balances.c.cleared_amount
        - payment_receipt_balances.c.reversed_amount
        - payment_receipt_balances.c.refunded_amount
        - payment_receipt_balances.c.allocated_amount
    )
    application_state_expr = case(
        (payment_receipt_status.c.state != "cleared", "not_cleared"),
        (unapplied_expr <= ZERO, "fully_applied"),
        (payment_receipt_balances.c.allocated_amount > ZERO, "partially_applied"),
        else_="unapplied",
    )

    base = (
        select(
            payment_receipts.c.payment_receipt_id,
            payment_receipts.c.received_at,
            application_state_expr.label("application_state"),
        )
        .join(
            payment_receipt_status,
            payment_receipts.c.payment_receipt_id == payment_receipt_status.c.payment_receipt_id,
        )
        .join(
            payment_receipt_balances,
            payment_receipts.c.payment_receipt_id == payment_receipt_balances.c.payment_receipt_id,
        )
        .where(payment_receipts.c.branch_id.in_(scoped_branches))
    )
    if state is not None:
        base = base.where(payment_receipt_status.c.state == state)
    if customer_id is not None:
        base = base.where(payment_receipts.c.customer_id == customer_id)
    if application_state is not None:
        base = base.where(application_state_expr == application_state)

    ordered = base.order_by(payment_receipts.c.received_at.desc())
    total = (await session.scalar(select(func.count()).select_from(ordered.subquery()))) or 0
    paged = ordered.limit(limit).offset(offset)
    receipt_ids = [row["payment_receipt_id"] for row in (await session.execute(paged)).mappings()]

    responses_by_id = await _batch_receipt_responses(session, receipt_ids)
    items = [responses_by_id[receipt_id] for receipt_id in receipt_ids]
    return PaymentReceiptListResponse(items=items, total=total)


@router.post(
    "/v1/finance/payment-receipts",
    response_model=PaymentReceiptResponse,
    status_code=201,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def record_payment_receipt(
    command: RecordPaymentReceiptCommand,
    request: Request,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_payment_recorder)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> PaymentReceiptResponse:
    if idempotency_key is None:
        raise AppError(400, "idempotency_key_required", "Idempotency-Key is required.")
    request_hash = _request_hash("record-payment-receipt", command, actor.subject)
    await session.rollback()
    async with session.begin():
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.status_code = 200
            response.headers["X-Idempotency-Replayed"] = "true"
            return PaymentReceiptResponse.model_validate(replay)
        company_id, base_currency = await _company(session)
        if command.currency != base_currency:
            raise AppError(
                409,
                "payment_currency_conflict",
                "Payment Receipt currency must match the Company Base Currency.",
            )
        customer = (
            (
                await session.execute(
                    select(customer_accounts).where(
                        customer_accounts.c.customer_id == command.customer_id
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if customer is None:
            raise AppError(404, "customer_not_found", "The Customer Account does not exist.")
        if command.branch_id not in actor.branch_ids or customer["branch_id"] != command.branch_id:
            raise AppError(
                403,
                "operational_scope_required",
                "Branch scope is required for this Payment Receipt.",
            )
        method = (
            (
                await session.execute(
                    select(payment_methods).where(
                        payment_methods.c.company_id == company_id,
                        payment_methods.c.kind == command.payment_method,
                        payment_methods.c.is_active.is_(True),
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if method is None:
            raise AppError(
                409,
                "payment_method_unavailable",
                "The Payment Method is not configured and active.",
            )
        reference = _normalize_reference(command.external_reference)
        if method["requires_external_reference"] and reference is None:
            raise AppError(
                422,
                "external_payment_reference_required",
                "An External Payment Reference is required.",
            )
        if method["requires_evidence"] and command.evidence is None:
            raise AppError(
                422,
                "payment_evidence_required",
                "Payment evidence is required for this Payment Method.",
            )
        if reference is not None:
            await _advisory_lock(
                session,
                f"payment-reference:{company_id}:{method['payment_method_id']}:{reference}",
            )
            conflict = await session.scalar(
                select(payment_receipt_status.c.payment_receipt_id).where(
                    payment_receipt_status.c.company_id == company_id,
                    payment_receipt_status.c.payment_method_id == method["payment_method_id"],
                    payment_receipt_status.c.external_reference_normalized == reference,
                    payment_receipt_status.c.state.in_(
                        ("pending_verification", "pending_clearance", "cleared")
                    ),
                )
            )
            if conflict is not None:
                raise AppError(
                    409,
                    "external_payment_reference_conflict",
                    "The active External Payment Reference is already recorded.",
                )
        fulfillment_order_id: UUID | None = None
        if command.sales_order_id is not None:
            order = (
                (
                    await session.execute(
                        select(sales_orders).where(
                            sales_orders.c.sales_order_id == command.sales_order_id
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if order is None or order["customer_id"] != command.customer_id:
                raise AppError(
                    409,
                    "payment_order_customer_conflict",
                    "The intended Sales Order must belong to the Payment Customer.",
                )
            if order["branch_id"] != command.branch_id:
                raise AppError(
                    403,
                    "operational_scope_required",
                    "The intended Sales Order is outside the Payment Branch.",
                )
            fulfillment_order_id = await _latest_fulfillment_for_order(
                session, command.sales_order_id
            )
        initial_state = "cleared" if command.payment_method == "cash" else "pending_verification"
        await session.execute(
            insert(payment_receipts).values(
                payment_receipt_id=command.payment_receipt_id,
                company_id=company_id,
                branch_id=command.branch_id,
                customer_id=command.customer_id,
                payment_method_id=method["payment_method_id"],
                payment_method_code=method["code"],
                payment_method_kind=method["kind"],
                amount=_money(command.amount, command.currency),
                currency=command.currency,
                received_at=command.received_at,
                external_reference=command.external_reference,
                external_reference_normalized=reference,
                evidence=(
                    command.evidence.model_dump(mode="json")
                    if command.evidence is not None
                    else None
                ),
                intended_sales_order_id=command.sales_order_id,
                intended_fulfillment_order_id=fulfillment_order_id,
                recorded_by=actor.subject,
                correlation_id=request.state.correlation_id,
                idempotency_key=idempotency_key,
            )
        )
        await session.execute(
            insert(payment_receipt_events).values(
                payment_receipt_event_id=uuid4(),
                payment_receipt_id=command.payment_receipt_id,
                event_type="recorded",
                actor_subject=actor.subject,
                reason="Payment Receipt recorded",
                evidence=(
                    command.evidence.model_dump(mode="json")
                    if command.evidence is not None
                    else None
                ),
                source_id=command.payment_receipt_id,
                correlation_id=request.state.correlation_id,
                idempotency_key=f"{idempotency_key}:recorded",
                occurred_at=command.received_at,
            )
        )
        await session.execute(
            insert(payment_receipt_status).values(
                payment_receipt_id=command.payment_receipt_id,
                company_id=company_id,
                payment_method_id=method["payment_method_id"],
                external_reference_normalized=reference,
                state=initial_state,
                cleared_at=(command.received_at if initial_state == "cleared" else None),
            )
        )
        await session.execute(
            insert(payment_receipt_balances).values(
                payment_receipt_id=command.payment_receipt_id,
                cleared_amount=(
                    _money(command.amount, command.currency) if initial_state == "cleared" else ZERO
                ),
            )
        )
        if command.payment_method == "cash":
            await session.execute(
                insert(payment_receipt_events).values(
                    payment_receipt_event_id=uuid4(),
                    payment_receipt_id=command.payment_receipt_id,
                    event_type="cleared",
                    actor_subject=actor.subject,
                    reason="Authorized cash clears immediately",
                    evidence=None,
                    source_id=command.payment_receipt_id,
                    correlation_id=request.state.correlation_id,
                    idempotency_key=f"{idempotency_key}:cleared",
                    occurred_at=command.received_at,
                )
            )
            await session.execute(
                insert(cash_reconciliation_items).values(
                    payment_receipt_id=command.payment_receipt_id,
                    status="pending",
                    expected_amount=_money(command.amount, command.currency),
                )
            )
            if fulfillment_order_id is not None:
                await _designate_available_payment(
                    session,
                    payment_receipt_id=command.payment_receipt_id,
                    fulfillment_order_id=fulfillment_order_id,
                    actor_subject=actor.subject,
                    correlation_id=request.state.correlation_id,
                    idempotency_key=idempotency_key,
                )
        result = await _receipt_response(session, command.payment_receipt_id)
        await store_command_result(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result=result,
        )
    response.headers["X-Idempotency-Replayed"] = "false"
    return result


@router.get(
    "/v1/finance/payment-receipts/{payment_receipt_id}",
    response_model=PaymentReceiptResponse,
    responses=error_responses(401, 403, 404, 500),
)
async def get_payment_receipt(
    payment_receipt_id: UUID,
    actor: Annotated[AuthorizedUser, Depends(require_payment_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> PaymentReceiptResponse:
    result = await _receipt_response(session, payment_receipt_id)
    if result.branch_id not in actor.branch_ids:
        raise AppError(403, "operational_scope_required", "Branch scope is required.")
    return result


@router.post(
    "/v1/finance/payment-receipts/{payment_receipt_id}/verification",
    response_model=PaymentReceiptResponse,
    status_code=201,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def verify_payment_receipt(
    payment_receipt_id: UUID,
    command: PaymentVerificationCommand,
    request: Request,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_payment_verifier)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> PaymentReceiptResponse:
    if idempotency_key is None:
        raise AppError(400, "idempotency_key_required", "Idempotency-Key is required.")
    request_hash = _request_hash("verify-payment-receipt", command, str(payment_receipt_id))
    await session.rollback()
    async with session.begin():
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.status_code = 200
            return PaymentReceiptResponse.model_validate(replay)
        receipt = (
            (
                await session.execute(
                    select(payment_receipts)
                    .where(payment_receipts.c.payment_receipt_id == payment_receipt_id)
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if receipt is None:
            raise AppError(404, "payment_receipt_not_found", "The Payment Receipt does not exist.")
        if receipt["branch_id"] not in actor.branch_ids:
            raise AppError(403, "operational_scope_required", "Branch scope is required.")
        if receipt["recorded_by"] == actor.subject:
            raise AppError(
                409,
                "maker_checker_violation",
                "The Payment Receipt recorder cannot verify the same receipt.",
            )
        state = await session.scalar(
            select(payment_receipt_status.c.state).where(
                payment_receipt_status.c.payment_receipt_id == payment_receipt_id
            )
        )
        if state != "pending_verification":
            raise AppError(
                409,
                "payment_state_conflict",
                "Only Pending Verification receipts can be verified.",
            )
        if command.decision == "rejected":
            await session.execute(
                insert(payment_receipt_events).values(
                    payment_receipt_event_id=uuid4(),
                    payment_receipt_id=payment_receipt_id,
                    event_type="rejected",
                    actor_subject=actor.subject,
                    reason=command.reason,
                    evidence=None,
                    source_id=payment_receipt_id,
                    correlation_id=request.state.correlation_id,
                    idempotency_key=idempotency_key,
                    occurred_at=command.verified_at,
                )
            )
            await session.execute(
                update(payment_receipt_status)
                .where(payment_receipt_status.c.payment_receipt_id == payment_receipt_id)
                .values(
                    state="rejected",
                    verified_by=actor.subject,
                    version=payment_receipt_status.c.version + 1,
                    updated_at=func.now(),
                )
            )
        elif receipt["payment_method_kind"] == "check":
            if command.decision != "evidence_verified":
                raise AppError(
                    422,
                    "check_bank_clearance_required",
                    "A check remains pending until distinct bank clearance evidence is recorded.",
                )
            await session.execute(
                insert(payment_receipt_events).values(
                    payment_receipt_event_id=uuid4(),
                    payment_receipt_id=payment_receipt_id,
                    event_type="verified",
                    actor_subject=actor.subject,
                    reason=command.reason,
                    evidence=None,
                    source_id=payment_receipt_id,
                    correlation_id=request.state.correlation_id,
                    idempotency_key=idempotency_key,
                    occurred_at=command.verified_at,
                )
            )
            await session.execute(
                update(payment_receipt_status)
                .where(payment_receipt_status.c.payment_receipt_id == payment_receipt_id)
                .values(
                    state="pending_clearance",
                    verified_by=actor.subject,
                    version=payment_receipt_status.c.version + 1,
                    updated_at=func.now(),
                )
            )
        else:
            if command.decision != "cleared":
                raise AppError(
                    422,
                    "payment_verification_decision_invalid",
                    "The verification decision is not valid for this Payment Method.",
                )
            await session.execute(
                insert(payment_receipt_events).values(
                    payment_receipt_event_id=uuid4(),
                    payment_receipt_id=payment_receipt_id,
                    event_type="verified",
                    actor_subject=actor.subject,
                    reason=command.reason,
                    evidence=None,
                    source_id=payment_receipt_id,
                    correlation_id=request.state.correlation_id,
                    idempotency_key=idempotency_key,
                    occurred_at=command.verified_at,
                )
            )
            await _clear_receipt(
                session,
                receipt=dict(receipt),
                actor_subject=actor.subject,
                correlation_id=request.state.correlation_id,
                idempotency_key=idempotency_key,
                occurred_at=command.verified_at,
                verified_by=actor.subject,
            )
        result = await _receipt_response(session, payment_receipt_id)
        await store_command_result(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result=result,
        )
    return result


@router.post(
    "/v1/finance/payment-receipts/{payment_receipt_id}/provider-confirmation",
    response_model=PaymentReceiptResponse,
    status_code=201,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def confirm_provider_payment(
    payment_receipt_id: UUID,
    command: ProviderConfirmationCommand,
    request: Request,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_payment_verifier)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> PaymentReceiptResponse:
    if idempotency_key is None:
        raise AppError(400, "idempotency_key_required", "Idempotency-Key is required.")
    request_hash = _request_hash("confirm-provider-payment", command, str(payment_receipt_id))
    await session.rollback()
    async with session.begin():
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.status_code = 200
            return PaymentReceiptResponse.model_validate(replay)
        receipt = (
            (
                await session.execute(
                    select(payment_receipts, payment_receipt_status.c.state)
                    .join(
                        payment_receipt_status,
                        payment_receipts.c.payment_receipt_id
                        == payment_receipt_status.c.payment_receipt_id,
                    )
                    .where(payment_receipts.c.payment_receipt_id == payment_receipt_id)
                    .with_for_update(of=payment_receipt_status)
                )
            )
            .mappings()
            .one_or_none()
        )
        if receipt is None:
            raise AppError(404, "payment_receipt_not_found", "Payment Receipt not found.")
        if receipt["branch_id"] not in actor.branch_ids:
            raise AppError(403, "operational_scope_required", "Branch scope is required.")
        if receipt["recorded_by"] == actor.subject:
            raise AppError(
                409,
                "maker_checker_violation",
                "The Payment recorder cannot confirm the same provider payment.",
            )
        method = (
            (
                await session.execute(
                    select(payment_methods).where(
                        payment_methods.c.payment_method_id == receipt["payment_method_id"]
                    )
                )
            )
            .mappings()
            .one()
        )
        if (
            receipt["state"] != "pending_verification"
            or not method["provider_confirmation_enabled"]
        ):
            raise AppError(
                409,
                "provider_confirmation_not_allowed",
                "This Payment Method is not awaiting an approved provider confirmation.",
            )
        if (
            _normalize_reference(command.provider_reference)
            != receipt["external_reference_normalized"]
        ):
            raise AppError(
                409,
                "provider_reference_conflict",
                "Provider confirmation must match the recorded External Payment Reference.",
            )
        await session.execute(
            insert(payment_receipt_events).values(
                payment_receipt_event_id=uuid4(),
                payment_receipt_id=payment_receipt_id,
                event_type="provider_confirmed",
                actor_subject=actor.subject,
                reason=command.reason,
                evidence={"provider_reference": command.provider_reference},
                source_id=payment_receipt_id,
                correlation_id=request.state.correlation_id,
                idempotency_key=f"{idempotency_key}:provider-confirmed",
                occurred_at=command.confirmed_at,
            )
        )
        await _clear_receipt(
            session,
            receipt=dict(receipt),
            actor_subject=actor.subject,
            correlation_id=request.state.correlation_id,
            idempotency_key=idempotency_key,
            occurred_at=command.confirmed_at,
            verified_by=actor.subject,
        )
        result = await _receipt_response(session, payment_receipt_id)
        await store_command_result(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result=result,
        )
        return result


@router.post(
    "/v1/finance/payment-receipts/{payment_receipt_id}/bank-clearance",
    response_model=PaymentReceiptResponse,
    status_code=201,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def clear_check_payment(
    payment_receipt_id: UUID,
    command: CheckClearanceCommand,
    request: Request,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_check_clearer)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> PaymentReceiptResponse:
    if idempotency_key is None:
        raise AppError(400, "idempotency_key_required", "Idempotency-Key is required.")
    request_hash = _request_hash("clear-check-payment", command, str(payment_receipt_id))
    await session.rollback()
    async with session.begin():
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.status_code = 200
            response.headers["X-Idempotency-Replayed"] = "true"
            return PaymentReceiptResponse.model_validate(replay)
        receipt = (
            (
                await session.execute(
                    select(payment_receipts)
                    .where(payment_receipts.c.payment_receipt_id == payment_receipt_id)
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if receipt is None:
            raise AppError(404, "payment_receipt_not_found", "The Payment Receipt does not exist.")
        if receipt["branch_id"] not in actor.branch_ids:
            raise AppError(403, "operational_scope_required", "Branch scope is required.")
        state = await session.scalar(
            select(payment_receipt_status.c.state).where(
                payment_receipt_status.c.payment_receipt_id == payment_receipt_id
            )
        )
        if receipt["payment_method_kind"] != "check" or state != "pending_clearance":
            raise AppError(
                409,
                "payment_state_conflict",
                "Only a verified check awaiting bank clearance can be cleared.",
            )
        await session.execute(
            insert(payment_receipt_events).values(
                payment_receipt_event_id=uuid4(),
                payment_receipt_id=payment_receipt_id,
                event_type="bank_cleared",
                actor_subject=actor.subject,
                reason=command.reason,
                evidence={"bank_reference": command.bank_reference},
                source_id=payment_receipt_id,
                correlation_id=request.state.correlation_id,
                idempotency_key=idempotency_key,
                occurred_at=command.cleared_at,
            )
        )
        await _clear_receipt(
            session,
            receipt=dict(receipt),
            actor_subject=actor.subject,
            correlation_id=request.state.correlation_id,
            idempotency_key=idempotency_key,
            occurred_at=command.cleared_at,
            verified_by=actor.subject,
        )
        result = await _receipt_response(session, payment_receipt_id)
        await store_command_result(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result=result,
        )
    return result


@router.post(
    "/v1/finance/payment-receipts/{payment_receipt_id}/cash-reconciliation",
    response_model=CashReconciliationResponse,
    status_code=201,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def reconcile_cash_payment(
    payment_receipt_id: UUID,
    command: CashReconciliationCommand,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_cash_reconciler)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> CashReconciliationResponse:
    if idempotency_key is None:
        raise AppError(400, "idempotency_key_required", "Idempotency-Key is required.")
    request_hash = _request_hash("reconcile-cash-payment", command, str(payment_receipt_id))
    await session.rollback()
    async with session.begin():
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.status_code = 200
            response.headers["X-Idempotency-Replayed"] = "true"
            return CashReconciliationResponse.model_validate(replay)
        receipt = (
            (
                await session.execute(
                    select(payment_receipts, payment_receipt_status.c.state)
                    .join(
                        payment_receipt_status,
                        payment_receipts.c.payment_receipt_id
                        == payment_receipt_status.c.payment_receipt_id,
                    )
                    .where(payment_receipts.c.payment_receipt_id == payment_receipt_id)
                    .with_for_update(of=payment_receipt_status)
                )
            )
            .mappings()
            .one_or_none()
        )
        if receipt is None:
            raise AppError(404, "payment_receipt_not_found", "The Payment Receipt does not exist.")
        if receipt["branch_id"] not in actor.branch_ids:
            raise AppError(403, "operational_scope_required", "Branch scope is required.")
        if receipt["state"] != "cleared":
            raise AppError(
                409,
                "cash_reconciliation_payment_state_conflict",
                "Only a Cleared cash Payment Receipt can be reconciled.",
            )
        item = (
            (
                await session.execute(
                    select(cash_reconciliation_items)
                    .where(cash_reconciliation_items.c.payment_receipt_id == payment_receipt_id)
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if item is None:
            raise AppError(
                409,
                "cash_reconciliation_not_required",
                "This Payment Receipt is not an authorized cash collection.",
            )
        if item["status"] == "reconciled":
            raise AppError(
                409,
                "cash_reconciliation_already_completed",
                "Cash has already been reconciled; use an adjustment or reversal.",
            )
        variance = _money(
            command.counted_amount - item["expected_amount"],
            receipt["currency"],
        )
        await session.execute(
            update(cash_reconciliation_items)
            .where(cash_reconciliation_items.c.payment_receipt_id == payment_receipt_id)
            .values(
                status="reconciled",
                counted_amount=_money(command.counted_amount, receipt["currency"]),
                variance_amount=variance,
                cash_reconciliation_id=command.cash_reconciliation_id,
                reconciled_by=actor.subject,
                reconciled_at=command.reconciled_at,
                reason=command.reason,
            )
        )
        await session.execute(
            insert(cash_reconciliation_events).values(
                cash_reconciliation_event_id=uuid4(),
                payment_receipt_id=payment_receipt_id,
                cash_reconciliation_id=command.cash_reconciliation_id,
                event_type="reconciled",
                expected_amount=item["expected_amount"],
                counted_amount=_money(command.counted_amount, receipt["currency"]),
                variance_amount=variance,
                reason=command.reason,
                actor_subject=actor.subject,
                occurred_at=command.reconciled_at,
                idempotency_key=idempotency_key,
            )
        )
        await session.execute(
            update(cod_collections)
            .where(cod_collections.c.payment_receipt_id == payment_receipt_id)
            .values(status="reconciled")
        )
        result = CashReconciliationResponse(
            cash_reconciliation_id=command.cash_reconciliation_id,
            payment_receipt_id=payment_receipt_id,
            status="reconciled",
            counted_amount=_money(command.counted_amount, receipt["currency"]),
            variance_amount=variance,
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
    "/v1/finance/payment-receipts/{payment_receipt_id}/cash-reconciliation/adjustments",
    response_model=CashReconciliationChangeResponse,
    status_code=201,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def adjust_cash_reconciliation(
    payment_receipt_id: UUID,
    command: CashReconciliationAdjustmentCommand,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_cash_reconciler)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> CashReconciliationChangeResponse:
    if idempotency_key is None:
        raise AppError(400, "idempotency_key_required", "Idempotency-Key is required.")
    request_hash = _request_hash("adjust-cash-reconciliation", command, str(payment_receipt_id))
    await session.rollback()
    async with session.begin():
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.status_code = 200
            return CashReconciliationChangeResponse.model_validate(replay)
        receipt = (
            (
                await session.execute(
                    select(payment_receipts, payment_receipt_status.c.state)
                    .join(
                        payment_receipt_status,
                        payment_receipts.c.payment_receipt_id
                        == payment_receipt_status.c.payment_receipt_id,
                    )
                    .where(payment_receipts.c.payment_receipt_id == payment_receipt_id)
                    .with_for_update(of=payment_receipt_status)
                )
            )
            .mappings()
            .one_or_none()
        )
        if receipt is None:
            raise AppError(404, "payment_receipt_not_found", "Payment Receipt not found.")
        if receipt["branch_id"] not in actor.branch_ids:
            raise AppError(403, "operational_scope_required", "Branch scope is required.")
        if receipt["state"] != "cleared":
            raise AppError(
                409,
                "cash_reconciliation_payment_state_conflict",
                "Only a Cleared cash Payment Receipt can be adjusted.",
            )
        item = (
            (
                await session.execute(
                    select(cash_reconciliation_items)
                    .where(cash_reconciliation_items.c.payment_receipt_id == payment_receipt_id)
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if item is None or item["status"] != "reconciled":
            raise AppError(
                409,
                "cash_reconciliation_adjustment_conflict",
                "Only reconciled cash can receive a reasoned adjustment.",
            )
        counted = _money(command.counted_amount, receipt["currency"])
        variance = _money(counted - item["expected_amount"], receipt["currency"])
        await session.execute(
            update(cash_reconciliation_items)
            .where(cash_reconciliation_items.c.payment_receipt_id == payment_receipt_id)
            .values(
                counted_amount=counted,
                variance_amount=variance,
                cash_reconciliation_id=command.cash_reconciliation_id,
                reconciled_by=actor.subject,
                reconciled_at=command.reconciled_at,
                reason=command.reason,
            )
        )
        await session.execute(
            insert(cash_reconciliation_events).values(
                cash_reconciliation_event_id=uuid4(),
                payment_receipt_id=payment_receipt_id,
                cash_reconciliation_id=command.cash_reconciliation_id,
                event_type="adjusted",
                expected_amount=item["expected_amount"],
                counted_amount=counted,
                variance_amount=variance,
                reason=command.reason,
                actor_subject=actor.subject,
                occurred_at=command.reconciled_at,
                idempotency_key=idempotency_key,
            )
        )
        result = CashReconciliationChangeResponse(
            cash_reconciliation_id=command.cash_reconciliation_id,
            payment_receipt_id=payment_receipt_id,
            status="reconciled",
            counted_amount=counted,
            variance_amount=variance,
            event_type="adjusted",
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
    "/v1/finance/payment-receipts/{payment_receipt_id}/cash-reconciliation/reversal",
    response_model=CashReconciliationChangeResponse,
    status_code=201,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def reverse_cash_reconciliation(
    payment_receipt_id: UUID,
    command: CashReconciliationReversalCommand,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_cash_reconciler)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> CashReconciliationChangeResponse:
    if idempotency_key is None:
        raise AppError(400, "idempotency_key_required", "Idempotency-Key is required.")
    request_hash = _request_hash("reverse-cash-reconciliation", command, str(payment_receipt_id))
    await session.rollback()
    async with session.begin():
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.status_code = 200
            return CashReconciliationChangeResponse.model_validate(replay)
        receipt = (
            (
                await session.execute(
                    select(payment_receipts, payment_receipt_status.c.state)
                    .join(
                        payment_receipt_status,
                        payment_receipts.c.payment_receipt_id
                        == payment_receipt_status.c.payment_receipt_id,
                    )
                    .where(payment_receipts.c.payment_receipt_id == payment_receipt_id)
                    .with_for_update(of=payment_receipt_status)
                )
            )
            .mappings()
            .one_or_none()
        )
        if receipt is None:
            raise AppError(404, "payment_receipt_not_found", "Payment Receipt not found.")
        if receipt["branch_id"] not in actor.branch_ids:
            raise AppError(403, "operational_scope_required", "Branch scope is required.")
        if receipt["state"] != "cleared":
            raise AppError(
                409,
                "cash_reconciliation_payment_state_conflict",
                "Only a Cleared cash Payment Receipt can have reconciliation reversed.",
            )
        item = (
            (
                await session.execute(
                    select(cash_reconciliation_items)
                    .where(cash_reconciliation_items.c.payment_receipt_id == payment_receipt_id)
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if item is None or item["status"] != "reconciled":
            raise AppError(
                409,
                "cash_reconciliation_reversal_conflict",
                "Only a current reconciliation can be reversed.",
            )
        counted = cast(Decimal, item["counted_amount"])
        variance = cast(Decimal, item["variance_amount"])
        await session.execute(
            insert(cash_reconciliation_events).values(
                cash_reconciliation_event_id=uuid4(),
                payment_receipt_id=payment_receipt_id,
                cash_reconciliation_id=command.cash_reconciliation_id,
                event_type="reversed",
                expected_amount=item["expected_amount"],
                counted_amount=counted,
                variance_amount=variance,
                reason=command.reason,
                actor_subject=actor.subject,
                occurred_at=command.reversed_at,
                idempotency_key=idempotency_key,
            )
        )
        await session.execute(
            update(cash_reconciliation_items)
            .where(cash_reconciliation_items.c.payment_receipt_id == payment_receipt_id)
            .values(
                status="pending",
                counted_amount=None,
                variance_amount=None,
                cash_reconciliation_id=None,
                reconciled_by=None,
                reconciled_at=None,
                reason=None,
            )
        )
        await session.execute(
            update(cod_collections)
            .where(cod_collections.c.payment_receipt_id == payment_receipt_id)
            .values(status="cleared")
        )
        result = CashReconciliationChangeResponse(
            cash_reconciliation_id=command.cash_reconciliation_id,
            payment_receipt_id=payment_receipt_id,
            status="pending",
            counted_amount=counted,
            variance_amount=variance,
            event_type="reversed",
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
    "/v1/finance/payment-receipts/{payment_receipt_id}/reversal",
    response_model=PaymentReversalResponse,
    status_code=201,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def reverse_payment_receipt(
    payment_receipt_id: UUID,
    command: PaymentReversalCommand,
    request: Request,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_payment_reverser)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> PaymentReversalResponse:
    if idempotency_key is None:
        raise AppError(400, "idempotency_key_required", "Idempotency-Key is required.")
    request_hash = _request_hash("reverse-payment-receipt", command, str(payment_receipt_id))
    await session.rollback()
    async with session.begin():
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.status_code = 200
            response.headers["X-Idempotency-Replayed"] = "true"
            return PaymentReversalResponse.model_validate(replay)
        receipt = (
            (
                await session.execute(
                    select(payment_receipts)
                    .where(payment_receipts.c.payment_receipt_id == payment_receipt_id)
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if receipt is None:
            raise AppError(404, "payment_receipt_not_found", "The Payment Receipt does not exist.")
        if receipt["branch_id"] not in actor.branch_ids:
            raise AppError(403, "operational_scope_required", "Branch scope is required.")
        status = (
            (
                await session.execute(
                    select(payment_receipt_status)
                    .where(payment_receipt_status.c.payment_receipt_id == payment_receipt_id)
                    .with_for_update()
                )
            )
            .mappings()
            .one()
        )
        if status["state"] != "cleared":
            raise AppError(
                409,
                "payment_state_conflict",
                "Only a Cleared Payment Receipt can be reversed.",
            )
        coverage_ids = (
            await session.execute(
                select(prepayment_coverage_events.c.fulfillment_order_id)
                .where(prepayment_coverage_events.c.payment_receipt_id == payment_receipt_id)
                .distinct()
            )
        ).scalars()
        for fulfillment_order_id in coverage_ids:
            state = await session.scalar(
                select(fulfillment_order_state.c.status).where(
                    fulfillment_order_state.c.fulfillment_order_id == fulfillment_order_id
                )
            )
            if state == "pick_released":
                raise AppError(
                    409,
                    "picked_payment_reversal_requires_resolution",
                    "Picked fulfillment requires an explicit payment exception workflow.",
                )
            await _release_coverage(
                session,
                fulfillment_order_id=fulfillment_order_id,
                actor_subject=actor.subject,
                correlation_id=request.state.correlation_id,
                idempotency_key=idempotency_key,
                reason=command.reason,
            )
        await session.execute(
            insert(payment_receipt_events).values(
                payment_receipt_event_id=command.payment_reversal_id,
                payment_receipt_id=payment_receipt_id,
                event_type="reversed",
                actor_subject=actor.subject,
                reason=command.reason,
                evidence=None,
                source_id=command.payment_reversal_id,
                correlation_id=request.state.correlation_id,
                idempotency_key=idempotency_key,
                occurred_at=command.reversed_at,
            )
        )
        await session.execute(
            update(payment_receipt_status)
            .where(payment_receipt_status.c.payment_receipt_id == payment_receipt_id)
            .values(
                state="reversed",
                reversal_id=command.payment_reversal_id,
                version=payment_receipt_status.c.version + 1,
                updated_at=func.now(),
            )
        )
        await session.execute(
            update(payment_receipt_balances)
            .where(payment_receipt_balances.c.payment_receipt_id == payment_receipt_id)
            .values(
                reversed_amount=receipt["amount"],
                version=payment_receipt_balances.c.version + 1,
            )
        )
        await session.execute(
            update(cod_collections)
            .where(cod_collections.c.payment_receipt_id == payment_receipt_id)
            .values(status="reversed")
        )
        result = PaymentReversalResponse(
            payment_reversal_id=command.payment_reversal_id,
            original_payment_receipt_id=payment_receipt_id,
            amount=_money(-receipt["amount"], receipt["currency"]),
            reason=command.reason,
        )
        await store_command_result(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result=result,
        )
    return result


@router.get(
    "/v1/fulfillment/orders",
    response_model=FulfillmentOrderListResponse,
    responses=error_responses(401, 403, 404, 500),
)
async def list_fulfillment_orders(
    actor: Annotated[AuthorizedUser, Depends(require_pick_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    sales_order_id: Annotated[UUID | None, Query()] = None,
) -> FulfillmentOrderListResponse:
    query = select(fulfillment_orders.c.fulfillment_order_id).where(
        fulfillment_orders.c.branch_id.in_(actor.branch_ids),
        fulfillment_orders.c.warehouse_id.in_(actor.warehouse_ids),
    )
    if sales_order_id is not None:
        query = query.where(fulfillment_orders.c.sales_order_id == sales_order_id)
    rows = (
        await session.execute(
            query.order_by(
                fulfillment_orders.c.created_at,
                fulfillment_orders.c.reservation_generation,
            )
        )
    ).scalars()
    items = [await _fulfillment_response(session, row) for row in rows]
    return FulfillmentOrderListResponse(items=items, total=len(items))


@router.post(
    "/v1/fulfillment/orders/{fulfillment_order_id}/pick-release",
    response_model=PickReleaseResponse,
    status_code=201,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def release_fulfillment_to_pick(
    fulfillment_order_id: UUID,
    command: PickReleaseCommand,
    request: Request,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_pick_releaser)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> PickReleaseResponse:
    if idempotency_key is None:
        raise AppError(400, "idempotency_key_required", "Idempotency-Key is required.")
    request_hash = _request_hash("pick-release", command, str(fulfillment_order_id))
    await session.rollback()
    async with session.begin():
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.status_code = 200
            return PickReleaseResponse.model_validate(replay)
        await _advisory_lock(session, f"prepayment:{fulfillment_order_id}")
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
                404, "fulfillment_order_not_found", "The Fulfillment Order does not exist."
            )
        if (
            order["branch_id"] not in actor.branch_ids
            or order["warehouse_id"] not in actor.warehouse_ids
        ):
            raise AppError(
                403,
                "operational_scope_required",
                "Branch and Warehouse scope are required.",
            )
        if order["status"] == "payment_hold":
            raise AppError(
                409,
                "reservation_retry_required",
                "Later payment cannot revive an expired reservation; reserve again first.",
            )
        if order["status"] == "pick_released":
            raise AppError(
                409,
                "pick_release_conflict",
                "The Fulfillment Order is already released for picking.",
            )
        if order["status"] not in {"reserved", "payment_ready"}:
            raise AppError(
                409,
                "fulfillment_state_conflict",
                "The Fulfillment Order is not eligible for Pick Release.",
            )
        if order["covered_amount"] < order["payment_required"]:
            shortfall = order["payment_required"] - order["covered_amount"]
            raise AppError(
                409,
                "cleared_payment_insufficient",
                "Cleared Customer Prepayment does not cover reserved value.",
                details={
                    "cleared_payment": f"{order['covered_amount']:.2f}",
                    "payment_required": f"{order['payment_required']:.2f}",
                    "shortfall": f"{shortfall:.2f}",
                },
            )
        active_approval = await session.scalar(
            select(commercial_approvals.c.commercial_approval_id)
            .outerjoin(
                commercial_approval_invalidations,
                commercial_approvals.c.commercial_approval_id
                == commercial_approval_invalidations.c.commercial_approval_id,
            )
            .where(
                commercial_approvals.c.commercial_approval_id == order["commercial_approval_id"],
                commercial_approval_invalidations.c.invalidation_id.is_(None),
            )
        )
        if active_approval is None:
            raise AppError(
                409,
                "commercial_approval_invalid",
                "The Commercial Approval is no longer active.",
            )
        pick_release_id = uuid4()
        await session.execute(
            insert(pick_releases).values(
                pick_release_id=pick_release_id,
                fulfillment_order_id=fulfillment_order_id,
                quantity_base=order["reserved_quantity_base"],
                payment_required=order["payment_required"],
                cleared_payment=order["covered_amount"],
                reason=command.reason,
                released_by=actor.subject,
                correlation_id=request.state.correlation_id,
                idempotency_key=idempotency_key,
            )
        )
        releasable_lines = (
            (
                await session.execute(
                    select(
                        fulfillment_order_lines.c.line_id,
                        fulfillment_order_lines.c.reserved_quantity_base,
                    ).where(
                        fulfillment_order_lines.c.fulfillment_order_id == fulfillment_order_id,
                        fulfillment_order_lines.c.reserved_quantity_base > ZERO,
                    )
                )
            )
            .mappings()
            .all()
        )
        for line in releasable_lines:
            await session.execute(
                insert(fulfillment_line_pick_state).values(
                    fulfillment_order_id=fulfillment_order_id,
                    line_id=line["line_id"],
                    released_quantity_base=line["reserved_quantity_base"],
                )
            )
        await session.execute(
            update(fulfillment_order_state)
            .where(fulfillment_order_state.c.fulfillment_order_id == fulfillment_order_id)
            .values(
                status="pick_released",
                version=fulfillment_order_state.c.version + 1,
                updated_at=func.now(),
            )
        )
        result = PickReleaseResponse(
            pick_release_id=pick_release_id,
            fulfillment_order_id=fulfillment_order_id,
            status="released",
            quantity_base=order["reserved_quantity_base"],
            payment_required=_money(order["payment_required"], order["currency"]),
            cleared_payment=_money(order["covered_amount"], order["currency"]),
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
    "/v1/fulfillment/payment-deadlines/process",
    response_model=PaymentDeadlineResponse,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def process_payment_deadline(
    command: ProcessPaymentDeadlineCommand,
    request: Request,
    actor: Annotated[AuthorizedUser, Depends(require_payment_deadline_processor)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> PaymentDeadlineResponse:
    if idempotency_key is None:
        raise AppError(400, "idempotency_key_required", "Idempotency-Key is required.")
    request_hash = _request_hash(
        "process-payment-deadline",
        command,
        str(command.fulfillment_order_id),
    )
    await session.rollback()
    async with session.begin():
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            return PaymentDeadlineResponse.model_validate(replay)
        await _advisory_lock(session, f"prepayment:{command.fulfillment_order_id}")
        order = (
            (
                await session.execute(
                    select(fulfillment_orders, fulfillment_order_state)
                    .join(
                        fulfillment_order_state,
                        fulfillment_orders.c.fulfillment_order_id
                        == fulfillment_order_state.c.fulfillment_order_id,
                    )
                    .where(
                        fulfillment_orders.c.fulfillment_order_id == command.fulfillment_order_id
                    )
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
        if (
            order["branch_id"] not in actor.branch_ids
            or order["warehouse_id"] not in actor.warehouse_ids
        ):
            raise AppError(
                403,
                "operational_scope_required",
                "Branch and Warehouse scope are required.",
            )
        now = command.as_of or await session.scalar(select(func.clock_timestamp()))
        if order["status"] == "pick_released" or (
            order["covered_amount"] >= order["payment_required"]
            and order["status"] in {"reserved", "payment_ready"}
        ):
            result = PaymentDeadlineResponse(
                fulfillment_order_id=command.fulfillment_order_id,
                status="payment_satisfied",
                released_quantity_base=ZERO,
                backorder_quantity_base=order["backorder_quantity_base"],
            )
        elif order["status"] == "payment_hold":
            result = PaymentDeadlineResponse(
                fulfillment_order_id=command.fulfillment_order_id,
                status="payment_hold",
                released_quantity_base=ZERO,
                backorder_quantity_base=order["backorder_quantity_base"],
            )
        elif order["payment_deadline_at"] is None or now < order["payment_deadline_at"]:
            result = PaymentDeadlineResponse(
                fulfillment_order_id=command.fulfillment_order_id,
                status="not_due",
                released_quantity_base=ZERO,
                backorder_quantity_base=order["backorder_quantity_base"],
            )
        else:
            commitments = (
                (
                    await session.execute(
                        select(sales_order_line_commitments)
                        .where(
                            sales_order_line_commitments.c.commercial_approval_id
                            == order["commercial_approval_id"]
                        )
                        .order_by(
                            sales_order_line_commitments.c.warehouse_id,
                            sales_order_line_commitments.c.sku_id,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .all()
            )
            for commitment in commitments:
                reserved = commitment["reserved_quantity_base"]
                if reserved <= ZERO:
                    continue
                await _advisory_lock(
                    session,
                    f"reservation:{commitment['warehouse_id']}:{commitment['sku_id']}",
                )
                await session.execute(
                    insert(inventory_reservation_events).values(
                        reservation_event_id=uuid4(),
                        commercial_approval_id=order["commercial_approval_id"],
                        sales_order_id=order["sales_order_id"],
                        sales_order_revision_id=order["sales_order_revision_id"],
                        line_id=commitment["line_id"],
                        sku_id=commitment["sku_id"],
                        warehouse_id=commitment["warehouse_id"],
                        event_type="released",
                        quantity_base=reserved,
                        reason="Prepaid Payment Deadline expired",
                        actor_subject=actor.subject,
                        correlation_id=request.state.correlation_id,
                        idempotency_key=f"{idempotency_key}:deadline",
                    )
                )
                await session.execute(
                    update(inventory_reserved_by_sku_warehouse)
                    .where(
                        inventory_reserved_by_sku_warehouse.c.sku_id == commitment["sku_id"],
                        inventory_reserved_by_sku_warehouse.c.warehouse_id
                        == commitment["warehouse_id"],
                    )
                    .values(
                        reserved_quantity_base=(
                            inventory_reserved_by_sku_warehouse.c.reserved_quantity_base - reserved
                        ),
                        version=inventory_reserved_by_sku_warehouse.c.version + 1,
                        updated_at=func.now(),
                    )
                )
                await session.execute(
                    update(sales_order_line_commitments)
                    .where(
                        sales_order_line_commitments.c.sales_order_id
                        == commitment["sales_order_id"],
                        sales_order_line_commitments.c.line_id == commitment["line_id"],
                    )
                    .values(
                        reserved_quantity_base=ZERO,
                        backorder_quantity_base=commitment["ordered_quantity_base"],
                    )
                )
            await _release_coverage(
                session,
                fulfillment_order_id=command.fulfillment_order_id,
                actor_subject=actor.subject,
                correlation_id=request.state.correlation_id,
                idempotency_key=idempotency_key,
                reason="Prepaid Payment Deadline expired",
            )
            hold_event_id = uuid4()
            await session.execute(
                insert(sales_order_hold_events).values(
                    hold_event_id=hold_event_id,
                    sales_order_id=order["sales_order_id"],
                    fulfillment_order_id=command.fulfillment_order_id,
                    hold_type="payment",
                    event_type="applied",
                    reason="Prepaid Payment Deadline expired",
                    actor_subject=actor.subject,
                    correlation_id=request.state.correlation_id,
                    idempotency_key=idempotency_key,
                )
            )
            await session.execute(
                pg_insert(active_sales_order_holds)
                .values(
                    sales_order_id=order["sales_order_id"],
                    hold_type="payment",
                    fulfillment_order_id=command.fulfillment_order_id,
                    hold_event_id=hold_event_id,
                )
                .on_conflict_do_nothing()
            )
            new_backorder = order["backorder_quantity_base"] + order["reserved_quantity_base"]
            await session.execute(
                update(fulfillment_order_state)
                .where(
                    fulfillment_order_state.c.fulfillment_order_id == command.fulfillment_order_id
                )
                .values(
                    status="payment_hold",
                    reserved_quantity_base=ZERO,
                    backorder_quantity_base=new_backorder,
                    covered_amount=ZERO,
                    payment_hold=True,
                    version=fulfillment_order_state.c.version + 1,
                    updated_at=func.now(),
                )
            )
            await session.execute(
                update(sales_orders)
                .where(sales_orders.c.sales_order_id == order["sales_order_id"])
                .values(status="held", updated_by=actor.subject, updated_at=func.now())
            )
            result = PaymentDeadlineResponse(
                fulfillment_order_id=command.fulfillment_order_id,
                status="payment_hold",
                released_quantity_base=order["reserved_quantity_base"],
                backorder_quantity_base=new_backorder,
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
    "/v1/sales/orders/{sales_order_id}/reservation-retry",
    response_model=ReservationRetryResponse,
    status_code=201,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def retry_sales_order_reservation(
    sales_order_id: UUID,
    command: ReservationRetryCommand,
    request: Request,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_reservation_retrier)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> ReservationRetryResponse:
    if idempotency_key is None:
        raise AppError(400, "idempotency_key_required", "Idempotency-Key is required.")
    request_hash = _request_hash("retry-sales-order-reservation", command, str(sales_order_id))
    await session.rollback()
    async with session.begin():
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.status_code = 200
            response.headers["X-Idempotency-Replayed"] = "true"
            return ReservationRetryResponse.model_validate(replay)
        order = (
            (
                await session.execute(
                    select(sales_orders)
                    .where(sales_orders.c.sales_order_id == sales_order_id)
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if order is None:
            raise AppError(404, "sales_order_not_found", "The Sales Order does not exist.")
        warehouse = (
            (
                await session.execute(
                    select(warehouses).where(
                        warehouses.c.warehouse_id == command.warehouse_id,
                        warehouses.c.branch_id == order["branch_id"],
                        warehouses.c.is_active.is_(True),
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if (
            warehouse is None
            or order["branch_id"] not in actor.branch_ids
            or command.warehouse_id not in actor.warehouse_ids
        ):
            raise AppError(
                403,
                "operational_scope_required",
                "Branch and Warehouse scope are required.",
            )
        hold = (
            (
                await session.execute(
                    select(active_sales_order_holds).where(
                        active_sales_order_holds.c.sales_order_id == sales_order_id,
                        active_sales_order_holds.c.hold_type == "payment",
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if hold is None:
            raise AppError(
                409,
                "payment_hold_required",
                "Reservation Retry requires an active Payment Hold.",
            )
        await _advisory_lock(session, f"prepayment:{hold['fulfillment_order_id']}")
        approval_id = await session.scalar(
            select(commercial_approvals.c.commercial_approval_id)
            .outerjoin(
                commercial_approval_invalidations,
                commercial_approvals.c.commercial_approval_id
                == commercial_approval_invalidations.c.commercial_approval_id,
            )
            .where(
                commercial_approvals.c.sales_order_id == sales_order_id,
                commercial_approvals.c.warehouse_id == command.warehouse_id,
                commercial_approval_invalidations.c.invalidation_id.is_(None),
            )
        )
        if approval_id is None:
            raise AppError(
                409,
                "commercial_approval_invalid",
                "An active Commercial Approval is required before Reservation Retry.",
            )
        commitments = (
            (
                await session.execute(
                    select(sales_order_line_commitments)
                    .where(sales_order_line_commitments.c.commercial_approval_id == approval_id)
                    .order_by(sales_order_line_commitments.c.sku_id)
                    .with_for_update()
                )
            )
            .mappings()
            .all()
        )
        remaining_by_sku: dict[UUID, Decimal] = {}
        for sku_id in sorted({row["sku_id"] for row in commitments}, key=str):
            await _advisory_lock(session, f"reservation:{command.warehouse_id}:{sku_id}")
            on_hand = (
                await session.scalar(
                    select(func.coalesce(func.sum(inventory_availability.c.on_hand), ZERO))
                    .select_from(
                        inventory_availability.join(
                            warehouse_stock_locations,
                            inventory_availability.c.location_id
                            == warehouse_stock_locations.c.location_id,
                        )
                    )
                    .where(
                        inventory_availability.c.sku_id == sku_id,
                        inventory_availability.c.warehouse_id == command.warehouse_id,
                        warehouse_stock_locations.c.custody == "available",
                        warehouse_stock_locations.c.is_active.is_(True),
                        or_(
                            inventory_availability.c.expiration_date.is_(None),
                            inventory_availability.c.expiration_date >= datetime.now(UTC).date(),
                        ),
                    )
                )
            ) or ZERO
            reserved = (
                await session.scalar(
                    select(inventory_reserved_by_sku_warehouse.c.reserved_quantity_base).where(
                        inventory_reserved_by_sku_warehouse.c.sku_id == sku_id,
                        inventory_reserved_by_sku_warehouse.c.warehouse_id == command.warehouse_id,
                    )
                )
            ) or ZERO
            remaining_by_sku[sku_id] = max(on_hand - reserved, ZERO)
        total_reserved = ZERO
        total_backorder = ZERO
        delta_by_sku: dict[UUID, Decimal] = {}
        for commitment in commitments:
            reserve = min(
                commitment["ordered_quantity_base"],
                remaining_by_sku[commitment["sku_id"]],
            )
            backorder = commitment["ordered_quantity_base"] - reserve
            remaining_by_sku[commitment["sku_id"]] -= reserve
            total_reserved += reserve
            total_backorder += backorder
            delta_by_sku[commitment["sku_id"]] = (
                delta_by_sku.get(commitment["sku_id"], ZERO) + reserve
            )
            await session.execute(
                update(sales_order_line_commitments)
                .where(
                    sales_order_line_commitments.c.sales_order_id == sales_order_id,
                    sales_order_line_commitments.c.line_id == commitment["line_id"],
                )
                .values(
                    reserved_quantity_base=reserve,
                    backorder_quantity_base=backorder,
                )
            )
            if reserve > ZERO:
                await session.execute(
                    insert(inventory_reservation_events).values(
                        reservation_event_id=uuid4(),
                        commercial_approval_id=approval_id,
                        sales_order_id=sales_order_id,
                        sales_order_revision_id=commitment["sales_order_revision_id"],
                        line_id=commitment["line_id"],
                        sku_id=commitment["sku_id"],
                        warehouse_id=command.warehouse_id,
                        event_type="reserved",
                        quantity_base=reserve,
                        reason=command.reason,
                        actor_subject=actor.subject,
                        correlation_id=request.state.correlation_id,
                        idempotency_key=f"{idempotency_key}:retry",
                    )
                )
        if total_reserved <= ZERO:
            raise AppError(
                409,
                "inventory_unavailable",
                "Reservation Retry found no eligible Warehouse quantity.",
            )
        for sku_id, amount in delta_by_sku.items():
            if amount <= ZERO:
                continue
            await session.execute(
                pg_insert(inventory_reserved_by_sku_warehouse)
                .values(
                    sku_id=sku_id,
                    warehouse_id=command.warehouse_id,
                    reserved_quantity_base=amount,
                )
                .on_conflict_do_update(
                    index_elements=[
                        inventory_reserved_by_sku_warehouse.c.sku_id,
                        inventory_reserved_by_sku_warehouse.c.warehouse_id,
                    ],
                    set_={
                        "reserved_quantity_base": (
                            inventory_reserved_by_sku_warehouse.c.reserved_quantity_base + amount
                        ),
                        "version": inventory_reserved_by_sku_warehouse.c.version + 1,
                        "updated_at": func.now(),
                    },
                )
            )
        fulfillment_order_id = await create_fulfillment_for_approval(
            session,
            approval_id=approval_id,
            actor_subject=actor.subject,
            correlation_id=request.state.correlation_id,
            idempotency_key=idempotency_key,
        )
        if fulfillment_order_id is None:
            raise AppError(409, "inventory_unavailable", "No quantity was reserved.")
        released_hold_event = uuid4()
        await session.execute(
            insert(sales_order_hold_events).values(
                hold_event_id=released_hold_event,
                sales_order_id=sales_order_id,
                fulfillment_order_id=fulfillment_order_id,
                hold_type="payment",
                event_type="released",
                reason=command.reason,
                actor_subject=actor.subject,
                correlation_id=request.state.correlation_id,
                idempotency_key=idempotency_key,
            )
        )
        await session.execute(
            delete(active_sales_order_holds).where(
                active_sales_order_holds.c.sales_order_id == sales_order_id,
                active_sales_order_holds.c.hold_type == "payment",
            )
        )
        await session.execute(
            update(sales_orders)
            .where(sales_orders.c.sales_order_id == sales_order_id)
            .values(status="approved", updated_by=actor.subject, updated_at=func.now())
        )
        cleared_receipts = (
            await session.execute(
                select(payment_receipts.c.payment_receipt_id)
                .join(
                    payment_receipt_status,
                    payment_receipts.c.payment_receipt_id
                    == payment_receipt_status.c.payment_receipt_id,
                )
                .where(
                    payment_receipts.c.intended_sales_order_id == sales_order_id,
                    payment_receipt_status.c.state == "cleared",
                )
                .order_by(payment_receipts.c.received_at, payment_receipts.c.payment_receipt_id)
            )
        ).scalars()
        for payment_receipt_id in cleared_receipts:
            await _designate_available_payment(
                session,
                payment_receipt_id=payment_receipt_id,
                fulfillment_order_id=fulfillment_order_id,
                actor_subject=actor.subject,
                correlation_id=request.state.correlation_id,
                idempotency_key=f"{idempotency_key}:redesignate",
            )
        result = ReservationRetryResponse(
            sales_order_id=sales_order_id,
            fulfillment_order_id=fulfillment_order_id,
            status="approved",
            payment_hold=False,
            reserved_quantity_base=total_reserved,
            backorder_quantity_base=total_backorder,
        )
        await store_command_result(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result=result,
        )
    return result
