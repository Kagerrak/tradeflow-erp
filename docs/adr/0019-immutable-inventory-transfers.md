# ADR-0019: Immutable warehouse stock transfers at source cost

- Status: Accepted
- Date: 2026-08-14

## Context

Issue #107 calls for moving stock between warehouses and posting controlled
inventory adjustments immutably. The codebase already models custody as
`warehouse_stock_locations` of kind `available` and `in_transit`, applies
availability and valuation through `inventory_projection_service`, and uses
advisory-lock ordering and command receipts for idempotent posting. Splitting
#107 keeps the transfer slice reviewable; counted-variance adjustments will
follow as a separate slice.

## Decision

Warehouse-to-warehouse transfers are immutable `inventory_transfers` records
backed by `stock_movements` with `movement_type='transfer'`.

A user with `inventory:transfer-request` may request a transfer only when the
actor has both the source and destination warehouses in `actor.warehouse_ids`.
The source location must hold enough unreserved on-hand quantity for the
requested SKU and, when lot tracking is required, the exact `lot_code`. Transfers
of serial-tracked SKUs are rejected for this slice.

Requesting a transfer records one `inventory_transfers` row with
`status='released'` and posts a movement group with two legs:
`transfer_source_out` from the source `available` location and
`transfer_in_transit_in` to the source warehouse's `in_transit` custody location.
Source valuation is reduced by `qty × source_moving_average_unit_cost`;
destination valuation is not yet touched because the goods are still in transit.

A user with `inventory:transfer-receive`, also scoped to both warehouses,
completes the transfer by posting a second movement group:
`transfer_in_transit_out` from source `in_transit` and
`transfer_destination_in` to the destination `available` location. Destination
valuation is increased by the source cost captured at release time. Total company
inventory value does not change; only custody and warehouse ownership move.

Both commands require an `Idempotency-Key` header. Replays return the stored
response. Concurrent transfers for the same SKU and warehouses serialize through
per-warehouse SKU advisory locks and the projection-rebuild lock.

The `inventory_transfers` table is protected by a trigger that rejects `UPDATE`
and `DELETE` except the allowed transition `released` → `received` where only the
receive fields change. The `stock_movements` table already rejects updates and
deletes.

## Consequences

Transfer history becomes immutable and auditable. Source cost is preserved
across warehouses, preventing valuation distortion. Scope checks ensure a clerk
can only move goods through warehouses they are authorized for. The UI can
request, list, and receive transfers without exposing mutable state. Downgrade
is blocked when transfer history exists, protecting the immutable record.

Out of scope for this slice: counted-variance adjustments, serial-tracked SKU
transfers, and transfer cancellation after release.
