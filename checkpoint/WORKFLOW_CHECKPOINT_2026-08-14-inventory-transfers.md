# TradeFlow workflow checkpoint

- Date: August 14, 2026
- Phase: Issue #107 part 1 ready for review — immutable warehouse stock transfers
  at source cost
- Branch: `feature/inventory-transfers`
- Base branch: `main`
- Release PR: #114 (draft)

## Established

- Added migration `apps/api/migrations/versions/0018_inventory_transfers.py`:
  - Extended `ck_stock_movements_type` and `ck_stock_movements_leg` with
    `transfer` legs.
  - Created `inventory_transfers` table with immutable-history trigger and
    received-shape, positive-version, and monotonic-transition guards.
  - Blocked downgrade when transfer history or movement rows exist.
- Expanded `apps/api/src/tradeflow_api/models.py` with transfer constraints and
  the `inventory_transfers` table.
- Added capabilities `inventory:transfer-request` and `inventory:transfer-receive`
  in `apps/api/src/tradeflow_api/auth.py`.
- Implemented `apps/api/src/tradeflow_api/inventory_movements.py` with:
  - `POST /v1/inventory/transfers`
  - `POST /v1/inventory/transfers/{transfer_id}/receive`
  - `GET /v1/inventory/transfers`
  - `GET /v1/inventory/transfers/{transfer_id}`
- Registered the inventory movements router in
  `apps/api/src/tradeflow_api/app.py`.
- Regenerated `openapi/openapi.json` and `packages/api-client/src/schema.d.ts`.
- Added web transfer workspace:
  - `apps/web/app/inventory/transfers/page.tsx`
  - `apps/web/components/inventory-transfer-workspace.tsx`
  - `apps/web/app/api/inventory/transfers/route.ts`
  - `apps/web/app/api/inventory/transfers/[transferId]/route.ts`
  - `apps/web/app/api/inventory/transfers/[transferId]/receive/route.ts`
- Added **Transfers** navigation in `apps/web/components/tradeflow-shell.tsx` and
  the `/inventory` landing page.
- Retained request identities for unchanged transfer commands and receive
  identities per transfer until success, so response-loss retries cannot create
  duplicate stock or valuation effects.
- Revalidate current dual-Warehouse scope on command replay, return the replay
  response header, and require the expected Transfer version at receipt.
- Preserve Lot Identity and expiration on all four movement legs and during
  projection rebuild.
- Keep source Warehouse valuation intact while stock is in transfer custody;
  receipt moves source quantity and value to the destination at captured source
  cost without changing total Company value.
- Added contract tests (`apps/api/tests/test_inventory_transfer_contract.py`),
  database invariant tests
  (`apps/api/tests/test_inventory_transfer_database_invariants.py`), and
  migration tests (`apps/api/tests/test_inventory_transfer_migration.py`).
- Added Playwright test `apps/web/tests/inventory-transfers.spec.ts`.
- Added ADR `docs/adr/0019-immutable-inventory-transfers.md`, updated
  `contexts/catalog-inventory/CONTEXT.md`, and release notes at
  `docs/release-notes/inventory-transfers-2026-08-14.md`.

## Verification evidence

All gates passed on `feature/inventory-transfers`:

- `uv run ruff check apps/api/src apps/api/tests apps/worker/src` — passed.
- `uv run ruff format --check .` — passed.
- `uv run mypy apps/api/src apps/worker/src` — passed.
- `pnpm format` — passed.
- `pnpm lint` — passed.
- `pnpm typecheck` — passed.
- Targeted Python pytest suite:
  ```
  pytest apps/api/tests/test_inventory_transfer_contract.py \
         apps/api/tests/test_inventory_transfer_database_invariants.py \
         apps/api/tests/test_inventory_transfer_migration.py -v
  ```
  — **14 passed**.
- Playwright web suite for transfers:
  `pnpm --filter @tradeflow/web exec playwright test tests/inventory-transfers.spec.ts`
  — **8 passed** across desktop and mobile-web, including request and receive
  identity reuse after an ambiguous failure.
- `pnpm build` / `uv build --all-packages` — passed.
- Complete `pnpm test` gate — **227 Python passed / 4 skipped, 146 Playwright
  passed / 10 skipped, 78 native passed / 4 skipped**, plus all package tests.
- `git diff --check` — passed.
- Alembic `downgrade base` / `upgrade head` full migration cycle — passed.

## Final review

- One independent standards/specification review found two P1 stock/rebuild
  issues and two P2 contract/domain-document issues.
- Resolved all P1/P2 findings: Lot expiration and allocation history now
  rebuild, transfer valuation remains conserved in transit, replays revalidate
  scope and expose their header, receipt uses an expected version, and transfer
  language is consolidated in the mapped Catalog & Inventory context.
- Deferred one P3 BFF helper duplication finding; it does not affect transfer
  correctness or replacement risk.

## Closed / ready for review

- Issue #107 part 1 — Immutable warehouse stock transfers at source cost.

## Shipped

- Not merged; this is a green draft PR awaiting review.

## Residual risks and follow-ups

- Counted-variance adjustments, serial-tracked SKU transfers, and transfer
  cancellation after release are intentionally out of scope for this slice.

## Next issue

- Continue first-release delivery from the next dependency-ready slice.
