from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import and_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tradeflow_api.models import (
    delivery_confirmation_lines,
    delivery_confirmations,
    delivery_dispatches,
)

ZERO = Decimal("0")


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
        subtotal = source_subtotal - prior_subtotal
        discount = source_discount - prior_discount
        tax = source_tax - prior_tax
    else:
        subtotal = (source_subtotal * ratio).quantize(quantum, ROUND_HALF_UP)
        discount = (source_discount * ratio).quantize(quantum, ROUND_HALF_UP)
        tax = (source_tax * ratio).quantize(quantum, ROUND_HALF_UP)
    # Derive the total from the rounded accounting components. Historical callers
    # still supply source/prior totals so allocation replay retains its stable API,
    # but new immutable rows must never encode a second, conflicting rounding path.
    del source_total, prior_total
    return subtotal, discount, tax, subtotal - discount + tax


async def load_prior_confirmation_allocations(
    session: AsyncSession,
    *,
    sales_order_revision_id: UUID,
    line_id: UUID,
    current_confirmation_id: UUID | None,
    source_quantity: Decimal,
    source_subtotal: Decimal,
    source_discount: Decimal,
    source_tax: Decimal,
    source_total: Decimal,
    quantum: Decimal,
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
    """Rebuild rounded allocations in immutable confirmation order."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:allocation_key))"),
        {"allocation_key": f"cod-allocation:{sales_order_revision_id}"},
    )
    prior_query = (
        select(delivery_confirmation_lines.c.accepted_quantity_base)
        .select_from(
            delivery_confirmation_lines.join(
                delivery_confirmations,
                delivery_confirmation_lines.c.confirmation_id
                == delivery_confirmations.c.confirmation_id,
            ).join(
                delivery_dispatches,
                delivery_confirmations.c.delivery_id == delivery_dispatches.c.delivery_id,
            )
        )
        .where(
            delivery_dispatches.c.sales_order_revision_id == sales_order_revision_id,
            delivery_confirmation_lines.c.line_id == line_id,
        )
        .order_by(
            delivery_confirmations.c.confirmed_at,
            delivery_confirmations.c.confirmation_id,
        )
    )
    if current_confirmation_id is not None:
        current_confirmed_at = await session.scalar(
            select(delivery_confirmations.c.confirmed_at).where(
                delivery_confirmations.c.confirmation_id == current_confirmation_id
            )
        )
        if current_confirmed_at is None:
            raise ValueError("Current Delivery Confirmation does not exist.")
        prior_query = prior_query.where(
            or_(
                delivery_confirmations.c.confirmed_at < current_confirmed_at,
                and_(
                    delivery_confirmations.c.confirmed_at == current_confirmed_at,
                    delivery_confirmations.c.confirmation_id < current_confirmation_id,
                ),
            )
        )
    prior_quantity = ZERO
    prior_subtotal = ZERO
    prior_discount = ZERO
    prior_tax = ZERO
    prior_total = ZERO
    prior_rows = (await session.execute(prior_query)).scalars()
    for prior_accepted in prior_rows:
        allocation = allocate_partial_line_amounts(
            quantity=prior_accepted,
            source_quantity=source_quantity,
            source_subtotal=source_subtotal,
            source_discount=source_discount,
            source_tax=source_tax,
            source_total=source_total,
            prior_quantity=prior_quantity,
            prior_subtotal=prior_subtotal,
            prior_discount=prior_discount,
            prior_tax=prior_tax,
            prior_total=prior_total,
            quantum=quantum,
        )
        prior_quantity += prior_accepted
        prior_subtotal += allocation[0]
        prior_discount += allocation[1]
        prior_tax += allocation[2]
        prior_total += allocation[3]
    return prior_quantity, prior_subtotal, prior_discount, prior_tax, prior_total
