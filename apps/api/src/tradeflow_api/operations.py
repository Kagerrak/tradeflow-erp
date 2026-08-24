# ruff: noqa: E501 - SQL fragments remain readable when their clauses stay intact.
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tradeflow_api.auth import AuthorizedUser, load_authorized_user
from tradeflow_api.database import get_database_session
from tradeflow_api.errors import AppError, error_responses

router = APIRouter(prefix="/v1/operations", tags=["operations"])
ZERO = Decimal("0")


class BranchOption(BaseModel):
    branch_id: UUID
    code: str
    name: str


class Metric(BaseModel):
    key: str
    label: str
    count: int | None = None
    amount: Decimal | None = None
    currency: str | None = None


class ActionQueueItem(BaseModel):
    record_id: UUID
    kind: Literal["approval", "pick", "delivery", "payment", "stock"]
    title: str
    reference: str
    branch_code: str
    owner: str
    status: str
    urgency: Literal["high", "medium", "normal"]
    age_minutes: int
    amount: Decimal | None = None
    currency: str | None = None
    next_action: str
    href: str


class PipelineStage(BaseModel):
    key: str
    label: str
    count: int
    value: Decimal
    currency: str


class InventoryHealth(BaseModel):
    available: Decimal
    reserved: Decimal
    low_stock_items: int
    blocked_lots: int
    pending_transfers: int
    pending_adjustments: int
    unit: str


class FinanceSnapshot(BaseModel):
    posted_invoices: int
    posted_value: Decimal
    receipts_awaiting_verification: int
    receipts_awaiting_value: Decimal
    overdue_balances: Decimal
    outstanding_receivables: Decimal
    collected_value: Decimal
    currency: str


class ActivityItem(BaseModel):
    activity_id: UUID
    kind: str
    title: str
    detail: str
    branch_code: str
    occurred_at: datetime
    href: str


class OperationsOverviewResponse(BaseModel):
    generated_at: datetime
    from_date: date
    to_date: date
    selected_branch_id: UUID | None
    branches: list[BranchOption]
    metrics: list[Metric]
    action_queue: list[ActionQueueItem]
    pipeline: list[PipelineStage]
    inventory: InventoryHealth
    finance: FinanceSnapshot
    recent_activity: list[ActivityItem]


def _age_minutes(value: datetime, now: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return max(0, int((now - value).total_seconds() // 60))


def _urgency(kind: str, age_minutes: int) -> Literal["high", "medium", "normal"]:
    if kind in {"payment", "delivery", "stock"} or age_minutes >= 24 * 60:
        return "high"
    if kind == "approval" or age_minutes >= 8 * 60:
        return "medium"
    return "normal"


@router.get(
    "/overview",
    response_model=OperationsOverviewResponse,
    responses=error_responses(401, 403, 422, 500),
)
async def operations_overview(
    actor: Annotated[AuthorizedUser, Depends(load_authorized_user)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    branch_id: Annotated[UUID | None, Query()] = None,
    from_date: Annotated[date | None, Query()] = None,
    to_date: Annotated[date | None, Query()] = None,
) -> OperationsOverviewResponse:
    today = datetime.now(UTC).date()
    resolved_to = to_date or today
    resolved_from = from_date or (resolved_to - timedelta(days=30))
    if resolved_from > resolved_to:
        raise AppError(422, "invalid_date_range", "The start date must not follow the end date.")

    branch_rows = (
        (
            await session.execute(
                text(
                    "SELECT branch_id, code, name FROM branches "
                    "WHERE branch_id = ANY(:branch_ids) ORDER BY code"
                ),
                {"branch_ids": list(actor.branch_ids)},
            )
        )
        .mappings()
        .all()
    )
    if branch_id is not None and branch_id not in actor.branch_ids:
        raise AppError(403, "operational_scope_required", "Branch scope is required.")
    scoped_branch_ids = [branch_id] if branch_id is not None else list(actor.branch_ids)
    if not scoped_branch_ids:
        return _empty_overview(
            resolved_from,
            resolved_to,
            [dict(row) for row in branch_rows],
            branch_id,
        )

    params = {
        "branch_ids": scoped_branch_ids,
        "from_at": datetime.combine(resolved_from, time.min, tzinfo=UTC),
        "to_at": datetime.combine(resolved_to + timedelta(days=1), time.min, tzinfo=UTC),
    }
    summary = (
        (
            await session.execute(
                text(
                    """
                SELECT
                  (SELECT count(*) FROM sales_orders
                    WHERE branch_id = ANY(:branch_ids) AND status = 'awaiting_approval')
                    AS awaiting_approval,
                  (SELECT count(*) FROM fulfillment_order_state state
                    JOIN fulfillment_orders orders USING (fulfillment_order_id)
                    WHERE orders.branch_id = ANY(:branch_ids) AND state.status = 'pick_released')
                    AS ready_to_pick,
                  (SELECT count(*) FROM delivery_state state
                    JOIN delivery_dispatches delivery USING (delivery_id)
                    WHERE delivery.branch_id = ANY(:branch_ids) AND state.status = 'dispatched')
                    AS awaiting_confirmation,
                  (SELECT count(*) FROM payment_receipt_status status
                    JOIN payment_receipts receipt USING (payment_receipt_id)
                    WHERE receipt.branch_id = ANY(:branch_ids)
                      AND status.state = 'pending_verification') AS awaiting_verification,
                  (SELECT coalesce(sum(amount), 0) FROM customer_ledger_entries
                    WHERE branch_id = ANY(:branch_ids)) AS outstanding_receivables,
                  (SELECT base_currency FROM companies LIMIT 1) AS currency
                """
                ),
                params,
            )
        )
        .mappings()
        .one()
    )

    stock_rows = (
        (
            await session.execute(
                text(
                    """
                WITH available AS (
                  SELECT inventory.sku_id, inventory.warehouse_id,
                         sum(inventory.on_hand) AS on_hand
                  FROM inventory_availability inventory
                  JOIN warehouse_stock_locations location USING (location_id)
                  WHERE inventory.warehouse_id IN (
                    SELECT warehouse_id FROM warehouses WHERE branch_id = ANY(:branch_ids)
                  ) AND location.custody = 'available' AND location.is_active
                  GROUP BY inventory.sku_id, inventory.warehouse_id
                )
                SELECT available.sku_id, available.warehouse_id, sku.code AS sku_code,
                       sku.name AS sku_name, warehouse.code AS warehouse_code,
                       branch.code AS branch_code,
                       coalesce(reserved.reserved_quantity_base, 0) AS reserved,
                       greatest(available.on_hand - coalesce(reserved.reserved_quantity_base, 0), 0)
                         AS available
                FROM available
                JOIN skus sku USING (sku_id)
                JOIN warehouses warehouse USING (warehouse_id)
                JOIN branches branch USING (branch_id)
                LEFT JOIN inventory_reserved_by_sku_warehouse reserved
                  USING (sku_id, warehouse_id)
                """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    low_stock_items = sum(1 for row in stock_rows if Decimal(row["available"]) <= ZERO)

    inventory_row = (
        (
            await session.execute(
                text(
                    """
                SELECT
                  (SELECT count(DISTINCT (inventory.sku_id, inventory.identity_key))
                   FROM inventory_availability inventory
                   JOIN warehouse_stock_locations location
                     ON location.location_id = inventory.location_id
                   JOIN warehouses warehouse
                     ON warehouse.warehouse_id = inventory.warehouse_id
                   WHERE warehouse.branch_id = ANY(:branch_ids)
                     AND location.custody = 'quarantine') AS blocked_lots,
                  (SELECT count(*) FROM inventory_transfers transfer
                   JOIN warehouses warehouse ON warehouse.warehouse_id = transfer.from_warehouse_id
                   WHERE warehouse.branch_id = ANY(:branch_ids) AND transfer.status = 'released')
                    AS pending_transfers,
                  (SELECT count(*) FROM inventory_adjustments adjustment
                   JOIN warehouses warehouse USING (warehouse_id)
                   WHERE warehouse.branch_id = ANY(:branch_ids)
                     AND adjustment.status = 'pending_authorization') AS pending_adjustments
                """
                ),
                params,
            )
        )
        .mappings()
        .one()
    )

    finance_row = (
        (
            await session.execute(
                text(
                    """
                SELECT
                  (SELECT count(*) FROM (
                    SELECT entry.invoice_id FROM customer_ledger_entries entry
                    WHERE entry.branch_id = ANY(:branch_ids) AND entry.invoice_id IS NOT NULL
                    GROUP BY entry.invoice_id HAVING sum(entry.amount) > 0
                  ) posted) AS posted_invoices,
                  (SELECT coalesce(sum(invoice.grand_total), 0) FROM draft_invoices invoice
                    WHERE invoice.branch_id = ANY(:branch_ids) AND EXISTS (
                      SELECT 1 FROM customer_ledger_entries entry
                      WHERE entry.invoice_id = invoice.draft_invoice_id
                        AND entry.entry_type = 'invoice'
                    )) AS posted_value,
                  (SELECT count(*) FROM payment_receipt_status status
                    JOIN payment_receipts receipt USING (payment_receipt_id)
                    WHERE receipt.branch_id = ANY(:branch_ids)
                      AND status.state = 'pending_verification') AS pending_receipts,
                  (SELECT coalesce(sum(receipt.amount), 0) FROM payment_receipt_status status
                    JOIN payment_receipts receipt USING (payment_receipt_id)
                    WHERE receipt.branch_id = ANY(:branch_ids)
                      AND status.state = 'pending_verification') AS pending_value,
                  (SELECT coalesce(sum(amount), 0) FROM customer_ledger_entries
                    WHERE branch_id = ANY(:branch_ids)) AS outstanding,
                  (SELECT coalesce(sum(receipt.amount), 0) FROM payment_receipt_status status
                    JOIN payment_receipts receipt USING (payment_receipt_id)
                    WHERE receipt.branch_id = ANY(:branch_ids) AND status.state = 'cleared'
                      AND receipt.received_at >= :from_at AND receipt.received_at < :to_at)
                    AS collected,
                  (SELECT coalesce(sum(balance), 0) FROM (
                    SELECT min(entry.posted_at) AS posted_at,
                      greatest(sum(entry.amount), 0) AS balance,
                      coalesce(nullif(regexp_replace(customer.payment_terms, '[^0-9]', '', 'g'), '')::int, 0)
                        AS term_days
                    FROM draft_invoices invoice
                    JOIN customer_accounts customer USING (customer_id)
                    JOIN customer_ledger_entries entry ON entry.invoice_id = invoice.draft_invoice_id
                    WHERE invoice.branch_id = ANY(:branch_ids) AND EXISTS (
                      SELECT 1 FROM customer_ledger_entries posted
                      WHERE posted.invoice_id = invoice.draft_invoice_id
                        AND posted.entry_type = 'invoice'
                    )
                    GROUP BY invoice.draft_invoice_id, customer.payment_terms
                  ) invoice_balance
                  WHERE invoice_balance.posted_at + make_interval(days => invoice_balance.term_days)
                    < :to_at AND invoice_balance.balance > 0) AS overdue
                """
                ),
                params,
            )
        )
        .mappings()
        .one()
    )

    action_rows = (
        (
            await session.execute(
                text(
                    """
                SELECT * FROM (
                  SELECT orders.sales_order_id AS record_id, 'approval' AS kind,
                    customer.legal_name AS title, orders.sales_order_id::text AS reference,
                    branch.code AS branch_code, orders.created_by AS owner,
                    orders.status, revision.grand_total AS amount, revision.currency,
                    orders.updated_at AS occurred_at,
                    '/sales-orders/approvals?orderId=' || orders.sales_order_id::text AS href
                  FROM sales_orders orders
                  JOIN sales_order_revisions revision ON revision.sales_order_id = orders.sales_order_id
                    AND revision.version = orders.version
                  JOIN customer_accounts customer ON customer.customer_id = orders.customer_id
                  JOIN branches branch ON branch.branch_id = orders.branch_id
                  WHERE orders.branch_id = ANY(:branch_ids) AND orders.status = 'awaiting_approval'
                  UNION ALL
                  SELECT fulfillment.fulfillment_order_id, 'pick', customer.legal_name,
                    fulfillment.fulfillment_order_id::text, branch.code, fulfillment.created_by,
                    state.status, fulfillment.order_value, fulfillment.currency, state.updated_at,
                    '/picking?fulfillmentOrderId=' || fulfillment.fulfillment_order_id::text
                  FROM fulfillment_orders fulfillment
                  JOIN fulfillment_order_state state
                    ON state.fulfillment_order_id = fulfillment.fulfillment_order_id
                  JOIN customer_accounts customer
                    ON customer.customer_id = fulfillment.customer_id
                  JOIN branches branch ON branch.branch_id = fulfillment.branch_id
                  WHERE fulfillment.branch_id = ANY(:branch_ids) AND state.status = 'pick_released'
                  UNION ALL
                  SELECT delivery.delivery_id, 'delivery', customer.legal_name,
                    delivery.delivery_id::text, branch.code, state.assigned_to, state.status,
                    fulfillment.order_value, fulfillment.currency, state.updated_at,
                    '/deliveries?deliveryId=' || delivery.delivery_id::text
                  FROM delivery_dispatches delivery
                  JOIN delivery_state state ON state.delivery_id = delivery.delivery_id
                  JOIN fulfillment_orders fulfillment
                    ON fulfillment.fulfillment_order_id = delivery.fulfillment_order_id
                  JOIN customer_accounts customer
                    ON customer.customer_id = fulfillment.customer_id
                  JOIN branches branch ON branch.branch_id = delivery.branch_id
                  WHERE delivery.branch_id = ANY(:branch_ids) AND state.status = 'dispatched'
                  UNION ALL
                  SELECT receipt.payment_receipt_id, 'payment', customer.legal_name,
                    receipt.payment_receipt_id::text, branch.code, receipt.recorded_by, status.state,
                    receipt.amount, receipt.currency, status.updated_at,
                    '/payments?paymentReceiptId=' || receipt.payment_receipt_id::text
                  FROM payment_receipts receipt
                  JOIN payment_receipt_status status
                    ON status.payment_receipt_id = receipt.payment_receipt_id
                  JOIN customer_accounts customer ON customer.customer_id = receipt.customer_id
                  JOIN branches branch ON branch.branch_id = receipt.branch_id
                  WHERE receipt.branch_id = ANY(:branch_ids) AND status.state = 'pending_verification'
                ) queue ORDER BY occurred_at ASC LIMIT 20
                """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )

    pipeline_rows = (
        (
            await session.execute(
                text(
                    """
                SELECT key, label, count(*), coalesce(sum(value), 0) AS value FROM (
                  SELECT 'approval' AS key, 'Approval' AS label, revision.grand_total AS value
                  FROM sales_orders orders JOIN sales_order_revisions revision
                    ON revision.sales_order_id = orders.sales_order_id AND revision.version = orders.version
                  WHERE orders.branch_id = ANY(:branch_ids) AND orders.status = 'awaiting_approval'
                  UNION ALL SELECT 'reservation', 'Reservation', fulfillment.order_value
                  FROM fulfillment_orders fulfillment JOIN fulfillment_order_state state USING (fulfillment_order_id)
                  WHERE fulfillment.branch_id = ANY(:branch_ids) AND state.status IN ('reserved','payment_ready','payment_hold')
                  UNION ALL SELECT 'picking', 'Picking', fulfillment.order_value
                  FROM fulfillment_orders fulfillment JOIN fulfillment_order_state state USING (fulfillment_order_id)
                  WHERE fulfillment.branch_id = ANY(:branch_ids) AND state.status IN ('pick_released','partially_picked','picked')
                  UNION ALL SELECT 'delivery', 'Delivery', fulfillment.order_value
                  FROM fulfillment_orders fulfillment JOIN fulfillment_order_state state USING (fulfillment_order_id)
                  WHERE fulfillment.branch_id = ANY(:branch_ids) AND state.status IN ('dispatched','partially_delivered')
                  UNION ALL SELECT 'invoicing', 'Invoicing', invoice.grand_total
                  FROM draft_invoices invoice WHERE invoice.branch_id = ANY(:branch_ids) AND invoice.status IN ('draft','posted')
                  UNION ALL SELECT 'payment', 'Payment', receipt.amount
                  FROM payment_receipts receipt JOIN payment_receipt_status status USING (payment_receipt_id)
                  WHERE receipt.branch_id = ANY(:branch_ids) AND status.state IN ('pending_verification','pending_clearance','cleared')
                ) pipeline GROUP BY key, label
                """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    pipeline_by_key = {row["key"]: row for row in pipeline_rows}

    activity_rows = (
        (
            await session.execute(
                text(
                    """
                SELECT * FROM (
                  SELECT approval.commercial_approval_id AS activity_id, 'approval' AS kind,
                    'Commercial approval accepted' AS title,
                    customer.legal_name AS detail, branch.code AS branch_code,
                    approval.created_at AS occurred_at,
                    '/sales-orders/approvals?orderId=' || approval.sales_order_id::text AS href
                  FROM commercial_approvals approval
                  JOIN customer_accounts customer
                    ON customer.customer_id = approval.customer_id
                  JOIN sales_orders orders ON orders.sales_order_id = approval.sales_order_id
                  JOIN branches branch ON branch.branch_id = orders.branch_id
                  WHERE orders.branch_id = ANY(:branch_ids)
                  UNION ALL
                  SELECT confirmation.confirmation_id, 'delivery', 'Delivery confirmed',
                    customer.legal_name, branch.code, confirmation.confirmed_at,
                    '/deliveries?deliveryId=' || confirmation.delivery_id::text
                  FROM delivery_confirmations confirmation
                  JOIN delivery_dispatches delivery
                    ON delivery.delivery_id = confirmation.delivery_id
                  JOIN customer_accounts customer
                    ON customer.customer_id = delivery.customer_id
                  JOIN branches branch ON branch.branch_id = delivery.branch_id
                  WHERE delivery.branch_id = ANY(:branch_ids)
                  UNION ALL
                  SELECT entry.entry_id, entry.entry_type, 'Finance entry posted',
                    customer.legal_name, branch.code, entry.created_at,
                    '/finance/statement?customerId=' || entry.customer_id::text
                  FROM customer_ledger_entries entry
                  JOIN customer_accounts customer ON customer.customer_id = entry.customer_id
                  JOIN branches branch ON branch.branch_id = entry.branch_id
                  WHERE entry.branch_id = ANY(:branch_ids)
                  UNION ALL
                  SELECT adjustment.adjustment_id, 'adjustment', 'Inventory adjustment requested',
                    sku.name, branch.code, adjustment.requested_at, '/inventory/adjustments'
                  FROM inventory_adjustments adjustment
                  JOIN skus sku ON sku.sku_id = adjustment.sku_id
                  JOIN warehouses warehouse
                    ON warehouse.warehouse_id = adjustment.warehouse_id
                  JOIN branches branch ON branch.branch_id = warehouse.branch_id
                  WHERE branch.branch_id = ANY(:branch_ids)
                ) activity
                WHERE occurred_at >= :from_at AND occurred_at < :to_at
                ORDER BY occurred_at DESC LIMIT 12
                """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )

    now = datetime.now(UTC)
    currency = str(summary["currency"] or "PHP")
    available = sum((Decimal(row["available"]) for row in stock_rows), ZERO)
    reserved = sum((Decimal(row["reserved"]) for row in stock_rows), ZERO)
    pipeline = []
    for key, label in (
        ("approval", "Approval"),
        ("reservation", "Reservation"),
        ("picking", "Picking"),
        ("delivery", "Delivery"),
        ("invoicing", "Invoicing"),
        ("payment", "Payment"),
    ):
        pipeline_row = pipeline_by_key.get(key)
        pipeline.append(
            PipelineStage(
                key=key,
                label=label,
                count=int(pipeline_row["count"]) if pipeline_row else 0,
                value=Decimal(pipeline_row["value"]) if pipeline_row else ZERO,
                currency=currency,
            )
        )
    action_queue = [
        ActionQueueItem(
            record_id=row["record_id"],
            kind=row["kind"],
            title=row["title"],
            reference=str(row["reference"]),
            branch_code=row["branch_code"],
            owner=row["owner"],
            status=row["status"],
            urgency=_urgency(row["kind"], _age_minutes(row["occurred_at"], now)),
            age_minutes=_age_minutes(row["occurred_at"], now),
            amount=row["amount"],
            currency=row["currency"],
            next_action={
                "approval": "Review order",
                "pick": "Start picking",
                "delivery": "Confirm delivery",
                "payment": "Verify receipt",
            }[row["kind"]],
            href=row["href"],
        )
        for row in action_rows
    ]
    action_queue.extend(
        ActionQueueItem(
            record_id=row["sku_id"],
            kind="stock",
            title=row["sku_name"],
            reference=f"{row['sku_code']} / {row['warehouse_code']}",
            branch_code=row["branch_code"],
            owner="Inventory control",
            status="Out of available stock",
            urgency="high",
            age_minutes=0,
            next_action="Review stock",
            href="/inventory",
        )
        for row in stock_rows
        if Decimal(row["available"]) <= ZERO
    )
    low_metric = Metric(key="low_stock", label="Low-stock items", count=low_stock_items)
    return OperationsOverviewResponse(
        generated_at=now,
        from_date=resolved_from,
        to_date=resolved_to,
        selected_branch_id=branch_id,
        branches=[BranchOption(**row) for row in branch_rows],
        metrics=[
            Metric(
                key="awaiting_approval",
                label="Orders awaiting approval",
                count=summary["awaiting_approval"],
            ),
            Metric(
                key="ready_to_pick", label="Orders ready to pick", count=summary["ready_to_pick"]
            ),
            Metric(
                key="awaiting_confirmation",
                label="Deliveries awaiting confirmation",
                count=summary["awaiting_confirmation"],
            ),
            Metric(
                key="awaiting_verification",
                label="Payments awaiting verification",
                count=summary["awaiting_verification"],
            ),
            low_metric,
            Metric(
                key="receivables",
                label="Outstanding receivables",
                amount=summary["outstanding_receivables"],
                currency=currency,
            ),
        ],
        action_queue=action_queue,
        pipeline=pipeline,
        inventory=InventoryHealth(
            available=available,
            reserved=reserved,
            low_stock_items=low_stock_items,
            blocked_lots=int(inventory_row["blocked_lots"]),
            pending_transfers=int(inventory_row["pending_transfers"]),
            pending_adjustments=int(inventory_row["pending_adjustments"]),
            unit="base units",
        ),
        finance=FinanceSnapshot(
            posted_invoices=int(finance_row["posted_invoices"]),
            posted_value=finance_row["posted_value"],
            receipts_awaiting_verification=int(finance_row["pending_receipts"]),
            receipts_awaiting_value=finance_row["pending_value"],
            overdue_balances=finance_row["overdue"],
            outstanding_receivables=finance_row["outstanding"],
            collected_value=finance_row["collected"],
            currency=currency,
        ),
        recent_activity=[ActivityItem(**row) for row in activity_rows],
    )


def _empty_overview(
    from_date: date,
    to_date: date,
    branches: Sequence[Mapping[str, Any]],
    branch_id: UUID | None,
) -> OperationsOverviewResponse:
    labels = (
        ("awaiting_approval", "Orders awaiting approval"),
        ("ready_to_pick", "Orders ready to pick"),
        ("awaiting_confirmation", "Deliveries awaiting confirmation"),
        ("awaiting_verification", "Payments awaiting verification"),
        ("low_stock", "Low-stock items"),
    )
    return OperationsOverviewResponse(
        generated_at=datetime.now(UTC),
        from_date=from_date,
        to_date=to_date,
        selected_branch_id=branch_id,
        branches=[BranchOption(**row) for row in branches],
        metrics=[Metric(key=key, label=label, count=0) for key, label in labels]
        + [Metric(key="receivables", label="Outstanding receivables", amount=ZERO, currency="PHP")],
        action_queue=[],
        pipeline=[
            PipelineStage(key=key, label=label, count=0, value=ZERO, currency="PHP")
            for key, label in (
                ("approval", "Approval"),
                ("reservation", "Reservation"),
                ("picking", "Picking"),
                ("delivery", "Delivery"),
                ("invoicing", "Invoicing"),
                ("payment", "Payment"),
            )
        ],
        inventory=InventoryHealth(
            available=ZERO,
            reserved=ZERO,
            low_stock_items=0,
            blocked_lots=0,
            pending_transfers=0,
            pending_adjustments=0,
            unit="base units",
        ),
        finance=FinanceSnapshot(
            posted_invoices=0,
            posted_value=ZERO,
            receipts_awaiting_verification=0,
            receipts_awaiting_value=ZERO,
            overdue_balances=ZERO,
            outstanding_receivables=ZERO,
            collected_value=ZERO,
            currency="PHP",
        ),
        recent_activity=[],
    )
