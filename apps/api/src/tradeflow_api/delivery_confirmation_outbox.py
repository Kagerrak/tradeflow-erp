from __future__ import annotations

from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256
from io import BytesIO
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid5

from reportlab.pdfgen import canvas  # type: ignore[import-untyped]
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tradeflow_api.errors import AppError
from tradeflow_api.models import (
    delivery_confirmation_lines,
    delivery_confirmations,
    delivery_dispatches,
    delivery_receipt_documents,
    delivery_receipts,
    draft_invoice_lines,
    draft_invoices,
    outbox_events,
    outbox_handler_receipts,
    sales_order_line_revisions,
    sales_order_revisions,
)
from tradeflow_api.money import currency_quantum
from tradeflow_api.object_storage import ObjectStorage

HANDLER_NAME = "finance.draft-invoice.v1"
RECEIPT_HANDLER_NAME = "documents.delivery-receipt.v1"
ZERO = Decimal("0")
SIX_PLACES = Decimal("0.000001")


def _id(kind: str, source_id: UUID | str) -> UUID:
    return uuid5(NAMESPACE_URL, f"tradeflow:{kind}:{source_id}")


def allocate_partial_line_amounts(
    *,
    quantity: Decimal,
    source_quantity: Decimal,
    source_subtotal: Decimal,
    source_discount: Decimal,
    source_tax: Decimal,
    source_total: Decimal,
    prior_quantity: Decimal,
    prior_subtotal: Decimal,
    prior_discount: Decimal,
    prior_tax: Decimal,
    prior_total: Decimal,
    quantum: Decimal,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    ratio = quantity / source_quantity
    if prior_quantity + quantity == source_quantity:
        return (
            source_subtotal - prior_subtotal,
            source_discount - prior_discount,
            source_tax - prior_tax,
            source_total - prior_total,
        )
    return (
        (source_subtotal * ratio).quantize(quantum, ROUND_HALF_UP),
        (source_discount * ratio).quantize(quantum, ROUND_HALF_UP),
        (source_tax * ratio).quantize(quantum, ROUND_HALF_UP),
        (source_total * ratio).quantize(quantum, ROUND_HALF_UP),
    )


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
        base_quantity_per_unit = Decimal(line["conversion_snapshot"]["base_quantity_per_unit"])
        base_unit_price = (
            cast(Decimal, line["effective_unit_price"]) / base_quantity_per_unit
        ).quantize(SIX_PLACES, ROUND_HALF_UP)
        prior = (
            await session.execute(
                select(
                    func.coalesce(func.sum(draft_invoice_lines.c.accepted_quantity_base), ZERO),
                    func.coalesce(func.sum(draft_invoice_lines.c.subtotal), ZERO),
                    func.coalesce(func.sum(draft_invoice_lines.c.discount_amount), ZERO),
                    func.coalesce(func.sum(draft_invoice_lines.c.tax_amount), ZERO),
                    func.coalesce(func.sum(draft_invoice_lines.c.line_total), ZERO),
                )
                .select_from(
                    draft_invoice_lines.join(
                        draft_invoices,
                        draft_invoice_lines.c.draft_invoice_id == draft_invoices.c.draft_invoice_id,
                    )
                )
                .where(
                    draft_invoices.c.sales_order_revision_id == source["sales_order_revision_id"],
                    draft_invoice_lines.c.line_id == line["line_id"],
                )
            )
        ).one()
        line_subtotal, line_discount, line_tax, line_total = allocate_partial_line_amounts(
            quantity=quantity,
            source_quantity=line["quantity_base"],
            source_subtotal=Decimal(line["calculation_snapshot"]["pre_discount_amount"]),
            source_discount=line["allocated_discount"],
            source_tax=line["tax_amount"],
            source_total=line["line_total"],
            prior_quantity=prior[0],
            prior_subtotal=prior[1],
            prior_discount=prior[2],
            prior_tax=prior[3],
            prior_total=prior[4],
            quantum=quantum,
        )
        invoice_lines.append(
            {
                "draft_invoice_line_id": _id(
                    "draft-invoice-line", f"{invoice_id}:{line['line_id']}"
                ),
                "draft_invoice_id": invoice_id,
                "line_id": line["line_id"],
                "sku_id": line["sku_id"],
                "accepted_quantity_base": quantity.quantize(SIX_PLACES),
                "unit_price": base_unit_price,
                "subtotal": line_subtotal,
                "discount_amount": line_discount,
                "tax_amount": line_tax,
                "line_total": line_total,
                "calculation_snapshot": {
                    "source_sales_order_line_revision_id": str(
                        line["sales_order_line_revision_id"]
                    ),
                    "accepted_ratio": str(ratio),
                    "base_quantity_per_entered_unit": str(base_quantity_per_unit),
                    "entered_unit_price": str(line["effective_unit_price"]),
                    "normalized_base_unit_price": str(base_unit_price),
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
            outbox_handler_receipt_id=_id("outbox-handler-receipt-finance", outbox_event_id),
            outbox_event_id=outbox_event_id,
            handler_name=HANDLER_NAME,
            result_id=invoice_id,
        )
    )
    return invoice_id


def _render_receipt_pdf(number: str, snapshot: dict[str, object]) -> bytes:
    target = BytesIO()
    document = canvas.Canvas(
        target,
        pagesize=(595, 842),
        invariant=1,
        pageCompression=0,
    )
    document.setTitle(number)
    document.setFont("Helvetica-Bold", 16)
    document.drawString(48, 790, "TradeFlow Delivery Receipt")
    document.setFont("Helvetica", 10)
    document.drawString(48, 770, number)
    document.drawString(48, 748, f"Recipient: {snapshot['recipient_name']}")
    document.drawString(
        48,
        732,
        f"Customer: {snapshot['customer_account_number']} - {snapshot['customer_legal_name']}",
    )
    address = cast(dict[str, object], snapshot["delivery_address"])
    address_text = ", ".join(
        str(address[key]) for key in ("line_1", "city", "region", "postal_code") if address.get(key)
    )
    document.drawString(48, 716, f"Delivery address: {address_text}")
    document.drawString(48, 700, f"Sales Order: {snapshot['sales_order_id']}")
    y = 674
    for line in cast(list[dict[str, object]], snapshot["lines"]):
        document.drawString(
            48,
            y,
            f"{line['sku_code']} — {line['sku_name']}",
        )
        document.drawRightString(
            547,
            y,
            f"{line['accepted_quantity_entered']} {line['entered_unit']}",
        )
        document.drawString(64, y - 12, f"Source line: {line['line_id']}")
        y -= 30
    document.drawString(48, y - 14, "Proof of Delivery evidence")
    y -= 30
    for evidence_id in cast(list[str], snapshot["evidence_ids"]):
        document.drawString(64, y, evidence_id)
        y -= 14
    document.showPage()
    document.save()
    return target.getvalue()


async def render_delivery_receipt_for_event(
    session: AsyncSession,
    outbox_event_id: UUID,
    storage: ObjectStorage,
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
            outbox_handler_receipts.c.handler_name == RECEIPT_HANDLER_NAME,
        )
    )
    if prior is not None:
        return cast(UUID, prior)
    receipt_id = UUID(str(event["payload"]["delivery_receipt_id"]))
    receipt = (
        (
            await session.execute(
                select(
                    delivery_receipts.c.number,
                    delivery_receipts.c.snapshot,
                    delivery_receipt_documents.c.object_key,
                )
                .join(
                    delivery_receipt_documents,
                    delivery_receipts.c.delivery_receipt_id
                    == delivery_receipt_documents.c.delivery_receipt_id,
                )
                .where(delivery_receipts.c.delivery_receipt_id == receipt_id)
                .with_for_update(of=delivery_receipt_documents)
            )
        )
        .mappings()
        .one()
    )
    body = _render_receipt_pdf(receipt["number"], dict(receipt["snapshot"]))
    await storage.ensure_bucket()
    await storage.put(
        body=body,
        content_type="application/pdf",
        object_key=receipt["object_key"],
    )
    await session.execute(
        update(delivery_receipt_documents)
        .where(delivery_receipt_documents.c.delivery_receipt_id == receipt_id)
        .values(
            status="ready",
            checksum_sha256=sha256(body).hexdigest(),
            size_bytes=len(body),
            rendered_at=func.now(),
            last_error=None,
        )
    )
    await session.execute(
        insert(outbox_handler_receipts).values(
            outbox_handler_receipt_id=_id("outbox-handler-receipt-document", outbox_event_id),
            outbox_event_id=outbox_event_id,
            handler_name=RECEIPT_HANDLER_NAME,
            result_id=receipt_id,
        )
    )
    return receipt_id
