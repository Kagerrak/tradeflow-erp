from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any, Literal

PaymentApplicationState = Literal[
    "not_cleared",
    "unapplied",
    "partially_applied",
    "fully_applied",
]


def unapplied_payment_amount(balance: Mapping[str, Any]) -> Decimal:
    return (
        Decimal(balance["cleared_amount"])
        - Decimal(balance["reversed_amount"])
        - Decimal(balance["refunded_amount"])
        - Decimal(balance["allocated_amount"])
    )


def payment_application_state(balance: Mapping[str, Any]) -> PaymentApplicationState:
    if balance["state"] != "cleared":
        return "not_cleared"
    unapplied = unapplied_payment_amount(balance)
    allocated = Decimal(balance["allocated_amount"])
    if unapplied <= 0:
        return "fully_applied"
    if allocated > 0:
        return "partially_applied"
    return "unapplied"
