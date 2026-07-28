# ADR-0003: Use moving-average inventory valuation by warehouse

- Status: Accepted
- Date: 2026-07-28

## Context

TradeFlow needs reproducible inventory value across receipts, partial
deliveries, transfers, returns, corrections, and later landed-cost allocation.
FIFO layers provide different costing behavior but add allocation complexity
that is independent of physical lot and serial traceability.

## Decision

Inventory uses perpetual moving weighted-average valuation per SKU and
Warehouse in the Company Base Currency. Receipts update the average; outbound
movements snapshot it. Transfers carry source cost into the destination
average, and customer returns use the original delivery's unit cost.
Corrections create immutable value adjustments or reversals.

## Consequences

Physical lot or serial selection does not determine accounting cost. Concurrent
postings for the same SKU and Warehouse must serialize average-cost updates,
and projection rebuilds must reproduce quantity, value, and unit cost exactly.
