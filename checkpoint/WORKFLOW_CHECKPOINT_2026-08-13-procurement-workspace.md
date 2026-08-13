# TradeFlow workflow checkpoint

- Date: August 13, 2026
- Phase: Procurement workspace web console (#46)
- Branch: `feat/procurement-workspace` (stacked on `feat/landed-cost-allocation`)
- Session: continuation of order-to-delivery shipment

## What was built

- `apps/api/src/tradeflow_api/goods_receipts.py`:
  - Added `GET /v1/procurement/purchase-orders/receipts`.
  - Returns `GoodsReceiptSearchResponse` filtered by company, branch scope, and
    warehouse scope, with `limit`/`offset` pagination.
- `apps/api/tests/test_goods_receipt_contract.py`:
  - Added `TestListGoodsReceipts` with 5 contract tests covering listing,
    branch/warehouse scope filtering, pagination, and capability enforcement.
- Web console:
  - `apps/web/app/api/procurement/goods-receipts/route.ts` BFF proxy.
  - `apps/web/components/procurement-workspace.tsx`.
  - `apps/web/app/procurement/page.tsx`.
  - Added Procurement navigation item in `apps/web/components/tradeflow-shell.tsx`.
- Regenerated `openapi/openapi.json` and `packages/api-client/src/schema.d.ts`.
- Release notes:
  `docs/release-notes/procurement-workspace-web-console-2026-08-13.md`.

## Verification evidence

- `pnpm format` — passed.
- `pnpm lint` — passed.
- `pnpm typecheck` — passed.
- `uv run pytest apps/api/tests apps/worker/tests --ignore=apps/api/tests/test_object_storage_contract.py` —
  **199 passed, 4 skipped**.
- `uv run pytest apps/api/tests/test_goods_receipt_contract.py` — **21 passed**.
- Playwright web suite — **134 passed, 10 skipped**.
- `pnpm build` / `uv build --all-packages` — passed.
- `git diff --check` — passed.

Note: `test_object_storage_contract.py` failed locally with a MinIO
`RequestTimeTooSkewed` error; this is an environment/time-drift issue unrelated
to this vertical.

## Dependency status

- #44 / PR #49 and #45 / PR #50 must merge before this branch can be retargeted
  to `main`.
- `feat/procurement-workspace` is based on `feat/landed-cost-allocation` so it
  can be reviewed as a stacked PR now and rebased after #50 merges.

## Remaining dependency-ready work

- None in the current procurement chain.

## Status

- #46 implementation is complete on `feat/procurement-workspace`.
- PR #51 is ready to be opened (targeting `feat/landed-cost-allocation` until
  #50 merges); merge requires explicit user approval.
