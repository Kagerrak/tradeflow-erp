# TradeFlow ERP — Procurement Purchase Order Creation and Approval Release Notes

Release: procurement purchase order creation and approval vertical  
Date: 2026-08-13  
Release branch: `feat/purchase-order-creation`  
Release PR: #48 — https://github.com/Kagerrak/tradeflow-erp/pull/48  
Migrations: `ccdf97c81a67_purchase_order_creation_and_approval.py`

## Scope

This release adds the second Procurement vertical slice: purchase order creation
and approval. It builds on the supplier directory branch (`feat/supplier-directory`,
#42 / #47) and introduces the `purchase_orders` and `purchase_order_lines`
tables, capability-based read/write/approve authorization, branch-scoped access,
and a web console page so Procurement operators can raise and approve purchase
orders against suppliers.

## Integrated pull requests

| Issue | PR  | Title                                |
| ----- | --- | ------------------------------------ |
| #43   | #48 | Purchase order creation and approval |

## Runtime environment

- Python: `>=3.13,<3.14` (managed by `uv`)
- Node.js: `>=22` (package manager `pnpm@9.7.0`)
- PostgreSQL: 17+ (see `infra/compose.yaml` and `infra/postgres/init/`)
- Object storage: S3-compatible (local MinIO in compose, configurable via env)
- Worker: `arq` Redis-backed async worker
- Mobile: Expo SDK 57 (iOS, Android, web)
- Web: Next.js 16 / Turbopack

## Verification evidence

All gates passed on `feat/purchase-order-creation`:

- `uv run ruff check apps/api/src apps/api/tests apps/worker/src` — passed.
- `uv run ruff format --check .` — passed.
- `uv run mypy apps/api/src apps/worker/src` — passed.
- `pnpm format` — passed.
- `pnpm lint` — passed.
- `pnpm typecheck` — passed.
- `pnpm test` — passed.
  - Full Python pytest suite: **165 passed, 4 skipped**.
  - Playwright web suite: **134 passed, 10 skipped**.
- `pnpm build` / `uv build --all-packages` — passed.
- `git diff --check` — passed.
- Alembic `downgrade -1` / `upgrade head` on the test database — passed.

## What changed

- Added `purchase_orders` table with `purchase_order_id`, `company_id`,
  `supplier_id`, `branch_id`, `code`, `currency`, `exchange_rate`, `status`,
  `version`, `created_by`, `created_at`, and `updated_at`, plus a unique
  constraint on `(company_id, code)`, status/exchange-rate/version checks, and
  indexes on `status` and `supplier_id`.
- Added `purchase_order_lines` table with `purchase_order_line_id`,
  `purchase_order_id`, `line_number`, `sku_id`, `requested_quantity`,
  `unit_code`, `base_quantity`, `unit_cost`, and `version`, plus line-number,
  quantity, and cost checks and an index on `sku_id`.
- `base_quantity` is computed from the active `unit_conversions` row at the time
  the purchase order is created, snapshotting the conversion factor for
  subsequent receipt and costing workflows.
- Added procurement authorization capabilities:
  `procurement:purchase-order-read`, `procurement:purchase-order-write`, and
  `procurement:purchase-order-approve`.
- Added `POST /v1/procurement/purchase-orders` to create a draft purchase order,
  rejecting duplicate codes with `purchase_order_code_duplicate` and missing unit
  conversions with `unit_conversion_missing`.
- Added `GET /v1/procurement/purchase-orders` to list and search purchase orders
  by code and status, with `limit`/`offset` pagination, scoped to the actor's
  branches.
- Added `GET /v1/procurement/purchase-orders/{purchase_order_id}` to fetch a
  single purchase order with its lines, enforcing branch scope.
- Added `POST /v1/procurement/purchase-orders/{purchase_order_id}/approve` to
  transition a draft purchase order to `approved`, enforcing the separate
  approver capability and branch scope.
- Regenerated `openapi/openapi.json` and `packages/api-client/src/schema.d.ts`.
- Added `/procurement/purchase-orders` web console page:
  - `apps/web/app/procurement/purchase-orders/page.tsx`
  - `apps/web/components/procurement-purchase-orders-workspace.tsx`
  - `apps/web/app/api/procurement/purchase-orders/route.ts`
  - `apps/web/app/api/procurement/purchase-orders/[purchaseOrderId]/route.ts`
  - `apps/web/app/api/procurement/purchase-orders/[purchaseOrderId]/approve/route.ts`
- Added Purchase orders navigation item in
  `apps/web/components/tradeflow-shell.tsx` and reused supporting CSS in
  `apps/web/app/procurement/procurement.css`.
- Added contract tests (`apps/api/tests/test_purchase_order_contract.py`)
  covering purchase order creation, duplicate-code rejection, write/approve
  capability gating, branch scope on create/list/fetch/approve, missing unit
  conversion rejection, approval transition, re-approval guard, and status
  filtering.
- Updated `apps/api/tests/test_delivery_correction_migration.py` to reflect the
  new Alembic head after the purchase order migration.

## Known limitations / next slices

- Purchase order lines are limited to a single line in the web console; the API
  supports multiple lines.
- Updates, partial receipts, goods receipts, and landed cost allocation are
  planned in subsequent Procurement issues (#44–#46).
- The web console uses the existing test-access-token pattern; production
  authentication will be wired when the identity layer is finalized.
