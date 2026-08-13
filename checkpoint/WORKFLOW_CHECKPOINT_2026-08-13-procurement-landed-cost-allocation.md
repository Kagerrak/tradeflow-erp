# TradeFlow workflow checkpoint

- Date: August 13, 2026
- Phase: Procurement landed cost allocation to goods receipts (#45)
- Branch: `feat/landed-cost-allocation` (stacked on `feat/goods-receipt-posting`)
- Session: continuation of order-to-delivery shipment

## What was built

- Migration `d524a29c32b8_landed_cost_allocation_to_goods_receipts.py`:
  - Created `landed_cost_charges` and `landed_cost_allocations` tables.
  - Added indexes for receipt, charge, and line lookups.
- `apps/api/src/tradeflow_api/models.py` updated for new tables and
  constraints.
- `apps/api/src/tradeflow_api/auth.py` added
  `require_landed_cost_allocator` for `procurement:landed-cost-allocate`.
- `apps/api/src/tradeflow_api/landed_costs.py`:
  - `POST /v1/procurement/goods-receipts/{goods_receipt_id}/landed-costs`
  - `GET /v1/procurement/goods-receipts/{goods_receipt_id}/landed-costs`
  - Validates branch/warehouse scope, charge types, and allocates charges
    proportionally by receipt line value.
  - Updates `inventory_valuation` moving-average cost via zero-quantity value
    delta.
- `apps/api/src/tradeflow_api/app.py` registered the landed cost router.
- `apps/api/tests/test_landed_cost_contract.py` added with 6 contract tests.
- `apps/api/tests/test_delivery_correction_migration.py` updated to expect the
  new Alembic head `d524a29c32b8`.
- OpenAPI contract and TypeScript client regenerated.
- Web console:
  - `apps/web/app/procurement/goods-receipts/[goodsReceiptId]/landed-costs/page.tsx`
  - `apps/web/components/procurement-landed-cost-workspace.tsx`
  - `apps/web/app/api/procurement/goods-receipts/[goodsReceiptId]/landed-costs/route.ts`
- Release notes:
  `docs/release-notes/procurement-landed-cost-allocation-2026-08-13.md`.

## Verification evidence

- `pnpm format` — passed.
- `pnpm lint` — passed.
- `pnpm typecheck` — passed.
- `uv run pytest apps/api/tests apps/worker/tests --ignore=apps/api/tests/test_object_storage_contract.py` —
  **194 passed, 4 skipped**.
- `test_landed_cost_contract.py` — **6 passed**.
- Playwright web suite — **134 passed, 10 skipped**.
- `pnpm build` / `uv build --all-packages` — passed.
- `git diff --check` — passed.

Note: `test_object_storage_contract.py` failed locally with a MinIO
`RequestTimeTooSkewed` error; this is an environment/time-drift issue unrelated
to the landed-cost vertical.

## Dependency status

- #44 / PR #49 is open and must merge before PR #50 can be retargeted to
  `main`.
- `feat/landed-cost-allocation` is based on `feat/goods-receipt-posting` so it
  can be reviewed as a stacked PR now and rebased after #49 merges.

## Next dependency-ready work

- #46 — Procurement workspace web console (umbrella page linking suppliers,
  purchase orders, goods receipts, and landed costs).

## Status

- #45 implementation is complete.
- PR #50 merged to `main` at 2026-08-13T11:09:50Z.
- Issue #45 closed automatically by the merge.
