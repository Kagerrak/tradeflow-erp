from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import case, delete, func, insert, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tradeflow_api.auth import (
    AuthorizedUser,
    require_commercial_approver,
    require_sales_order_writer,
    require_sales_projection_rebuilder,
)
from tradeflow_api.command_receipts import get_command_replay, store_command_result
from tradeflow_api.database import get_database_session
from tradeflow_api.errors import AppError, error_responses
from tradeflow_api.models import (
    approval_authorities,
    commercial_approval_invalidations,
    commercial_approvals,
    commercial_exception_approvals,
    credit_exposure_entries,
    customer_accounts,
    customer_credit_exposure,
    inventory_availability,
    inventory_reservation_events,
    inventory_reserved_by_sku_warehouse,
    sales_order_line_commitments,
    sales_order_line_revisions,
    sales_order_revisions,
    sales_orders,
    skus,
    warehouse_stock_locations,
    warehouses,
)
from tradeflow_api.money import currency_quantum

router = APIRouter(prefix="/v1/sales", tags=["sales"])
ZERO = Decimal("0")


class CommandModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CommercialApprovalCommand(CommandModel):
    warehouse_id: UUID
    exception_reason: str | None = Field(default=None, min_length=1, max_length=500)
    credit_override_reason: str | None = Field(default=None, min_length=1, max_length=500)

    @field_validator("exception_reason", "credit_override_reason")
    @classmethod
    def normalize_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if normalized == "":
            raise ValueError("Approval reasons must contain non-whitespace text.")
        return normalized


class ReservationLineResponse(BaseModel):
    line_id: UUID
    sku_id: UUID
    ordered_quantity_base: Decimal
    reserved_quantity_base: Decimal
    backorder_quantity_base: Decimal


class CreditCheckResponse(BaseModel):
    open_balance: Decimal
    approved_uninvoiced_before: Decimal
    order_value: Decimal
    projected_exposure: Decimal
    credit_limit: Decimal | None
    override_required: bool
    approved_excess: Decimal


class CommercialApprovalResponse(BaseModel):
    commercial_approval_id: UUID
    sales_order_id: UUID
    sales_order_revision_id: UUID
    status: Literal["approved"]
    warehouse_id: UUID
    payment_timing_policy: Literal["prepaid", "cash_on_delivery", "on_account"]
    approved_by: str
    maker_subject: str
    required_exceptions: list[str]
    credit: CreditCheckResponse
    reservations: list[ReservationLineResponse]
    reserved_quantity_base: Decimal
    backorder_quantity_base: Decimal


class CommercialReviewExceptionResponse(BaseModel):
    exception_type: Literal["discount", "below_floor", "credit_override"]
    amount: Decimal
    percentage: Decimal | None


class CommercialReviewLineResponse(BaseModel):
    line_id: UUID
    sku_id: UUID
    sku_code: str
    sku_name: str
    entered_quantity: Decimal
    entered_unit: str
    quantity_base: Decimal
    conversion_snapshot: dict[str, str]
    list_unit_price: Decimal
    floor_unit_price: Decimal | None
    manual_override_unit_price: Decimal | None
    effective_unit_price: Decimal
    allocated_discount: Decimal
    below_floor: bool
    tax_snapshot: dict[str, str]
    calculation_snapshot: dict[str, str]
    warehouse_on_hand_base: Decimal
    warehouse_reserved_base: Decimal
    reservable_quantity_base: Decimal
    backorder_quantity_base: Decimal


class CommercialReviewResponse(BaseModel):
    sales_order_id: UUID
    sales_order_revision_id: UUID
    version: int
    status: Literal["draft", "approved", "held"]
    maker_subject: str
    warehouse_id: UUID
    customer_id: UUID
    customer_account_number: str
    customer_name: str
    customer_status: Literal["active", "inactive", "prospect"]
    customer_snapshot_current: bool
    payment_terms: str
    credit_hold: bool
    currency: str
    payment_timing_policy: Literal["prepaid", "cash_on_delivery", "on_account"]
    subtotal: Decimal
    discount_total: Decimal
    tax_total: Decimal
    grand_total: Decimal
    open_balance: Decimal
    approved_uninvoiced: Decimal
    projected_exposure: Decimal
    credit_limit: Decimal | None
    required_exceptions: list[CommercialReviewExceptionResponse]
    lines: list[CommercialReviewLineResponse]


class NonMaterialOrderChangeCommand(CommandModel):
    notes: str | None = Field(default=None, max_length=2000)
    delivery_instructions: str | None = Field(default=None, max_length=2000)


class NonMaterialOrderChangeResponse(BaseModel):
    sales_order_id: UUID
    commercial_version: int
    version: int
    status: Literal["draft", "approved", "held"]
    notes: str | None
    delivery_instructions: str | None
    commercial_approval_id: UUID | None


class ProjectionRebuildResponse(BaseModel):
    credit_customers: int
    line_commitments: int
    reservation_items: int


def _request_hash(operation: str, command: BaseModel, context: str) -> str:
    payload = f"{operation}:{context}:{command.model_dump_json(exclude_none=False)}"
    return sha256(payload.encode()).hexdigest()


def _money(value: Decimal, currency: str) -> Decimal:
    return value.quantize(currency_quantum(currency), ROUND_HALF_UP)


async def _advisory_lock(session: AsyncSession, key: str) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": key},
    )


async def _projection_read_lock(session: AsyncSession) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock_shared(hashtextextended('commercial-projections', 0))")
    )


async def _authority(
    session: AsyncSession,
    *,
    actor: AuthorizedUser,
    branch_id: UUID,
    capability: str,
    maker_subject: str,
    amount: Decimal,
    percentage: Decimal | None,
) -> dict[str, object]:
    if actor.subject == maker_subject:
        raise AppError(
            409,
            "maker_checker_violation",
            "The order maker cannot approve the same commercial exception.",
        )
    if capability not in actor.capabilities:
        raise AppError(
            409,
            "commercial_exception_required",
            f"The order requires a different approver with '{capability}' authority.",
        )
    row = (
        (
            await session.execute(
                select(approval_authorities).where(
                    approval_authorities.c.user_subject == actor.subject,
                    approval_authorities.c.capability_code == capability,
                    approval_authorities.c.branch_id == branch_id,
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
            f"Explicit '{capability}' Approval Authority is required.",
        )
    if row["maximum_amount"] is not None and amount > row["maximum_amount"]:
        raise AppError(
            403,
            "approval_limit_exceeded",
            "The commercial exception exceeds the approver's amount authority.",
        )
    if (
        percentage is not None
        and row["maximum_percentage"] is not None
        and percentage > row["maximum_percentage"]
    ):
        raise AppError(
            403,
            "approval_limit_exceeded",
            "The commercial exception exceeds the approver's percentage authority.",
        )
    return {
        "capability": capability,
        "maximum_amount": (
            str(row["maximum_amount"]) if row["maximum_amount"] is not None else None
        ),
        "maximum_percentage": (
            str(row["maximum_percentage"]) if row["maximum_percentage"] is not None else None
        ),
        "maker_checker_required": row["maker_checker_required"],
    }


async def _active_approval_id(
    session: AsyncSession,
    sales_order_id: UUID,
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


async def invalidate_active_approval(
    session: AsyncSession,
    *,
    sales_order_id: UUID,
    actor_subject: str,
    reason: str,
    correlation_id: str,
    idempotency_key: str,
) -> UUID | None:
    await _projection_read_lock(session)
    approval_id = await _active_approval_id(session, sales_order_id)
    if approval_id is None:
        return None
    invalidation_id = uuid4()
    await session.execute(
        insert(commercial_approval_invalidations).values(
            invalidation_id=invalidation_id,
            commercial_approval_id=approval_id,
            reason=reason,
            invalidated_by=actor_subject,
            correlation_id=correlation_id,
            idempotency_key=f"{idempotency_key}:invalidate",
        )
    )
    credit_entry = (
        (
            await session.execute(
                select(credit_exposure_entries).where(
                    credit_exposure_entries.c.commercial_approval_id == approval_id,
                    credit_exposure_entries.c.component == "approved_uninvoiced",
                    credit_exposure_entries.c.amount_delta > 0,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if credit_entry is not None:
        amount = credit_entry["amount_delta"]
        await session.execute(
            insert(credit_exposure_entries).values(
                entry_id=uuid4(),
                customer_id=credit_entry["customer_id"],
                commercial_approval_id=approval_id,
                sales_order_id=sales_order_id,
                component="approved_uninvoiced",
                amount_delta=-amount,
                source_type="commercial_approval_invalidation",
                source_id=invalidation_id,
                actor_subject=actor_subject,
                correlation_id=correlation_id,
                idempotency_key=f"{idempotency_key}:credit-release",
            )
        )
        await session.execute(
            update(customer_credit_exposure)
            .where(customer_credit_exposure.c.customer_id == credit_entry["customer_id"])
            .values(
                approved_uninvoiced=customer_credit_exposure.c.approved_uninvoiced - amount,
                version=customer_credit_exposure.c.version + 1,
                updated_at=func.now(),
            )
        )
    commitments = (
        (
            await session.execute(
                select(sales_order_line_commitments).where(
                    sales_order_line_commitments.c.commercial_approval_id == approval_id
                )
            )
        )
        .mappings()
        .all()
    )
    reservation_keys = sorted(
        {
            (commitment["warehouse_id"], commitment["sku_id"])
            for commitment in commitments
            if commitment["reserved_quantity_base"] > ZERO
        },
        key=lambda value: (str(value[0]), str(value[1])),
    )
    for warehouse_id, sku_id in reservation_keys:
        await _advisory_lock(session, f"reservation:{warehouse_id}:{sku_id}")
    for commitment in commitments:
        reserved = commitment["reserved_quantity_base"]
        if reserved > ZERO:
            await session.execute(
                insert(inventory_reservation_events).values(
                    reservation_event_id=uuid4(),
                    commercial_approval_id=approval_id,
                    sales_order_id=sales_order_id,
                    sales_order_revision_id=commitment["sales_order_revision_id"],
                    line_id=commitment["line_id"],
                    sku_id=commitment["sku_id"],
                    warehouse_id=commitment["warehouse_id"],
                    event_type="released",
                    quantity_base=reserved,
                    reason=reason,
                    actor_subject=actor_subject,
                    correlation_id=correlation_id,
                    idempotency_key=f"{idempotency_key}:reservation-release",
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
        delete(sales_order_line_commitments).where(
            sales_order_line_commitments.c.commercial_approval_id == approval_id
        )
    )
    await session.execute(
        update(sales_orders)
        .where(sales_orders.c.sales_order_id == sales_order_id)
        .values(
            status="draft",
            approved_revision_id=None,
            fulfillment_warehouse_id=None,
            updated_by=actor_subject,
            updated_at=func.now(),
        )
    )
    return approval_id


@router.get(
    "/orders/{sales_order_id}/commercial-review",
    response_model=CommercialReviewResponse,
    responses=error_responses(401, 403, 404, 409, 500),
)
async def review_commercial_approval(
    sales_order_id: UUID,
    warehouse_id: Annotated[UUID, Query()],
    actor: Annotated[AuthorizedUser, Depends(require_commercial_approver)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> CommercialReviewResponse:
    order = (
        (
            await session.execute(
                select(sales_orders).where(sales_orders.c.sales_order_id == sales_order_id)
            )
        )
        .mappings()
        .one_or_none()
    )
    if order is None:
        raise AppError(404, "sales_order_not_found", "The Sales Order does not exist.")
    if order["branch_id"] not in actor.branch_ids:
        raise AppError(403, "operational_scope_required", "Branch scope is required.")
    warehouse = (
        (
            await session.execute(
                select(warehouses).where(
                    warehouses.c.warehouse_id == warehouse_id,
                    warehouses.c.branch_id == order["branch_id"],
                    warehouses.c.is_active.is_(True),
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if warehouse is None or warehouse_id not in actor.warehouse_ids:
        raise AppError(
            403,
            "warehouse_scope_required",
            "An active Warehouse in the order Branch is required.",
        )
    revision = (
        (
            await session.execute(
                select(sales_order_revisions).where(
                    sales_order_revisions.c.sales_order_id == sales_order_id,
                    sales_order_revisions.c.version == order["version"],
                )
            )
        )
        .mappings()
        .one()
    )
    customer = (
        (
            await session.execute(
                select(customer_accounts).where(
                    customer_accounts.c.customer_id == revision["customer_id"]
                )
            )
        )
        .mappings()
        .one()
    )
    line_rows = (
        (
            await session.execute(
                select(sales_order_line_revisions)
                .where(
                    sales_order_line_revisions.c.sales_order_revision_id
                    == revision["sales_order_revision_id"]
                )
                .order_by(
                    sales_order_line_revisions.c.line_position,
                    sales_order_line_revisions.c.line_id,
                )
            )
        )
        .mappings()
        .all()
    )
    maker_threshold = (
        await session.scalar(
            select(approval_authorities.c.maximum_percentage).where(
                approval_authorities.c.user_subject == revision["actor_subject"],
                approval_authorities.c.capability_code == "sales:discount-enter",
                approval_authorities.c.branch_id == order["branch_id"],
            )
        )
    ) or ZERO
    discount_percentage = (
        revision["discount_total"] / revision["subtotal"] * Decimal("100")
        if revision["subtotal"] > ZERO
        else ZERO
    )
    exceptions: list[CommercialReviewExceptionResponse] = []
    if discount_percentage > maker_threshold:
        permitted = _money(
            revision["subtotal"] * maker_threshold / Decimal("100"),
            revision["currency"],
        )
        exceptions.append(
            CommercialReviewExceptionResponse(
                exception_type="discount",
                amount=max(
                    _money(
                        revision["discount_total"] - permitted,
                        revision["currency"],
                    ),
                    ZERO,
                ),
                percentage=discount_percentage,
            )
        )
    below_floor_amount = _money(
        sum(
            (
                _money(
                    max(
                        (line["floor_unit_price"] or ZERO) * line["entered_quantity"]
                        - (
                            line["effective_unit_price"] * line["entered_quantity"]
                            - line["allocated_discount"]
                        ),
                        ZERO,
                    ),
                    revision["currency"],
                )
                for line in line_rows
            ),
            ZERO,
        ),
        revision["currency"],
    )
    if below_floor_amount > ZERO:
        exceptions.append(
            CommercialReviewExceptionResponse(
                exception_type="below_floor",
                amount=below_floor_amount,
                percentage=None,
            )
        )
    exposure = (
        (
            await session.execute(
                select(customer_credit_exposure).where(
                    customer_credit_exposure.c.customer_id == customer["customer_id"]
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    open_balance = exposure["open_balance"] if exposure is not None else ZERO
    approved_uninvoiced = exposure["approved_uninvoiced"] if exposure is not None else ZERO
    order_credit = (
        revision["grand_total"] if revision["payment_timing_policy"] == "on_account" else ZERO
    )
    projected = open_balance + approved_uninvoiced + order_credit
    credit_limit = customer["credit_limit"]
    if order_credit > ZERO and (credit_limit is None or projected > credit_limit):
        exceptions.append(
            CommercialReviewExceptionResponse(
                exception_type="credit_override",
                amount=(
                    projected
                    if credit_limit is None
                    else max(_money(projected - credit_limit, revision["currency"]), ZERO)
                ),
                percentage=None,
            )
        )
    remaining_by_sku: dict[UUID, Decimal] = {}
    on_hand_by_sku: dict[UUID, Decimal] = {}
    reserved_by_sku: dict[UUID, Decimal] = {}
    for sku_id in sorted({line["sku_id"] for line in line_rows}, key=str):
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
                    inventory_availability.c.warehouse_id == warehouse_id,
                    warehouse_stock_locations.c.custody == "available",
                    warehouse_stock_locations.c.is_active.is_(True),
                    or_(
                        inventory_availability.c.expiration_date.is_(None),
                        inventory_availability.c.expiration_date >= date.today(),
                    ),
                )
            )
        ) or ZERO
        reserved = (
            await session.scalar(
                select(inventory_reserved_by_sku_warehouse.c.reserved_quantity_base).where(
                    inventory_reserved_by_sku_warehouse.c.sku_id == sku_id,
                    inventory_reserved_by_sku_warehouse.c.warehouse_id == warehouse_id,
                )
            )
        ) or ZERO
        on_hand_by_sku[sku_id] = on_hand
        reserved_by_sku[sku_id] = reserved
        remaining_by_sku[sku_id] = max(on_hand - reserved, ZERO)
    lines: list[CommercialReviewLineResponse] = []
    for line in line_rows:
        reservable = min(line["quantity_base"], remaining_by_sku[line["sku_id"]])
        remaining_by_sku[line["sku_id"]] -= reservable
        lines.append(
            CommercialReviewLineResponse(
                line_id=line["line_id"],
                sku_id=line["sku_id"],
                sku_code=line["sku_code"],
                sku_name=line["sku_name"],
                entered_quantity=line["entered_quantity"],
                entered_unit=line["entered_unit"],
                quantity_base=line["quantity_base"],
                conversion_snapshot=line["conversion_snapshot"],
                list_unit_price=line["list_unit_price"],
                floor_unit_price=line["floor_unit_price"],
                manual_override_unit_price=line["manual_override_unit_price"],
                effective_unit_price=line["effective_unit_price"],
                allocated_discount=line["allocated_discount"],
                below_floor=line["below_floor"],
                tax_snapshot=line["tax_snapshot"],
                calculation_snapshot=line["calculation_snapshot"],
                warehouse_on_hand_base=on_hand_by_sku[line["sku_id"]],
                warehouse_reserved_base=reserved_by_sku[line["sku_id"]],
                reservable_quantity_base=reservable,
                backorder_quantity_base=line["quantity_base"] - reservable,
            )
        )
    return CommercialReviewResponse(
        sales_order_id=sales_order_id,
        sales_order_revision_id=revision["sales_order_revision_id"],
        version=revision["version"],
        status=order["status"],
        maker_subject=revision["actor_subject"],
        warehouse_id=warehouse_id,
        customer_id=customer["customer_id"],
        customer_account_number=customer["account_number"],
        customer_name=customer["legal_name"],
        customer_status=customer["status"],
        customer_snapshot_current=customer["version"] == revision["customer_version"],
        payment_terms=customer["payment_terms"],
        credit_hold=customer["credit_hold"],
        currency=revision["currency"],
        payment_timing_policy=revision["payment_timing_policy"],
        subtotal=revision["subtotal"],
        discount_total=revision["discount_total"],
        tax_total=revision["tax_total"],
        grand_total=revision["grand_total"],
        open_balance=open_balance,
        approved_uninvoiced=approved_uninvoiced,
        projected_exposure=projected,
        credit_limit=credit_limit,
        required_exceptions=exceptions,
        lines=lines,
    )


@router.post(
    "/orders/{sales_order_id}/commercial-approval",
    response_model=CommercialApprovalResponse,
    status_code=201,
    responses=error_responses(401, 403, 404, 409, 422, 500),
)
async def approve_sales_order(
    sales_order_id: UUID,
    command: CommercialApprovalCommand,
    request: Request,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_commercial_approver)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    if_match: Annotated[int, Header(alias="If-Match", ge=1)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
) -> CommercialApprovalResponse:
    request_hash = _request_hash(
        "commercial_approval",
        command,
        f"{sales_order_id}:{if_match}",
    )
    # Scope authorization uses this same session and starts a read-only
    # transaction. Close it before the atomic command transaction.
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
        if command.warehouse_id not in actor.warehouse_ids:
            raise AppError(403, "operational_scope_required", "Warehouse scope is required.")
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            replay_exceptions = (
                (
                    await session.execute(
                        select(commercial_exception_approvals).where(
                            commercial_exception_approvals.c.commercial_approval_id
                            == UUID(str(replay["commercial_approval_id"]))
                        )
                    )
                )
                .mappings()
                .all()
            )
            capability_by_type = {
                "discount": "sales:discount-approve",
                "below_floor": "sales:below-floor-approve",
                "credit_override": "sales:credit-override",
            }
            for replay_evidence in replay_exceptions:
                await _authority(
                    session,
                    actor=actor,
                    branch_id=scoped_order["branch_id"],
                    capability=capability_by_type[replay_evidence["exception_type"]],
                    maker_subject=replay_evidence["maker_subject"],
                    amount=replay_evidence["exception_amount"],
                    percentage=replay_evidence["exception_percentage"],
                )
            response.status_code = 200
            response.headers["X-Idempotency-Replayed"] = "true"
            return CommercialApprovalResponse.model_validate(replay)
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
        if command.warehouse_id not in actor.warehouse_ids:
            raise AppError(403, "operational_scope_required", "Warehouse scope is required.")
        if order["version"] != if_match:
            raise AppError(
                409,
                "optimistic_version_conflict",
                "The Sales Order changed and requires explicit review.",
            )
        if order["status"] != "draft":
            raise AppError(
                409,
                "sales_order_not_draft",
                "Only the current Sales Order Draft can be commercially approved.",
            )
        await _projection_read_lock(session)
        warehouse = (
            (
                await session.execute(
                    select(warehouses).where(warehouses.c.warehouse_id == command.warehouse_id)
                )
            )
            .mappings()
            .one_or_none()
        )
        if (
            warehouse is None
            or not warehouse["is_active"]
            or warehouse["branch_id"] != order["branch_id"]
        ):
            raise AppError(
                409,
                "warehouse_unavailable",
                "An active warehouse in the Sales Order Branch is required.",
            )
        revision = (
            (
                await session.execute(
                    select(sales_order_revisions).where(
                        sales_order_revisions.c.sales_order_id == sales_order_id,
                        sales_order_revisions.c.version == if_match,
                    )
                )
            )
            .mappings()
            .one()
        )
        lines = (
            (
                await session.execute(
                    select(sales_order_line_revisions)
                    .where(
                        sales_order_line_revisions.c.sales_order_revision_id
                        == revision["sales_order_revision_id"]
                    )
                    .order_by(
                        sales_order_line_revisions.c.line_position,
                        sales_order_line_revisions.c.line_id,
                    )
                )
            )
            .mappings()
            .all()
        )
        active_sku_count = await session.scalar(
            select(func.count())
            .select_from(skus)
            .where(
                skus.c.sku_id.in_({line["sku_id"] for line in lines}),
                skus.c.is_active.is_(True),
            )
        )
        if active_sku_count != len({line["sku_id"] for line in lines}):
            raise AppError(
                409,
                "sales_order_sku_inactive",
                "Every Sales Order SKU must remain active at Commercial Approval.",
            )
        if (
            sum((line["allocated_discount"] for line in lines), ZERO) != revision["discount_total"]
            or sum((line["taxable_amount"] for line in lines), ZERO) != revision["taxable_total"]
            or sum((line["tax_amount"] for line in lines), ZERO) != revision["tax_total"]
            or sum((line["line_total"] for line in lines), ZERO) != revision["grand_total"]
            or any(
                Decimal(line["tax_snapshot"]["tax_amount"]) != line["tax_amount"]
                or Decimal(line["tax_snapshot"]["taxable_basis"]) != line["taxable_amount"]
                or Decimal(line["calculation_snapshot"]["allocated_discount"])
                != line["allocated_discount"]
                or Decimal(line["calculation_snapshot"]["line_total"]) != line["line_total"]
                or line["tax_snapshot"]["inclusion_mode"] != revision["price_inclusion_mode"]
                for line in lines
            )
        ):
            raise AppError(
                409,
                "commercial_snapshot_invalid",
                "Stored pricing, tax, or calculation snapshots are inconsistent.",
            )
        maker = revision["actor_subject"]
        customer = (
            (
                await session.execute(
                    select(customer_accounts)
                    .where(customer_accounts.c.customer_id == order["customer_id"])
                    .with_for_update()
                )
            )
            .mappings()
            .one()
        )
        if customer["status"] != "active":
            raise AppError(409, "customer_inactive", "The Customer Account is not active.")
        if customer["credit_hold"]:
            raise AppError(409, "customer_credit_hold", "The Customer Account is on Credit Hold.")
        if revision["customer_version"] != customer["version"]:
            raise AppError(
                409,
                "reference_data_conflict",
                "The Customer Account changed after this Sales Order revision was priced.",
            )
        required: list[str] = []
        exception_rows: list[dict[str, object]] = []
        discount_percentage = (
            (revision["discount_total"] / revision["subtotal"] * Decimal("100"))
            if revision["subtotal"] > ZERO
            else ZERO
        )
        maker_discount_threshold = (
            await session.scalar(
                select(approval_authorities.c.maximum_percentage).where(
                    approval_authorities.c.user_subject == maker,
                    approval_authorities.c.capability_code == "sales:discount-enter",
                    approval_authorities.c.branch_id == order["branch_id"],
                )
            )
        ) or ZERO
        if discount_percentage > maker_discount_threshold:
            required.append("discount")
            permitted_discount = _money(
                revision["subtotal"] * maker_discount_threshold / Decimal("100"),
                revision["currency"],
            )
            excess_discount = max(
                _money(
                    revision["discount_total"] - permitted_discount,
                    revision["currency"],
                ),
                ZERO,
            )
            authority = await _authority(
                session,
                actor=actor,
                branch_id=order["branch_id"],
                capability="sales:discount-approve",
                maker_subject=maker,
                amount=excess_discount,
                percentage=discount_percentage,
            )
            if command.exception_reason is None:
                raise AppError(
                    422,
                    "exception_reason_required",
                    "A reason is required for discount or below-floor approval.",
                )
            exception_rows.append(
                {
                    "exception_type": "discount",
                    "reason": command.exception_reason,
                    "exception_amount": excess_discount,
                    "exception_percentage": discount_percentage,
                    "authority_snapshot": {
                        **authority,
                        "maker_discount_threshold": str(maker_discount_threshold),
                    },
                }
            )
        below_floor_amount = _money(
            sum(
                [
                    _money(
                        max(
                            (line["floor_unit_price"] or ZERO) * line["entered_quantity"]
                            - (
                                line["effective_unit_price"] * line["entered_quantity"]
                                - line["allocated_discount"]
                            ),
                            ZERO,
                        ),
                        revision["currency"],
                    )
                    for line in lines
                ],
                ZERO,
            ),
            revision["currency"],
        )
        if below_floor_amount > ZERO:
            required.append("below_floor")
            authority = await _authority(
                session,
                actor=actor,
                branch_id=order["branch_id"],
                capability="sales:below-floor-approve",
                maker_subject=maker,
                amount=below_floor_amount,
                percentage=None,
            )
            if command.exception_reason is None:
                raise AppError(
                    422,
                    "exception_reason_required",
                    "A reason is required for discount or below-floor approval.",
                )
            exception_rows.append(
                {
                    "exception_type": "below_floor",
                    "reason": command.exception_reason,
                    "exception_amount": below_floor_amount,
                    "exception_percentage": None,
                    "authority_snapshot": authority,
                }
            )
        await _advisory_lock(session, f"credit:{customer['customer_id']}")
        exposure = (
            (
                await session.execute(
                    select(customer_credit_exposure)
                    .where(customer_credit_exposure.c.customer_id == customer["customer_id"])
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        open_balance = exposure["open_balance"] if exposure is not None else ZERO
        approved_before = exposure["approved_uninvoiced"] if exposure is not None else ZERO
        credit_amount = (
            revision["grand_total"] if revision["payment_timing_policy"] == "on_account" else ZERO
        )
        projected = open_balance + approved_before + credit_amount
        credit_limit = customer["credit_limit"]
        credit_excess = (
            max(projected - credit_limit, ZERO)
            if credit_limit is not None
            else (projected if credit_amount > ZERO else ZERO)
        )
        override_required = credit_amount > ZERO and (
            credit_limit is None or projected > credit_limit
        )
        if (
            revision["payment_timing_policy"] == "on_account"
            and not customer["payment_terms"].strip()
        ):
            raise AppError(
                409,
                "payment_terms_required",
                "On Account approval requires Customer payment terms.",
            )
        if override_required:
            required.append("credit_override")
            authority = await _authority(
                session,
                actor=actor,
                branch_id=order["branch_id"],
                capability="sales:credit-override",
                maker_subject=maker,
                amount=credit_excess,
                percentage=None,
            )
            if command.credit_override_reason is None:
                raise AppError(
                    422,
                    "credit_override_reason_required",
                    "A reason is required for an order-specific Credit Override.",
                )
            exception_rows.append(
                {
                    "exception_type": "credit_override",
                    "reason": command.credit_override_reason,
                    "exception_amount": credit_excess,
                    "exception_percentage": None,
                    "authority_snapshot": authority,
                }
            )
        approval_id = uuid4()
        await session.execute(
            insert(commercial_approvals).values(
                commercial_approval_id=approval_id,
                sales_order_id=sales_order_id,
                sales_order_revision_id=revision["sales_order_revision_id"],
                customer_id=customer["customer_id"],
                warehouse_id=command.warehouse_id,
                maker_subject=maker,
                approved_by=actor.subject,
                payment_timing_policy=revision["payment_timing_policy"],
                order_total=revision["grand_total"],
                open_balance_snapshot=open_balance,
                approved_uninvoiced_snapshot=approved_before,
                credit_limit_snapshot=credit_limit,
                credit_excess_approved=credit_excess if override_required else ZERO,
                correlation_id=request.state.correlation_id,
                idempotency_key=idempotency_key,
            )
        )
        for evidence in exception_rows:
            await session.execute(
                insert(commercial_exception_approvals).values(
                    exception_approval_id=uuid4(),
                    commercial_approval_id=approval_id,
                    maker_subject=maker,
                    approved_by=actor.subject,
                    **evidence,
                )
            )
        if credit_amount > ZERO:
            if exposure is None:
                await session.execute(
                    insert(customer_credit_exposure).values(
                        customer_id=customer["customer_id"],
                        open_balance=ZERO,
                        approved_uninvoiced=credit_amount,
                    )
                )
            else:
                await session.execute(
                    update(customer_credit_exposure)
                    .where(customer_credit_exposure.c.customer_id == customer["customer_id"])
                    .values(
                        approved_uninvoiced=(
                            customer_credit_exposure.c.approved_uninvoiced + credit_amount
                        ),
                        version=customer_credit_exposure.c.version + 1,
                        updated_at=func.now(),
                    )
                )
            await session.execute(
                insert(credit_exposure_entries).values(
                    entry_id=uuid4(),
                    customer_id=customer["customer_id"],
                    commercial_approval_id=approval_id,
                    sales_order_id=sales_order_id,
                    component="approved_uninvoiced",
                    amount_delta=credit_amount,
                    source_type="commercial_approval",
                    source_id=approval_id,
                    actor_subject=actor.subject,
                    correlation_id=request.state.correlation_id,
                    idempotency_key=f"{idempotency_key}:credit",
                )
            )
        sku_ids = sorted({line["sku_id"] for line in lines}, key=str)
        remaining_by_sku: dict[UUID, Decimal] = {}
        for sku_id in sku_ids:
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
                            inventory_availability.c.expiration_date >= date.today(),
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
        reservations: list[ReservationLineResponse] = []
        reserved_delta_by_sku: dict[UUID, Decimal] = {}
        for line in lines:
            ordered = line["quantity_base"]
            reserved = min(ordered, remaining_by_sku[line["sku_id"]])
            backordered = ordered - reserved
            remaining_by_sku[line["sku_id"]] -= reserved
            reserved_delta_by_sku[line["sku_id"]] = (
                reserved_delta_by_sku.get(line["sku_id"], ZERO) + reserved
            )
            await session.execute(
                insert(sales_order_line_commitments).values(
                    sales_order_id=sales_order_id,
                    line_id=line["line_id"],
                    commercial_approval_id=approval_id,
                    sales_order_revision_id=revision["sales_order_revision_id"],
                    sku_id=line["sku_id"],
                    warehouse_id=command.warehouse_id,
                    ordered_quantity_base=ordered,
                    reserved_quantity_base=reserved,
                    backorder_quantity_base=backordered,
                )
            )
            if reserved > ZERO:
                await session.execute(
                    insert(inventory_reservation_events).values(
                        reservation_event_id=uuid4(),
                        commercial_approval_id=approval_id,
                        sales_order_id=sales_order_id,
                        sales_order_revision_id=revision["sales_order_revision_id"],
                        line_id=line["line_id"],
                        sku_id=line["sku_id"],
                        warehouse_id=command.warehouse_id,
                        event_type="reserved",
                        quantity_base=reserved,
                        reason="Commercial approval",
                        actor_subject=actor.subject,
                        correlation_id=request.state.correlation_id,
                        idempotency_key=f"{idempotency_key}:reservation",
                    )
                )
            reservations.append(
                ReservationLineResponse(
                    line_id=line["line_id"],
                    sku_id=line["sku_id"],
                    ordered_quantity_base=ordered,
                    reserved_quantity_base=reserved,
                    backorder_quantity_base=backordered,
                )
            )
        for sku_id, reserved_delta in reserved_delta_by_sku.items():
            if reserved_delta == ZERO:
                continue
            await session.execute(
                pg_insert(inventory_reserved_by_sku_warehouse)
                .values(
                    sku_id=sku_id,
                    warehouse_id=command.warehouse_id,
                    reserved_quantity_base=reserved_delta,
                )
                .on_conflict_do_update(
                    index_elements=[
                        inventory_reserved_by_sku_warehouse.c.sku_id,
                        inventory_reserved_by_sku_warehouse.c.warehouse_id,
                    ],
                    set_={
                        "reserved_quantity_base": (
                            inventory_reserved_by_sku_warehouse.c.reserved_quantity_base
                            + reserved_delta
                        ),
                        "version": inventory_reserved_by_sku_warehouse.c.version + 1,
                        "updated_at": func.now(),
                    },
                )
            )
        await session.execute(
            update(sales_orders)
            .where(sales_orders.c.sales_order_id == sales_order_id)
            .values(
                status="approved",
                approved_revision_id=revision["sales_order_revision_id"],
                fulfillment_warehouse_id=command.warehouse_id,
                updated_by=actor.subject,
                updated_at=func.now(),
            )
        )
        result = CommercialApprovalResponse(
            commercial_approval_id=approval_id,
            sales_order_id=sales_order_id,
            sales_order_revision_id=revision["sales_order_revision_id"],
            status="approved",
            warehouse_id=command.warehouse_id,
            payment_timing_policy=revision["payment_timing_policy"],
            approved_by=actor.subject,
            maker_subject=maker,
            required_exceptions=required,
            credit=CreditCheckResponse(
                open_balance=open_balance,
                approved_uninvoiced_before=approved_before,
                order_value=credit_amount,
                projected_exposure=projected,
                credit_limit=credit_limit,
                override_required=override_required,
                approved_excess=credit_excess if override_required else ZERO,
            ),
            reservations=reservations,
            reserved_quantity_base=sum(
                (line.reserved_quantity_base for line in reservations), ZERO
            ),
            backorder_quantity_base=sum(
                (line.backorder_quantity_base for line in reservations), ZERO
            ),
        )
        await store_command_result(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result=result,
        )
        return result


@router.patch(
    "/orders/{sales_order_id}/non-material",
    response_model=NonMaterialOrderChangeResponse,
    responses=error_responses(401, 403, 404, 409, 422, 500),
)
async def change_non_material_order_fields(
    sales_order_id: UUID,
    command: NonMaterialOrderChangeCommand,
    request: Request,
    actor: Annotated[AuthorizedUser, Depends(require_sales_order_writer)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    if_match: Annotated[int, Header(alias="If-Match", ge=1)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
) -> NonMaterialOrderChangeResponse:
    request_hash = _request_hash(
        "non_material_order_change", command, f"{sales_order_id}:{if_match}"
    )
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
            return NonMaterialOrderChangeResponse.model_validate(replay)
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
            raise AppError(409, "optimistic_version_conflict", "The Sales Order changed.")
        updated = await session.execute(
            update(sales_orders)
            .where(
                sales_orders.c.sales_order_id == sales_order_id,
                sales_orders.c.metadata_version == if_match,
            )
            .values(
                notes=command.notes,
                delivery_instructions=command.delivery_instructions,
                metadata_version=if_match + 1,
                updated_by=actor.subject,
                updated_at=func.now(),
            )
            .returning(sales_orders.c.sales_order_id)
        )
        if updated.scalar_one_or_none() is None:
            raise AppError(409, "optimistic_version_conflict", "The Sales Order changed.")
        approval_id = await _active_approval_id(session, sales_order_id)
        result = NonMaterialOrderChangeResponse(
            sales_order_id=sales_order_id,
            commercial_version=order["version"],
            version=if_match + 1,
            status=order["status"],
            notes=command.notes,
            delivery_instructions=command.delivery_instructions,
            commercial_approval_id=approval_id,
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
    "/projections/rebuild",
    response_model=ProjectionRebuildResponse,
    responses=error_responses(401, 403, 500),
)
async def rebuild_commercial_projections(
    actor: Annotated[AuthorizedUser, Depends(require_sales_projection_rebuilder)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ProjectionRebuildResponse:
    await session.rollback()
    async with session.begin():
        await _advisory_lock(session, "commercial-projections")
        customer_rows = (
            await session.execute(
                select(
                    credit_exposure_entries.c.customer_id,
                    func.coalesce(
                        func.sum(credit_exposure_entries.c.amount_delta).filter(
                            credit_exposure_entries.c.component == "posted_open_balance"
                        ),
                        ZERO,
                    ).label("open_balance"),
                    func.coalesce(
                        func.sum(credit_exposure_entries.c.amount_delta).filter(
                            credit_exposure_entries.c.component == "approved_uninvoiced"
                        ),
                        ZERO,
                    ).label("approved_uninvoiced"),
                ).group_by(credit_exposure_entries.c.customer_id)
            )
        ).mappings()
        await session.execute(delete(customer_credit_exposure))
        credit_customers = 0
        for row in customer_rows:
            await session.execute(
                insert(customer_credit_exposure).values(
                    customer_id=row["customer_id"],
                    open_balance=row["open_balance"],
                    approved_uninvoiced=row["approved_uninvoiced"],
                )
            )
            credit_customers += 1
        reservation_rows = (
            await session.execute(
                select(
                    inventory_reservation_events.c.sku_id,
                    inventory_reservation_events.c.warehouse_id,
                    func.sum(
                        case(
                            (
                                inventory_reservation_events.c.event_type == "reserved",
                                inventory_reservation_events.c.quantity_base,
                            ),
                            else_=-inventory_reservation_events.c.quantity_base,
                        )
                    ).label("reserved_quantity_base"),
                ).group_by(
                    inventory_reservation_events.c.sku_id,
                    inventory_reservation_events.c.warehouse_id,
                )
            )
        ).mappings()
        await session.execute(delete(inventory_reserved_by_sku_warehouse))
        reservation_items = 0
        for row in reservation_rows:
            await session.execute(
                insert(inventory_reserved_by_sku_warehouse).values(
                    sku_id=row["sku_id"],
                    warehouse_id=row["warehouse_id"],
                    reserved_quantity_base=row["reserved_quantity_base"],
                )
            )
            reservation_items += 1
        active_lines = (
            await session.execute(
                select(
                    commercial_approvals.c.commercial_approval_id,
                    commercial_approvals.c.sales_order_id,
                    commercial_approvals.c.sales_order_revision_id,
                    commercial_approvals.c.warehouse_id,
                    sales_order_line_revisions.c.line_id,
                    sales_order_line_revisions.c.sku_id,
                    sales_order_line_revisions.c.quantity_base.label("ordered_quantity_base"),
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    inventory_reservation_events.c.event_type == "reserved",
                                    inventory_reservation_events.c.quantity_base,
                                ),
                                else_=-inventory_reservation_events.c.quantity_base,
                            )
                        ),
                        ZERO,
                    ).label("reserved_quantity_base"),
                )
                .select_from(
                    commercial_approvals.join(
                        sales_order_line_revisions,
                        commercial_approvals.c.sales_order_revision_id
                        == sales_order_line_revisions.c.sales_order_revision_id,
                    )
                    .outerjoin(
                        commercial_approval_invalidations,
                        commercial_approvals.c.commercial_approval_id
                        == commercial_approval_invalidations.c.commercial_approval_id,
                    )
                    .outerjoin(
                        inventory_reservation_events,
                        (
                            commercial_approvals.c.commercial_approval_id
                            == inventory_reservation_events.c.commercial_approval_id
                        )
                        & (
                            sales_order_line_revisions.c.line_id
                            == inventory_reservation_events.c.line_id
                        ),
                    )
                )
                .where(commercial_approval_invalidations.c.invalidation_id.is_(None))
                .group_by(
                    commercial_approvals.c.commercial_approval_id,
                    commercial_approvals.c.sales_order_id,
                    commercial_approvals.c.sales_order_revision_id,
                    commercial_approvals.c.warehouse_id,
                    sales_order_line_revisions.c.line_id,
                    sales_order_line_revisions.c.sku_id,
                    sales_order_line_revisions.c.quantity_base,
                )
            )
        ).mappings()
        await session.execute(delete(sales_order_line_commitments))
        line_commitments = 0
        for row in active_lines:
            await session.execute(
                insert(sales_order_line_commitments).values(
                    sales_order_id=row["sales_order_id"],
                    line_id=row["line_id"],
                    commercial_approval_id=row["commercial_approval_id"],
                    sales_order_revision_id=row["sales_order_revision_id"],
                    sku_id=row["sku_id"],
                    warehouse_id=row["warehouse_id"],
                    ordered_quantity_base=row["ordered_quantity_base"],
                    reserved_quantity_base=row["reserved_quantity_base"],
                    backorder_quantity_base=(
                        row["ordered_quantity_base"] - row["reserved_quantity_base"]
                    ),
                )
            )
            line_commitments += 1
        return ProjectionRebuildResponse(
            credit_customers=credit_customers,
            line_commitments=line_commitments,
            reservation_items=reservation_items,
        )
