# TradeFlow workflow checkpoint

- Date: August 13, 2026
- Phase: Procurement goods receipt posting against purchase orders (#44)
- Branch: `feat/goods-receipt-posting`
- Session: continuation of order-to-delivery shipment

## What was built

- Migration `d53dcaa7ede3_goods_receipt_posting_against_purchase_.py`:
  - Added `received_quantity_base` to `purchase_order_lines`.
  - Created `goods_receipts` and `goods_receipt_lines` tables.
  - Added `goods_receipt` movement type and `goods_receipt_in` leg to
    `stock_movements`.
- `apps/api/src/tradeflow_api/models.py` updated for new tables/columns and
  extended `stock_movements` constraints.
- `apps/api/src/tradeflow_api/auth.py` added
  `require_goods_receipt_poster` for `procurement:goods-receipt-post`.
- `apps/api/src/tradeflow_api/goods_receipts.py`:
  - `POST /v1/procurement/purchase-orders/{purchase_order_id}/receipts`
  - Validates branch/warehouse scope, PO status, open quantity, and tracking
    policy (untracked/lot/serial).
  - Posts `stock_movements`, updates `purchase_order_lines` and PO status, and
    applies availability/valuation projections.
- `apps/api/src/tradeflow_api/app.py` registered the goods receipt router.
- `apps/api/tests/test_goods_receipt_contract.py` added with 16 contract tests.
- `apps/api/tests/test_delivery_correction_migration.py` updated to expect the
  new Alembic head `d53dcaa7ede3`.
- OpenAPI contract and TypeScript client regenerated.
- Web console:
  - `apps/web/app/procurement/purchase-orders/[purchaseOrderId]/receipts/page.tsx`
  - `apps/web/components/procurement-goods-receipt-workspace.tsx`
  - `apps/web/app/api/procurement/purchase-orders/[purchaseOrderId]/receipts/route.ts`
  - Added **Receive** link from the purchase-order workspace for approved and
    partially-received orders.
- Release notes:
  `docs/release-notes/procurement-goods-receipt-posting-2026-08-13.md`.

## Verification evidence

- `pnpm format` — passed.
- `pnpm lint` — passed.
- `pnpm typecheck` — passed.
- `uv run pytest apps/api/tests apps/worker/tests --ignore=apps/api/tests/test_object_storage_contract.py` —
  **188 passed, 4 skipped**.
- `test_goods_receipt_contract.py` — **16 passed**.
- Playwright web suite — **134 passed, 10 skipped**.
- `pnpm build` / `uv build --all-packages` — passed.
- `git diff --check` — passed.

Note: `test_object_storage_contract.py` failed locally with a MinIO
`RequestTimeTooSkewed` error; this is an environment/time-drift issue unrelated
to the goods-receipt vertical.

## Next dependency-ready work

- #45 — Landed cost allocation to goods receipts (blocked by #44).
- #46 — Procurement workspace web console (may be covered by #44/#45 pages).

## Status

- #44 implementation is complete.
- PR #49 merged to `main` at 2026-08-13T11:00:00Z.
- Issue #44 closed automatically by the merge.
