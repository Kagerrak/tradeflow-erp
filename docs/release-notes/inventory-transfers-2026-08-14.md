# Inventory transfers (immutable, source-cost)

**Scope:** Warehouse-to-warehouse stock transfers at source cost.
**Date:** 2026-08-14
**Issue:** #107 (part 1 of 2; counted-variance adjustments deferred to next slice)

## What changed

- Added `inventory_transfers` table with immutable-history trigger and
  `released` → `received` transition guard.
- Extended `stock_movements` with `transfer` movement type and four transfer legs:
  `transfer_source_out`, `transfer_in_transit_in`, `transfer_in_transit_out`,
  `transfer_destination_in`.
- New API capabilities:
  - `inventory:transfer-request`
  - `inventory:transfer-receive`
- New API endpoints under `/v1/inventory`:
  - `POST /v1/inventory/transfers`
  - `POST /v1/inventory/transfers/{transfer_id}/receive`
  - `GET /v1/inventory/transfers`
  - `GET /v1/inventory/transfers/{transfer_id}`
- The proposed valuation policy preserves source cost: release retains quantity
  and value under the source Warehouse while custody is in transit; receipt
  reduces source valuation and increases destination valuation by the same
  amount. Total Company inventory value is unchanged throughout. This timing
  remains pending explicit business approval under ADR-0019.
- Transfer In Transit uses dedicated custody, distinct from Delivery In Transit.
- Lot expiration is preserved on every movement leg and projection rebuild.
- Lot identity is carried; serial-tracked SKU transfers are rejected for this slice.
- Commands are idempotent via `Idempotency-Key` and command receipts.
- Replays revalidate current Warehouse scope and are explicitly identified;
  receipt rejects stale expected versions before posting.
- The web workspace retains the same command identity when unchanged request or
  receive work is retried after an ambiguous network failure.
- Concurrent transfers serialize through per-SKU/warehouse advisory locks and the
  projection-rebuild lock.
- The database boundary requires complete, balanced release and receipt movement
  groups before accepting the corresponding Transfer lifecycle state.

## Web console

- New workspace: `/inventory/transfers`
- BFF routes:
  - `/api/inventory/transfers`
  - `/api/inventory/transfers/{transferId}`
  - `/api/inventory/transfers/{transferId}/receive`
- Navigation: "Transfers" added to primary rail and inventory landing page.

## Verification

- `apps/api/tests/test_inventory_transfer_contract.py`
- `apps/api/tests/test_inventory_transfer_database_invariants.py`
- `apps/api/tests/test_inventory_transfer_migration.py`
- `apps/web/tests/inventory-transfers.spec.ts`

## Out of scope

- Counted-variance inventory adjustments (next slice).
- Serial-tracked SKU transfers.
- Transfer cancellation after release.

## Decision required

- Approve or revise ADR-0019's proposed Warehouse valuation-ownership timing.
- Decide whether requester/receiver maker-checker separation is mandatory and
  whether transfer value limits or Approval Authorities apply.
- The slice must not merge while either material business policy remains pending.

## Related documents

- `docs/adr/0019-immutable-inventory-transfers.md`
- `contexts/catalog-inventory/CONTEXT.md`
- `checkpoint/WORKFLOW_CHECKPOINT_2026-08-14-inventory-transfers.md`
