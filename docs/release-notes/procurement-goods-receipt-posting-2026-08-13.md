# TradeFlow ERP — Procurement Goods Receipt Posting Release Notes

Release: procurement goods receipt posting against purchase orders vertical  
Date: 2026-08-13  
Release branch: `feat/goods-receipt-posting`  
Release PR: #49 — https://github.com/Kagerrak/tradeflow-erp/pull/49  
Migrations: `d53dcaa7ede3_goods_receipt_posting_against_purchase_.py`

## Scope

This release adds the third Procurement vertical slice: posting goods receipts
against approved purchase orders. It builds on the purchase order creation
branch (`feat/purchase-order-creation`, #43 / #48) and introduces the
`goods_receipts` and `goods_receipt_lines` tables, warehouse/location-scoped
receipt posting, tracked-SKU lot/serial validation, immutable `stock_movements`
entries for `goods_receipt` / `goods_receipt_in`, and updates to inventory
availability and moving-average valuation projections. A web console page lets
Procurement/Warehouse operators receive inventory against an approved purchase
order.

## Integrated pull requests

| Issue | PR  | Title                                         |
| ----- | --- | --------------------------------------------- |
| #44   | #49 | Goods receipt posting against purchase orders |

## Runtime environment

- Python: `>=3.13,<3.14` (managed by `uv`)
- Node.js: `>=22` (package manager `pnpm@9.7.0`)
- PostgreSQL: 17+ (see `infra/compose.yaml` and `infra/postgres/init/`)
- Object storage: S3-compatible (local MinIO in compose, configurable via env)
- Worker: `arq` Redis-backed async worker
- Mobile: Expo SDK 57 (iOS, Android, web)
- Web: Next.js 16 / Turbopack

## Verification evidence

All gates passed on `feat/goods-receipt-posting`:

- `uv run ruff check apps/api/src apps/api/tests apps/worker/src` — passed.
- `uv run ruff format --check .` — passed.
- `uv run mypy apps/api/src apps/worker/src` — passed.
- `pnpm format` — passed.
- `pnpm lint` — passed.
- `pnpm typecheck` — passed.
- `uv run pytest apps/api/tests apps/worker/tests --ignore=apps/api/tests/test_object_storage_contract.py` — passed.
  - Full Python pytest suite except object-storage contract: **188 passed, 4 skipped**.
  - `test_goods_receipt_contract.py`: **16 passed**.
- Playwright web suite (`pnpm test` apps/web): **134 passed, 10 skipped**.
- `pnpm build` / `uv build --all-packages` — passed.
- `git diff --check` — passed.
- Alembic `downgrade -1` / `upgrade head` on the test database — passed.

Note: `test_object_storage_contract.py` failed locally with a MinIO
`RequestTimeTooSkewed` clock-drift error; this is an environment issue and not
related to the goods-receipt changes.

## What changed

- Added `received_quantity_base` column to `purchase_order_lines` with a
  nonnegative check and server default of `0`.
- Added `goods_receipts` table with `goods_receipt_id`, `purchase_order_id`,
  `warehouse_id`, `location_id`, `receipt_number`, `status`, `correlation_id`,
  `idempotency_key`, `created_by`, `created_at`, and `updated_at`, plus status
  check and unique constraint on `(purchase_order_id, receipt_number)`.
- Added `goods_receipt_lines` table with `goods_receipt_line_id`,
  `goods_receipt_id`, `purchase_order_line_id`, `received_quantity_base`,
  `lot_code`, and `serial_numbers`, plus a positive-quantity check.
- Extended `stock_movements` check constraints to accept `movement_type =
'goods_receipt'` and `movement_leg = 'goods_receipt_in'`.
- Added `procurement:goods-receipt-post` capability and
  `require_goods_receipt_poster` authorization guard.
- Added `POST /v1/procurement/purchase-orders/{purchase_order_id}/receipts` to
  post a goods receipt:
  - Validates the actor's branch scope against the purchase order and warehouse
    scope against the receipt warehouse.
  - Requires the purchase order status to be `approved` or `partially_received`.
  - Rejects receipts that exceed the open line quantity with
    `goods_receipt_over_receipt`.
  - Enforces SKU tracking policy: untracked SKUs reject lot/serial identities,
    lot-tracked SKUs require a `lot_code`, and serial-tracked SKUs require one
    unique serial number per received unit.
  - Records a `goods_receipt` / `goods_receipt_in` `stock_movements` row per
    line with source reference and idempotency key.
  - Increments `purchase_order_lines.received_quantity_base` and transitions
    the purchase order status to `partially_received` or `received`.
  - Updates `inventory_availability` and `inventory_valuation` projections,
    computing moving-average unit cost.
- Regenerated `openapi/openapi.json` and `packages/api-client/src/schema.d.ts`.
- Added goods receipt web console page:
  - `apps/web/app/procurement/purchase-orders/[purchaseOrderId]/receipts/page.tsx`
  - `apps/web/components/procurement-goods-receipt-workspace.tsx`
  - `apps/web/app/api/procurement/purchase-orders/[purchaseOrderId]/receipts/route.ts`
- Added a **Receive** link from the purchase-order workspace for approved and
  partially-received orders.
- Added contract tests (`apps/api/tests/test_goods_receipt_contract.py`)
  covering happy-path full receipt, partial receipt, over-receipt rejection,
  draft-order rejection, capability gating, branch scope, warehouse scope,
  duplicate receipt-number rejection, untracked/lot/serial tracking rules, and
  unknown purchase-order/location rejection.
- Updated `apps/api/tests/test_delivery_correction_migration.py` to reflect the
  new Alembic head after the goods receipt migration.

## Known limitations / next slices

- The web console receipt form supports one line at a time and requires manual
  warehouse/location IDs; future slices can add location lookup and multi-line
  batch entry.
- Goods receipt reversal is not implemented; it is planned for a future
  inventory correction slice.
- Landed cost allocation to goods receipts is tracked in #45.
- The procurement workspace umbrella page (#46) may consolidate purchase orders,
  goods receipts, and suppliers.
