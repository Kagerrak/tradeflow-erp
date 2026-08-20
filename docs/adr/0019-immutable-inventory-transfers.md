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
`transfer_in_transit_in` to the source Warehouse's dedicated Transfer In Transit
Custody Location. Source valuation remains unchanged while the goods are in
transfer custody; the source Warehouse continues to own both quantity and value
until receipt.

A user with `inventory:transfer-receive`, also scoped to both warehouses,
completes the transfer by posting a second movement group:
`transfer_in_transit_out` from source Transfer In Transit and
`transfer_destination_in` to the destination `available` location. Destination
valuation is increased by the source cost captured at release time, while source
valuation is reduced by the same quantity and value. Total Company inventory
value never changes; receipt changes warehouse ownership while preserving cost.

### Authorization control

Receipt of a transfer requires a different actor from the one who requested it
(maker-checker). The receiver's subject must not equal `requested_by`.

In addition, receipt requires an `approval_authorities` row for capability
`inventory:transfer-receive` scoped to the source warehouse (branch plus
warehouse) with a `maximum_amount` that covers the transfer value
(`quantity_base * unit_cost`). The existing dual-Warehouse scope check remains:
the receiver must have both source and destination warehouses in
`actor.warehouse_ids`.

This brings transfers in line with credit notes and delivery corrections: a
capable actor with warehouse scope can still be denied if they created the
transfer, lack an approval authority, or exceed their authority limit.

Both commands require an `Idempotency-Key` header. Replays revalidate current
Warehouse scope and return the stored response with
`X-Idempotency-Replayed: true`. Receipt also requires the expected Transfer
version. Concurrent transfers for the same SKU and warehouses serialize through
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
