from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256
from typing import Annotated, Literal, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from tradeflow_api.auth import AuthorizedUser, require_order_canceller
from tradeflow_api.command_receipts import get_command_replay, store_command_result
from tradeflow_api.database import get_database_session
from tradeflow_api.errors import AppError, error_responses
from tradeflow_api.models import (
    active_sales_order_holds,
    approval_authorities,
    commercial_approval_invalidations,
    commercial_approvals,
    credit_exposure_entries,
    customer_credit_exposure,
    delivery_confirmation_lines,
    delivery_confirmations,
    delivery_dispatches,
    fulfillment_order_lines,
    fulfillment_order_state,
    fulfillment_orders,
    inventory_reservation_events,
    inventory_reserved_by_sku_warehouse,
    sales_order_cancellation_lines,
    sales_order_cancellations,
    sales_order_hold_events,
    sales_order_line_commitments,
    sales_order_line_revisions,
    sales_order_revisions,
    sales_orders,
)
from tradeflow_api.money import currency_quantum

router = APIRouter(prefix="/v1/sales", tags=["sales"])
ZERO = Decimal("0")


class CommandModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CancelSalesOrderLineCommand(CommandModel):
    line_id: UUID
    cancel_quantity_base: Decimal = Field(gt=0, max_digits=18, decimal_places=6)


class CancelSalesOrderCommand(CommandModel):
    lines: list[CancelSalesOrderLineCommand] = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_lines(self) -> CancelSalesOrderCommand:
        line_ids = [line.line_id for line in self.lines]
        if len(line_ids) != len(set(line_ids)):
            raise ValueError("Sales Order Line identities must be unique.")
        return self


class CancelledLineResponse(BaseModel):
    line_id: UUID
    cancelled_quantity_base: Decimal
    reserved_released_quantity_base: Decimal
    backorder_reduced_quantity_base: Decimal
    line_total_delta: Decimal


class CancelSalesOrderResponse(BaseModel):
    cancellation_id: UUID
    sales_order_id: UUID
    status: Literal["approved", "partially_cancelled", "cancelled", "held"]
    reason: str
    cancelled_by: str
    lines: list[CancelledLineResponse]
    total_cancelled_quantity_base: Decimal
    reserved_released_quantity_base: Decimal
    backorder_reduced_quantity_base: Decimal
    credit_released_base: Decimal


@dataclass(frozen=True, slots=True)
class _LineCancellation:
    line_id: UUID
    sku_id: UUID
    ordered_quantity_base: Decimal
    open_quantity_base: Decimal
    cancel_quantity_base: Decimal
    reserved_released_quantity_base: Decimal
    backorder_reduced_quantity_base: Decimal
    line_total: Decimal
    line_total_delta: Decimal
    warehouse_id: UUID
    commercial_approval_id: UUID
    sales_order_revision_id: UUID


def _request_hash(command: BaseModel, sales_order_id: UUID, if_match: int) -> str:
    payload = (
        f"cancel-sales-order:{sales_order_id}:{if_match}:"
        f"{command.model_dump_json(exclude_none=False)}"
    )
    return sha256(payload.encode()).hexdigest()


def _money(value: Decimal, currency: str) -> Decimal:
    return value.quantize(currency_quantum(currency), ROUND_HALF_UP)


async def _advisory_lock(session: AsyncSession, key: str) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": key},
    )


async def _authority(
    session: AsyncSession,
    *,
    actor: AuthorizedUser,
    branch_id: UUID,
    warehouse_id: UUID,
    maker_subject: str,
    amount: Decimal,
) -> dict[str, object]:
    capability = "sales:order-cancel"
    if capability not in actor.capabilities:
        raise AppError(
            403,
            "capability_required",
            f"The '{capability}' capability is required.",
        )
    warehouse_row = (
        (
            await session.execute(
                select(approval_authorities).where(
                    approval_authorities.c.user_subject == actor.subject,
                    approval_authorities.c.capability_code == capability,
                    approval_authorities.c.branch_id == branch_id,
                    approval_authorities.c.warehouse_id == warehouse_id,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    row = warehouse_row
    if row is None:
        row = (
            (
                await session.execute(
                    select(approval_authorities).where(
                        approval_authorities.c.user_subject == actor.subject,
                        approval_authorities.c.capability_code == capability,
                        approval_authorities.c.branch_id == branch_id,
                        approval_authorities.c.warehouse_id.is_(None),
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
            "Explicit 'sales:order-cancel' Approval Authority is required.",
        )
    if row["maker_checker_required"] and actor.subject == maker_subject:
        raise AppError(
            409,
            "maker_checker_violation",
            "The order maker cannot cancel the same Sales Order.",
        )
    if row["maximum_amount"] is not None and amount > row["maximum_amount"]:
        raise AppError(
            403,
            "approval_limit_exceeded",
            "The cancellation value exceeds the approver's amount authority.",
        )
    return {
        "approval_authority_id": row["approval_authority_id"],
        "capability": capability,
        "maximum_amount": (
            str(row["maximum_amount"]) if row["maximum_amount"] is not None else None
        ),
        "maker_checker_required": row["maker_checker_required"],
    }


async def _active_approval_id(
    session: AsyncSession, sales_order_id: UUID
) -> UUID | None:
    return await session.scalar(
        select(commercial_approvals.c.commercial_approval_id)
        .outerjoin(
            commercial_approval_invalidations,
            commercial_approvals.c.commercial_approval_id
            == commercial_approval_invalidations.c.commercial_approval_id,
        )
        .where(
            commercial_approvals.c.sales_order_id == sales_order_id,
            commercial_approval_invalidations.c.invalidation_id.is_(None),
        )
    )


async def _delivered_by_line(
    session: AsyncSession, sales_order_id: UUID
) -> dict[UUID, Decimal]:
    rows = (
        (
            await session.execute(
                select(
                    delivery_confirmation_lines.c.line_id,
                    func.coalesce(
                        func.sum(delivery_confirmation_lines.c.accepted_quantity_base), ZERO
                    ).label("delivered_quantity_base"),
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
                .where(delivery_dispatches.c.sales_order_id == sales_order_id)
                .group_by(delivery_confirmation_lines.c.line_id)
            )
        )
        .mappings()
        .all()
    )
    return {row["line_id"]: row["delivered_quantity_base"] for row in rows}


async def _latest_active_fulfillment_order(
    session: AsyncSession,
    sales_order_id: UUID,
) -> RowMapping | None:
    return (
        (
            await session.execute(
                select(fulfillment_orders, fulfillment_order_state)
                .join(
                    fulfillment_order_state,
                    fulfillment_orders.c.fulfillment_order_id
                    == fulfillment_order_state.c.fulfillment_order_id,
                )
                .where(
                    fulfillment_orders.c.sales_order_id == sales_order_id,
                    fulfillment_order_state.c.status.not_in_(
                        ("cancelled", "delivered")
                    ),
                )
                .order_by(fulfillment_orders.c.reservation_generation.desc())
                .limit(1)
            )
        )
        .mappings()
        .one_or_none()
    )


@router.post(
    "/orders/{sales_order_id}/cancellation",
    response_model=CancelSalesOrderResponse,
    status_code=201,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
async def cancel_sales_order(
    sales_order_id: UUID,
    command: CancelSalesOrderCommand,
    request: Request,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_order_canceller)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    if_match: Annotated[int, Header(alias="If-Match", ge=1)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
) -> CancelSalesOrderResponse:
    request_hash = _request_hash(command, sales_order_id, if_match)
    await session.rollback()
    async with session.begin():
        scoped_order = (
            (
                await session.execute(
                    select(
                        sales_orders.c.branch_id,
                        sales_orders.c.sales_order_id,
                    ).where(sales_orders.c.sales_order_id == sales_order_id)
                )
            )
            .mappings()
            .one_or_none()
        )
        if scoped_order is None:
            raise AppError(404, "sales_order_not_found", "The Sales Order does not exist.")
        if scoped_order["branch_id"] not in actor.branch_ids:
            raise AppError(403, "operational_scope_required", "Branch scope is required.")
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.status_code = 200
            return CancelSalesOrderResponse.model_validate(replay)

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
        if order["branch_id"] not in actor.branch_ids:
            raise AppError(403, "operational_scope_required", "Branch scope is required.")
        if order["metadata_version"] != if_match:
            raise AppError(
                409,
                "optimistic_version_conflict",
                "The Sales Order changed and requires explicit review.",
            )
        if order["status"] not in ("approved", "held", "partially_cancelled"):
            raise AppError(
                409,
                "sales_order_not_cancellable",
                "Only an approved or held Sales Order can be cancelled.",
            )

        revision = (
            (
                await session.execute(
                    select(sales_order_revisions).where(
                        sales_order_revisions.c.sales_order_revision_id
                        == order["approved_revision_id"]
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if revision is None:
            raise AppError(
                409,
                "sales_order_not_approved",
                "The Sales Order does not have an active approved revision.",
            )

        fulfillment_warehouse_id = order["fulfillment_warehouse_id"]
        if fulfillment_warehouse_id is None or fulfillment_warehouse_id not in actor.warehouse_ids:
            raise AppError(
                403,
                "warehouse_scope_required",
                "Warehouse scope is required for cancellation.",
            )

        line_rows = {
            row["line_id"]: row
            for row in (
                (
                    await session.execute(
                        select(sales_order_line_revisions).where(
                            sales_order_line_revisions.c.sales_order_revision_id
                            == revision["sales_order_revision_id"]
                        )
                    )
                )
                .mappings()
                .all()
            )
        }
        requested_line_ids = {line.line_id for line in command.lines}
        if requested_line_ids - set(line_rows.keys()):
            raise AppError(
                422,
                "sales_order_line_not_found",
                "A requested Sales Order Line does not exist on the approved revision.",
            )

        commitments = {
            row["line_id"]: row
            for row in (
                (
                    await session.execute(
                        select(sales_order_line_commitments)
                        .where(
                            sales_order_line_commitments.c.sales_order_id == sales_order_id,
                            sales_order_line_commitments.c.line_id.in_(requested_line_ids),
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .all()
            )
        }
        delivered_by_line = await _delivered_by_line(session, sales_order_id)

        cancellations: list[_LineCancellation] = []
        total_cancel_value = ZERO
        for input_line in command.lines:
            line_revision = line_rows[input_line.line_id]
            commitment = commitments.get(input_line.line_id)
            if commitment is None:
                raise AppError(
                    409,
                    "sales_order_line_commitment_missing",
                    "The Sales Order Line commitment is not active.",
                )
            delivered = delivered_by_line.get(input_line.line_id, ZERO)
            open_quantity = (
                commitment["ordered_quantity_base"]
                - commitment["picked_quantity_base"]
                - delivered
                - commitment["cancelled_quantity_base"]
            )
            if input_line.cancel_quantity_base > open_quantity:
                raise AppError(
                    409,
                    "cancel_quantity_exceeds_open",
                    "Cancelled quantity cannot exceed the open order quantity.",
                )
            reserved_release = min(
                input_line.cancel_quantity_base,
                commitment["reserved_quantity_base"],
            )
            backorder_reduce = input_line.cancel_quantity_base - reserved_release
            line_total_delta = _money(
                line_revision["line_total"]
                * input_line.cancel_quantity_base
                / line_revision["quantity_base"],
                revision["currency"],
            )
            total_cancel_value += line_total_delta
            cancellations.append(
                _LineCancellation(
                    line_id=input_line.line_id,
                    sku_id=line_revision["sku_id"],
                    ordered_quantity_base=commitment["ordered_quantity_base"],
                    open_quantity_base=open_quantity,
                    cancel_quantity_base=input_line.cancel_quantity_base,
                    reserved_released_quantity_base=reserved_release,
                    backorder_reduced_quantity_base=backorder_reduce,
                    line_total=line_revision["line_total"],
                    line_total_delta=line_total_delta,
                    warehouse_id=commitment["warehouse_id"],
                    commercial_approval_id=commitment["commercial_approval_id"],
                    sales_order_revision_id=commitment["sales_order_revision_id"],
                )
            )

        await _authority(
            session,
            actor=actor,
            branch_id=order["branch_id"],
            warehouse_id=fulfillment_warehouse_id,
            maker_subject=revision["actor_subject"],
            amount=total_cancel_value,
        )

        for cancellation in cancellations:
            await _advisory_lock(
                session, f"reservation:{cancellation.warehouse_id}:{cancellation.sku_id}"
            )

        cancellation_id = uuid4()
        await session.execute(
            insert(sales_order_cancellations).values(
                cancellation_id=cancellation_id,
                sales_order_id=sales_order_id,
                reason=command.reason,
                cancelled_by=actor.subject,
                correlation_id=request.state.correlation_id,
                idempotency_key=idempotency_key,
            )
        )

        credit_release = ZERO
        if revision["payment_timing_policy"] == "on_account" and total_cancel_value > ZERO:
            await _advisory_lock(session, f"credit:{revision['customer_id']}")
            await session.execute(
                insert(credit_exposure_entries).values(
                    entry_id=uuid4(),
                    customer_id=revision["customer_id"],
                    commercial_approval_id=cancellations[0].commercial_approval_id,
                    sales_order_id=sales_order_id,
                    component="approved_uninvoiced",
                    amount_delta=-total_cancel_value,
                    source_type="sales_order_cancellation",
                    source_id=cancellation_id,
                    actor_subject=actor.subject,
                    correlation_id=request.state.correlation_id,
                    idempotency_key=f"{idempotency_key}:credit",
                )
            )
            await session.execute(
                update(customer_credit_exposure)
                .where(customer_credit_exposure.c.customer_id == revision["customer_id"])
                .values(
                    approved_uninvoiced=customer_credit_exposure.c.approved_uninvoiced
                    - total_cancel_value,
                    version=customer_credit_exposure.c.version + 1,
                    updated_at=func.now(),
                )
            )
            credit_release = total_cancel_value

        latest_fulfillment = await _latest_active_fulfillment_order(session, sales_order_id)
        fulfillment_reserved_delta = ZERO
        fulfillment_backorder_delta = ZERO
        fulfillment_payment_delta = ZERO

        for cancellation in cancellations:
            commitment = commitments[cancellation.line_id]
            await session.execute(
                update(sales_order_line_commitments)
                .where(
                    sales_order_line_commitments.c.sales_order_id == sales_order_id,
                    sales_order_line_commitments.c.line_id == cancellation.line_id,
                )
                .values(
                    reserved_quantity_base=commitment["reserved_quantity_base"]
                    - cancellation.reserved_released_quantity_base,
                    backorder_quantity_base=commitment["backorder_quantity_base"]
                    - cancellation.backorder_reduced_quantity_base,
                    cancelled_quantity_base=commitment["cancelled_quantity_base"]
                    + cancellation.cancel_quantity_base,
                )
            )
            await session.execute(
                insert(sales_order_cancellation_lines).values(
                    cancellation_line_id=uuid4(),
                    cancellation_id=cancellation_id,
                    line_id=cancellation.line_id,
                    sku_id=cancellation.sku_id,
                    cancelled_quantity_base=cancellation.cancel_quantity_base,
                    reserved_released_quantity_base=cancellation.reserved_released_quantity_base,
                    backorder_reduced_quantity_base=cancellation.backorder_reduced_quantity_base,
                    line_total_delta=cancellation.line_total_delta,
                )
            )
            if cancellation.reserved_released_quantity_base > ZERO:
                await session.execute(
                    insert(inventory_reservation_events).values(
                        reservation_event_id=uuid4(),
                        commercial_approval_id=cancellation.commercial_approval_id,
                        sales_order_id=sales_order_id,
                        sales_order_revision_id=cancellation.sales_order_revision_id,
                        line_id=cancellation.line_id,
                        sku_id=cancellation.sku_id,
                        warehouse_id=cancellation.warehouse_id,
                        event_type="released",
                        quantity_base=cancellation.reserved_released_quantity_base,
                        reason=f"Order cancellation: {command.reason}",
                        actor_subject=actor.subject,
                        correlation_id=request.state.correlation_id,
                        idempotency_key=f"{idempotency_key}:reservation-release:{cancellation.line_id}",
                    )
                )
                await session.execute(
                    update(inventory_reserved_by_sku_warehouse)
                    .where(
                        inventory_reserved_by_sku_warehouse.c.sku_id == cancellation.sku_id,
                        inventory_reserved_by_sku_warehouse.c.warehouse_id
                        == cancellation.warehouse_id,
                    )
                    .values(
                        reserved_quantity_base=(
                            inventory_reserved_by_sku_warehouse.c.reserved_quantity_base
                            - cancellation.reserved_released_quantity_base
                        ),
                        version=inventory_reserved_by_sku_warehouse.c.version + 1,
                        updated_at=func.now(),
                    )
                )
                fulfillment_reserved_delta += cancellation.reserved_released_quantity_base
                if latest_fulfillment is not None:
                    fulfillment_line = (
                        (
                            await session.execute(
                                select(fulfillment_order_lines).where(
                                    fulfillment_order_lines.c.fulfillment_order_id
                                    == latest_fulfillment["fulfillment_order_id"],
                                    fulfillment_order_lines.c.line_id == cancellation.line_id,
                                )
                            )
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if (
                        fulfillment_line is not None
                        and cancellation.reserved_released_quantity_base > ZERO
                    ):
                        unit_reserved_value = (
                            cast(Decimal, fulfillment_line["reserved_value"])
                            / cast(Decimal, fulfillment_line["reserved_quantity_base"])
                            if cast(Decimal, fulfillment_line["reserved_quantity_base"]) > ZERO
                            else ZERO
                        )
                        fulfillment_payment_delta += _money(
                            unit_reserved_value * cancellation.reserved_released_quantity_base,
                            cast(str, latest_fulfillment["currency"]),
                        )
            fulfillment_backorder_delta += cancellation.backorder_reduced_quantity_base

        remaining_open = ZERO
        for cancellation in cancellations:
            remaining_open += (
                cancellation.open_quantity_base - cancellation.cancel_quantity_base
            )
        for commitment in commitments.values():
            if commitment["line_id"] not in {
                cancellation.line_id for cancellation in cancellations
            }:
                delivered = delivered_by_line.get(commitment["line_id"], ZERO)
                remaining_open += (
                    commitment["ordered_quantity_base"]
                    - commitment["picked_quantity_base"]
                    - delivered
                    - commitment["cancelled_quantity_base"]
                )

        new_status: Literal["approved", "partially_cancelled", "cancelled", "held"]
        if remaining_open == ZERO:
            new_status = "cancelled"
        elif order["status"] == "held":
            new_status = "held"
        else:
            new_status = "partially_cancelled"

        await session.execute(
            update(sales_orders)
            .where(sales_orders.c.sales_order_id == sales_order_id)
            .values(
                status=new_status,
                metadata_version=if_match + 1,
                updated_by=actor.subject,
                updated_at=func.now(),
            )
        )

        if latest_fulfillment is not None and (
            fulfillment_reserved_delta > ZERO
            or fulfillment_backorder_delta > ZERO
            or fulfillment_payment_delta > ZERO
        ):
            lf_reserved = cast(Decimal, latest_fulfillment["reserved_quantity_base"])
            lf_backorder = cast(Decimal, latest_fulfillment["backorder_quantity_base"])
            lf_payment_required = cast(Decimal, latest_fulfillment["payment_required"])
            lf_covered = cast(Decimal, latest_fulfillment["covered_amount"])
            new_reserved = max(
                lf_reserved - fulfillment_reserved_delta,
                ZERO,
            )
            new_backorder = max(
                lf_backorder - fulfillment_backorder_delta,
                ZERO,
            )
            new_payment_required = max(
                lf_payment_required - fulfillment_payment_delta,
                ZERO,
            )
            new_covered = min(lf_covered, new_payment_required)
            next_status = latest_fulfillment["status"]
            if new_reserved == ZERO and new_backorder == ZERO:
                next_status = "cancelled"
            elif (
                next_status == "payment_ready"
                and new_covered < new_payment_required
                and latest_fulfillment["payment_timing_policy"] == "prepaid"
            ):
                next_status = "reserved"
            await session.execute(
                update(fulfillment_order_state)
                .where(
                    fulfillment_order_state.c.fulfillment_order_id
                    == latest_fulfillment["fulfillment_order_id"]
                )
                .values(
                    status=next_status,
                    reserved_quantity_base=new_reserved,
                    backorder_quantity_base=new_backorder,
                    covered_amount=new_covered,
                    version=fulfillment_order_state.c.version + 1,
                    updated_at=func.now(),
                )
            )
            if new_status == "cancelled":
                await session.execute(
                    update(fulfillment_order_state)
                    .where(
                        fulfillment_order_state.c.fulfillment_order_id
                        == latest_fulfillment["fulfillment_order_id"]
                    )
                    .values(
                        payment_hold=False,
                    )
                )

        if new_status == "cancelled":
            hold = (
                (
                    await session.execute(
                        select(active_sales_order_holds).where(
                            active_sales_order_holds.c.sales_order_id == sales_order_id
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if hold is not None:
                released_event_id = uuid4()
                await session.execute(
                    insert(sales_order_hold_events).values(
                        hold_event_id=released_event_id,
                        sales_order_id=sales_order_id,
                        fulfillment_order_id=hold["fulfillment_order_id"],
                        hold_type="payment",
                        event_type="released",
                        reason=f"Order cancelled: {command.reason}",
                        actor_subject=actor.subject,
                        correlation_id=request.state.correlation_id,
                        idempotency_key=f"{idempotency_key}:hold-release",
                    )
                )
                await session.execute(
                    delete(active_sales_order_holds).where(
                        active_sales_order_holds.c.sales_order_id == sales_order_id
                    )
                )

        result = CancelSalesOrderResponse(
            cancellation_id=cancellation_id,
            sales_order_id=sales_order_id,
            status=new_status,
            reason=command.reason,
            cancelled_by=actor.subject,
            lines=[
                CancelledLineResponse(
                    line_id=cancellation.line_id,
                    cancelled_quantity_base=cancellation.cancel_quantity_base,
                    reserved_released_quantity_base=cancellation.reserved_released_quantity_base,
                    backorder_reduced_quantity_base=cancellation.backorder_reduced_quantity_base,
                    line_total_delta=cancellation.line_total_delta,
                )
                for cancellation in cancellations
            ],
            total_cancelled_quantity_base=sum(
                (cancellation.cancel_quantity_base for cancellation in cancellations), ZERO
            ),
            reserved_released_quantity_base=fulfillment_reserved_delta,
            backorder_reduced_quantity_base=fulfillment_backorder_delta,
            credit_released_base=credit_release,
        )
        await store_command_result(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result=result,
        )
        return result
