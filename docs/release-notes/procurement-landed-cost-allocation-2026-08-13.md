# TradeFlow ERP — Procurement Landed Cost Allocation Release Notes

Release: landed cost allocation to goods receipts vertical  
Date: 2026-08-13  
Release branch: `feat/landed-cost-allocation`  
Release PR: #50 — https://github.com/Kagerrak/tradeflow-erp/pull/50  
Migrations: `d524a29c32b8_landed_cost_allocation_to_goods_receipts.py`

## Scope

This release adds the fourth Procurement vertical slice: allocating inbound
acquisition costs (freight, insurance, customs, brokerage, handling) to goods
receipt lines and updating moving-average inventory valuation. It builds on the
goods receipt posting branch (`feat/goods-receipt-posting`, #44 / #49) and
introduces the `landed_cost_charges` and `landed_cost_allocations` tables,
warehouse/location-scoped authorization, and a web console page for entering
charges and viewing receipt totals.

## Integrated pull requests

| Issue | PR  | Title                                    |
| ----- | --- | ---------------------------------------- |
| #45   | #50 | Landed cost allocation to goods receipts |

## Runtime environment

- Python: `>=3.13,<3.14` (managed by `uv`)
- Node.js: `>=22` (package manager `pnpm@9.7.0`)
- PostgreSQL: 17+ (see `infra/compose.yaml` and `infra/postgres/init/`)
- Object storage: S3-compatible (local MinIO in compose, configurable via env)
- Worker: `arq` Redis-backed async worker
- Mobile: Expo SDK 57 (iOS, Android, web)
- Web: Next.js 16 / Turbopack

## Verification evidence

All gates passed on `feat/landed-cost-allocation`:

- `uv run ruff check apps/api/src apps/api/tests apps/worker/src` — passed.
- `uv run ruff format --check .` — passed.
- `uv run mypy apps/api/src apps/worker/src` — passed.
- `pnpm format` — passed.
- `pnpm lint` — passed.
- `pnpm typecheck` — passed.
- `uv run pytest apps/api/tests apps/worker/tests --ignore=apps/api/tests/test_object_storage_contract.py` — passed.
  - Full Python pytest suite except object-storage contract: **194 passed, 4 skipped**.
  - `test_landed_cost_contract.py`: **6 passed**.
- Playwright web suite (`pnpm test` apps/web): **134 passed, 10 skipped**.
- `pnpm build` / `uv build --all-packages` — passed.
- `git diff --check` — passed.
- Alembic `downgrade -1` / `upgrade head` on the test database — passed.

Note: `test_object_storage_contract.py` failed locally with a MinIO
`RequestTimeTooSkewed` clock-drift error; this is an environment issue and not
related to the landed-cost vertical.

## What changed

- Added `landed_cost_charges` table with `landed_cost_charge_id`,
  `goods_receipt_id`, `charge_type`, `amount_base`, `base_currency`,
  `correlation_id`, `idempotency_key`, `created_by`, and `created_at`, plus
  charge-type and positive-amount checks.
- Added `landed_cost_allocations` table with `landed_cost_allocation_id`,
  `landed_cost_charge_id`, `goods_receipt_line_id`, and
  `allocated_amount_base`, plus a positive-amount check.
- Added `procurement:landed-cost-allocate` capability and
  `require_landed_cost_allocator` authorization guard.
- Added `POST /v1/procurement/goods-receipts/{goods_receipt_id}/landed-costs` to
  allocate charges proportionally by receipt line value:
  - Validates the actor's branch scope against the goods receipt's purchase
    order and warehouse scope against the receipt warehouse.
  - Rejects unknown charge types with `landed_cost_charge_type_invalid`.
  - Distributes each charge across receipt lines by line value; falls back to
    quantity-based distribution when all line values are zero.
  - Rounds the last line allocation to absorb remainder and ensure the sum of
    allocations equals the charge amount exactly.
  - Inserts charge and allocation rows and applies a zero-quantity valuation
    delta per SKU/warehouse, updating `inventory_valuation` and the moving-
    average unit cost.
- Added `GET /v1/procurement/goods-receipts/{goods_receipt_id}/landed-costs` to
  return existing charges, allocations, and receipt line totals including
  original receipt cost plus allocated landed cost.
- Regenerated `openapi/openapi.json` and `packages/api-client/src/schema.d.ts`.
- Added landed cost web console page:
  - `apps/web/app/procurement/goods-receipts/[goodsReceiptId]/landed-costs/page.tsx`
  - `apps/web/components/procurement-landed-cost-workspace.tsx`
  - `apps/web/app/api/procurement/goods-receipts/[goodsReceiptId]/landed-costs/route.ts`
- Added contract tests (`apps/api/tests/test_landed_cost_contract.py`) covering
  proportional allocation, capability gating, branch scope, invalid charge type
  rejection, GET totals, and moving-average valuation update.
- Updated `apps/api/tests/test_delivery_correction_migration.py` to reflect the
  new Alembic head after the landed cost migration.

## Rollback

- Alembic downgrade `d524a29c32b8` drops `landed_cost_allocations` and
  `landed_cost_charges` and removes their indexes.
- Rolling back after charges have been allocated will remove the allocation
  records but will not reverse the inventory valuation impact; a separate
  inventory correction slice will handle reversal workflows.

## Known limitations / next slices

- Landed cost reversal is not implemented; it is planned for a future inventory
  correction slice.
- The web console requires the goods receipt ID in the URL; future procurement
  workspace pages can link receipts to landed-cost entry.
- The procurement workspace umbrella page (#46) may consolidate purchase orders,
  goods receipts, landed costs, and suppliers.
