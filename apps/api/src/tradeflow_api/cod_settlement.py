from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tradeflow_api.money import currency_quantum
from tradeflow_api.settlement_allocation import (
    allocate_partial_line_amounts,
    load_prior_confirmation_allocations,
)

ZERO = Decimal("0")


async def calculate_cod_amount_due(
    session: AsyncSession,
    *,
    sales_order_revision_id: UUID,
    lines: Sequence[Mapping[str, Any]],
    accepted: Mapping[UUID, Decimal],
    currency: str,
    current_confirmation_id: UUID | None = None,
) -> Decimal:
    """Allocate accepted value from immutable pricing, including final residuals."""
    quantum = currency_quantum(currency)
    due = ZERO
    source_lines: dict[UUID, Mapping[str, Any]] = {}
    for line in lines:
        source_lines.setdefault(cast(UUID, line["line_id"]), line)
    for line_id, line in source_lines.items():
        prior = await load_prior_confirmation_allocations(
            session,
            sales_order_revision_id=sales_order_revision_id,
            line_id=line_id,
            current_confirmation_id=current_confirmation_id,
            source_quantity=cast(Decimal, line["source_quantity_base"]),
            source_subtotal=Decimal(line["source_calculation_snapshot"]["pre_discount_amount"]),
            source_discount=cast(Decimal, line["source_allocated_discount"]),
            source_tax=cast(Decimal, line["source_tax_amount"]),
            source_total=cast(Decimal, line["source_line_total"]),
            quantum=quantum,
        )
        _, _, _, line_total = allocate_partial_line_amounts(
            quantity=accepted[line_id],
            source_quantity=cast(Decimal, line["source_quantity_base"]),
            source_subtotal=Decimal(line["source_calculation_snapshot"]["pre_discount_amount"]),
            source_discount=cast(Decimal, line["source_allocated_discount"]),
            source_tax=cast(Decimal, line["source_tax_amount"]),
            source_total=cast(Decimal, line["source_line_total"]),
            prior_quantity=prior[0],
            prior_subtotal=prior[1],
            prior_discount=prior[2],
            prior_tax=prior[3],
            prior_total=prior[4],
            quantum=quantum,
        )
        due += line_total
    return due.quantize(quantum, ROUND_HALF_UP)
