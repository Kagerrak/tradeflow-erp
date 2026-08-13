# Procurement workspace web console

**Issue:** #46  
**Ship date:** 2026-08-13  
**Branch:** `feat/procurement-workspace`

## What changed

- Added `GET /v1/procurement/purchase-orders/receipts` to list goods receipts
  across the actor’s branch and warehouse scope, ordered by creation time.
- Added BFF proxy route at `/api/procurement/goods-receipts` for the web
  console.
- Created a new `/procurement` landing page with links to Suppliers, Purchase
  orders, and Goods receipts.
- Added the Procurement workspace to the primary navigation rail.
- The workspace displays open approved purchase orders and recent goods
  receipts scoped to the actor’s branch.
- Regenerated the OpenAPI contract and TypeScript client to include the new
  list endpoint and schemas.

## Verification

- `pnpm format` — passed.
- `pnpm lint` — passed.
- `pnpm typecheck` — passed.
- `uv run pytest apps/api/tests apps/worker/tests --ignore=apps/api/tests/test_object_storage_contract.py` —
  **199 passed, 4 skipped**.
- `uv run pytest apps/api/tests/test_goods_receipt_contract.py` — **21 passed**.
- Playwright web suite — **134 passed, 10 skipped**.
- `pnpm build` / `uv build --all-packages` — passed.
- `git diff --check` — passed.

Note: `test_object_storage_contract.py` continues to fail locally with a MinIO
`RequestTimeTooSkewed` error; this is an environment/time-drift issue unrelated
to this vertical.

## Depends on

- #44 — Goods receipt posting against purchase orders (PR #49).
- #45 — Landed cost allocation to goods receipts (PR #50).

## Related PR

- PR #51
