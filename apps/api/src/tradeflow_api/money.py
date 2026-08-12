from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from uuid import UUID

ZERO_MINOR_UNIT_CURRENCIES = {
    "BIF",
    "CLP",
    "DJF",
    "GNF",
    "ISK",
    "JPY",
    "KMF",
    "KRW",
    "PYG",
    "RWF",
    "UGX",
    "VND",
    "VUV",
    "XAF",
    "XOF",
    "XPF",
}
THREE_MINOR_UNIT_CURRENCIES = {"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"}
FOUR_MINOR_UNIT_CURRENCIES = {"CLF", "UYW"}


def currency_quantum(currency: str) -> Decimal:
    if currency in ZERO_MINOR_UNIT_CURRENCIES:
        return Decimal("1")
    if currency in THREE_MINOR_UNIT_CURRENCIES:
        return Decimal("0.001")
    if currency in FOUR_MINOR_UNIT_CURRENCIES:
        return Decimal("0.0001")
    return Decimal("0.01")


@dataclass(frozen=True)
class ScaledInvoiceLineAmounts:
    subtotal: Decimal
    discount: Decimal
    tax: Decimal
    total: Decimal


def scale_invoice_line_amounts(
    *,
    source_quantity: Decimal,
    replacement_quantity: Decimal,
    source_subtotal: Decimal,
    source_discount: Decimal,
    source_tax: Decimal,
    quantum: Decimal,
) -> ScaledInvoiceLineAmounts:
    """Scale one immutable invoice line and keep its rounded accounting equation exact."""
    if source_quantity <= 0:
        raise ValueError("A positive source invoice quantity is required.")
    if replacement_quantity < 0:
        raise ValueError("Replacement invoice quantity cannot be negative.")
    ratio = replacement_quantity / source_quantity
    subtotal = (source_subtotal * ratio).quantize(quantum, ROUND_HALF_UP)
    discount = (source_discount * ratio).quantize(quantum, ROUND_HALF_UP)
    tax = (source_tax * ratio).quantize(quantum, ROUND_HALF_UP)
    return ScaledInvoiceLineAmounts(
        subtotal=subtotal,
        discount=discount,
        tax=tax,
        total=subtotal - discount + tax,
    )


def allocate_largest_remainder(
    *,
    amount: Decimal,
    weighted_lines: list[tuple[int, UUID, Decimal]],
    quantum: Decimal,
) -> dict[UUID, Decimal]:
    if amount == 0:
        return {line_id: Decimal("0").quantize(quantum) for _, line_id, _ in weighted_lines}
    total_weight = sum((weight for _, _, weight in weighted_lines), Decimal("0"))
    if total_weight <= 0:
        raise ValueError("A positive allocation weight is required.")

    amount_units = int((amount / quantum).to_integral_value())
    allocated_units: dict[UUID, int] = {}
    remainders: list[tuple[Decimal, int, str, UUID]] = []
    used_units = 0
    for position, line_id, weight in weighted_lines:
        raw_units = Decimal(amount_units) * weight / total_weight
        floor_units = int(raw_units.to_integral_value(rounding=ROUND_FLOOR))
        allocated_units[line_id] = floor_units
        used_units += floor_units
        remainders.append((raw_units - floor_units, position, str(line_id), line_id))

    remainders.sort(key=lambda item: (-item[0], item[1], item[2]))
    for _, _, _, line_id in remainders[: amount_units - used_units]:
        allocated_units[line_id] += 1

    return {
        line_id: (Decimal(units) * quantum).quantize(quantum)
        for line_id, units in allocated_units.items()
    }
