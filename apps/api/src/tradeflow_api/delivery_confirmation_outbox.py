from __future__ import annotations

from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from tradeflow_api.errors import AppError
from tradeflow_api.models import (
    delivery_confirmation_lines,
    delivery_confirmations,
    delivery_dispatches,
    draft_invoice_lines,
    draft_invoices,
    outbox_events,
    outbox_handler_receipts,
    sales_order_line_revisions,
    sales_order_revisions,
)
from tradeflow_api.money import currency_quantum

HANDLER_NAME = "finance.draft-invoice.v1"
ZERO = Decimal("0")
SIX_PLACES = Decimal("0.000001")


def _id(kind: str, source_id: UUID | str) -> UUID:
    return uuid5(NAMESPACE_URL, f"tradeflow:{kind}:{source_id}")


async def create_draft_invoice_for_event(
    session: AsyncSession,
    outbox_event_id: UUID,
) -> UUID:
    event = (
        (
            await session.execute(
                select(outbox_events)
                .where(outbox_events.c.outbox_event_id == outbox_event_id)
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if event is None or event["event_type"] != "delivery.confirmed.v1":
        raise AppError(404, "outbox_event_not_found", "Delivery confirmation event not found.")
    prior = await session.scalar(
        select(outbox_handler_receipts.c.result_id).where(
            outbox_handler_receipts.c.outbox_event_id == outbox_event_id,
            outbox_handler_receipts.c.handler_name == HANDLER_NAME,
        )
    )
    if prior is not None:
        return cast(UUID, prior)
    confirmation_id = UUID(str(event["payload"]["confirmation_id"]))
    source = (
        (
            await session.execute(
                select(
                    delivery_confirmations.c.confirmation_id,
                    delivery_dispatches.c.sales_order_id,
                    delivery_dispatches.c.sales_order_revision_id,
                    delivery_dispatches.c.customer_id,
                    delivery_dispatches.c.branch_id,
                    sales_order_revisions.c.currency,
                )
                .join(
                    delivery_dispatches,
                    delivery_confirmations.c.delivery_id == delivery_dispatches.c.delivery_id,
                )
                .join(
                    sales_order_revisions,
                    delivery_dispatches.c.sales_order_revision_id
                    == sales_order_revisions.c.sales_order_revision_id,
                )
                .where(delivery_confirmations.c.confirmation_id == confirmation_id)
            )
        )
        .mappings()
        .one()
    )
    accepted_rows = list(
        (
            await session.execute(
                select(
                    delivery_confirmation_lines.c.line_id,
                    delivery_confirmation_lines.c.sku_id,
                    delivery_confirmation_lines.c.accepted_quantity_base,
                ).where(delivery_confirmation_lines.c.confirmation_id == confirmation_id)
            )
        ).mappings()
    )
    accepted: dict[tuple[UUID, UUID], Decimal] = defaultdict(lambda: ZERO)
    for row in accepted_rows:
        accepted[(row["line_id"], row["sku_id"])] += row["accepted_quantity_base"]
    source_lines = list(
        (
            await session.execute(
                select(sales_order_line_revisions)
                .where(
                    sales_order_line_revisions.c.sales_order_revision_id
                    == source["sales_order_revision_id"],
                    sales_order_line_revisions.c.line_id.in_([line_id for line_id, _ in accepted]),
                )
                .order_by(sales_order_line_revisions.c.line_position)
                .with_for_update()
            )
        ).mappings()
    )
    currency = cast(str, source["currency"])
    quantum = currency_quantum(currency)
    invoice_id = _id("draft-invoice", confirmation_id)
    invoice_lines: list[dict[str, object]] = []
    subtotal = ZERO
    discount = ZERO
    tax = ZERO
    total = ZERO
    for line in source_lines:
        quantity = accepted[(line["line_id"], line["sku_id"])]
        ratio = quantity / cast(Decimal, line["quantity_base"])
        line_subtotal = (quantity * line["effective_unit_price"]).quantize(quantum, ROUND_HALF_UP)
        line_discount = (line["allocated_discount"] * ratio).quantize(quantum, ROUND_HALF_UP)
        line_tax = (line["tax_amount"] * ratio).quantize(quantum, ROUND_HALF_UP)
        line_total = (line["line_total"] * ratio).quantize(quantum, ROUND_HALF_UP)
        invoice_lines.append(
            {
                "draft_invoice_line_id": _id(
                    "draft-invoice-line", f"{invoice_id}:{line['line_id']}"
                ),
                "draft_invoice_id": invoice_id,
                "line_id": line["line_id"],
                "sku_id": line["sku_id"],
                "accepted_quantity_base": quantity.quantize(SIX_PLACES),
                "unit_price": line["effective_unit_price"],
                "subtotal": line_subtotal,
                "discount_amount": line_discount,
                "tax_amount": line_tax,
                "line_total": line_total,
                "calculation_snapshot": {
                    "source_sales_order_line_revision_id": str(
                        line["sales_order_line_revision_id"]
                    ),
                    "accepted_ratio": str(ratio),
                    "source": dict(line["calculation_snapshot"]),
                },
            }
        )
        subtotal += line_subtotal
        discount += line_discount
        tax += line_tax
        total += line_total
    await session.execute(
        insert(draft_invoices).values(
            draft_invoice_id=invoice_id,
            delivery_confirmation_id=confirmation_id,
            source_event_id=outbox_event_id,
            status="draft",
            sales_order_id=source["sales_order_id"],
            sales_order_revision_id=source["sales_order_revision_id"],
            customer_id=source["customer_id"],
            branch_id=source["branch_id"],
            currency=currency,
            subtotal=subtotal,
            discount_total=discount,
            tax_total=tax,
            grand_total=total,
            source_snapshot={
                "delivery_confirmation_id": str(confirmation_id),
                "outbox_event_id": str(outbox_event_id),
            },
        )
    )
    if invoice_lines:
        await session.execute(insert(draft_invoice_lines), invoice_lines)
    await session.execute(
        insert(outbox_handler_receipts).values(
            outbox_handler_receipt_id=_id("outbox-handler-receipt", outbox_event_id),
            outbox_event_id=outbox_event_id,
            handler_name=HANDLER_NAME,
            result_id=invoice_id,
        )
    )
    return invoice_id
