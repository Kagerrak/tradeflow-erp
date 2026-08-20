# Inventory transfers (immutable, source-cost) and counted-variance adjustments

**Scope:** Warehouse-to-warehouse stock transfers at source cost, and counted-variance inventory adjustments.
**Date:** 2026-08-14 (updated 2026-08-21)
**Issue:** #107 (part 1 and part 2)
**Merged:** `main` at `baf1d48` via PR #114.

## What changed

- Added `inventory_transfers` table with immutable-history trigger and
  `released` → `received` transition guard.
- Extended `stock_movements` with `transfer` movement type and four transfer legs:
  `transfer_source_out`, `transfer_in_transit_in`, `transfer_in_transit_out`,
  `transfer_destination_in`.
- Transfer receipt now enforces maker-checker separation and an Approval
  Authority for `inventory:transfer-receive` scoped to the source warehouse with
  a `maximum_amount` covering the transfer value.
- Records the authorizing `approval_authority_id` in
  `inventory_transfer_authorizations`.
- New API capabilities:
  - `inventory:transfer-request`
  - `inventory:transfer-receive`
- New API endpoints under `/v1/inventory`:
  - `POST /v1/inventory/transfers`
  - `POST /v1/inventory/transfers/{transfer_id}/receive`
  - `GET /v1/inventory/transfers`
  - `GET /v1/inventory/transfers/{transfer_id}`
- ADR-0019 is accepted: the source Warehouse owns both quantity and value until
  destination receipt. Total Company inventory value is unchanged throughout the
  transfer lifecycle.
- Transfer In Transit uses dedicated custody, distinct from Delivery In Transit.
- Lot expiration is preserved on every movement leg and projection rebuild.
- Lot identity is carried; serial-tracked SKU transfers are rejected for this slice.
- Commands are idempotent via `Idempotency-Key` and command receipts.
- Replays revalidate current Warehouse scope and are explicitly identified;
  receipt rejects stale expected versions before posting.
- Concurrent transfers serialize through per-SKU/warehouse advisory locks and the
  projection-rebuild lock.
- The database boundary requires complete, balanced release and receipt movement
  groups before accepting the corresponding Transfer lifecycle state.
- Added migration `0022_inventory_adjustments.py` (merge of `0018` and `0021`):
  - Created `inventory_adjustments` and `inventory_adjustment_authorizations`
    tables with immutable-history trigger and status-shape guards.
  - Extended `stock_movements` with `inventory_adjustment` movement type and four
    adjustment legs: `adjustment_surplus_in`, `adjustment_shortage_out`,
    `adjustment_surplus_reversal_out`, `adjustment_shortage_reversal_in`.
- New API capabilities:
  - `inventory:adjustment-request`
  - `inventory:adjustment-approve`
- New API endpoints under `/v1/inventory`:
  - `POST /v1/inventory/adjustments`
  - `POST /v1/inventory/adjustments/{adjustment_id}/post`
  - `POST /v1/inventory/adjustments/{adjustment_id}/reverse`
  - `GET /v1/inventory/adjustments`
  - `GET /v1/inventory/adjustments/{adjustment_id}`
- Adjustment request, post, and reverse enforce maker-checker separation and
  Approval Authority value limits via `inventory:adjustment-approve` scoped to
  the target branch/warehouse.
- Adjustments update availability and valuation at the current moving-average
  unit cost; surplus increases value, shortage decreases value, and reversals
  restore the prior state.

## Web console

- New workspace: `/inventory/transfers`
- BFF routes:
  - `/api/inventory/transfers`
  - `/api/inventory/transfers/{transferId}`
  - `/api/inventory/transfers/{transferId}/receive`
- New workspace: `/inventory/adjustments`
- BFF routes:
  - `/api/inventory/adjustments`
  - `/api/inventory/adjustments/{adjustmentId}`
  - `/api/inventory/adjustments/{adjustmentId}/post`
  - `/api/inventory/adjustments/{adjustmentId}/reverse`
- Navigation: "Transfers" and "Adjustments" added to the inventory landing page.

## Verification

- `apps/api/tests/test_inventory_transfer_contract.py`
- `apps/api/tests/test_inventory_transfer_database_invariants.py`
- `apps/api/tests/test_inventory_transfer_migration.py`
- `apps/api/tests/test_inventory_adjustment_contract.py`
- `apps/api/tests/test_inventory_adjustment_migration.py`
- `apps/web/tests/inventory-transfers.spec.ts`
- `apps/web/tests/inventory-adjustments.spec.ts`

## Out of scope

- Serial-tracked SKU transfers.
- Transfer cancellation after release.

## Decisions applied

- ADR-0019 transfer valuation timing: source Warehouse owns quantity and value
  until destination receipt.
- ADR-0019 transfer authorization control: request and receipt must be performed
  by different actors; receipt requires an Approval Authority for
  `inventory:transfer-receive` with a sufficient `maximum_amount`.
- Counted-variance adjustments require an Approval Authority for
  `inventory:adjustment-approve`; request and post/reverse must be performed by
  different actors.

## Related documents

- `docs/adr/0019-immutable-inventory-transfers.md`
- `contexts/catalog-inventory/CONTEXT.md`
- `checkpoint/WORKFLOW_CHECKPOINT_2026-08-14-inventory-transfers.md`
- `docs/delivery/first-release-reconciliation.md`
