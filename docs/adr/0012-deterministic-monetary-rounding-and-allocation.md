# ADR-0012: Use deterministic monetary rounding and allocation

- Status: Accepted
- Date: 2026-07-28

## Context

Discount, tax, unit conversion, and partial delivery can produce fractional
currency amounts. Rounding only at the document total or allocating residuals
non-deterministically would make client totals disagree and partial invoices
fail to reconcile to their approved Sales Order.

## Decision

TradeFlow uses decimal arithmetic. Quantities, unit prices, discount rates, and
tax rates retain up to six decimal places. Posted Money Amounts use the
currency's minor unit with round-half-up. Discount, taxable basis, tax, and
total round per line; document totals sum rounded lines. Largest-remainder
allocation with stable line ordering distributes order-level amounts. Partial
deliveries allocate approved line amounts proportionally, and the final
delivery receives the residual. Calculation Snapshots retain inputs and
outputs.

## Consequences

Web, mobile, API, workers, reports, and generated documents must use the same
calculation contract. Tests must cover tie-breaking and prove that all partial
deliveries sum exactly to the approved order.
