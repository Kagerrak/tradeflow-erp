# TradeFlow workflow checkpoint

- Date: August 21, 2026
- Phase: Issue #107 complete — immutable warehouse transfers at source cost and
  counted-variance inventory adjustments, with ADR-0019 policies accepted
- Branch: `feature/inventory-transfers` (merged to `main`)
- Base branch: `main` at `baf1d48`
- Release PR: #114 (merged; explicit approval recorded by repo owner in
  https://github.com/Kagerrak/tradeflow-erp/pull/114#issuecomment-5359718827
  because GitHub self-approval is blocked for the sole collaborator)

## Established

- Accepted ADR-0019:
  - Source Warehouse owns quantity and value until destination receipt.
  - Transfer receipt requires a different actor than the requester and an
    `approval_authorities` row for `inventory:transfer-receive` scoped to the
    source warehouse with a sufficient `maximum_amount`.
- Added migration `apps/api/migrations/versions/0022_inventory_adjustments.py`:
  - Merge migration over `0018_inventory_transfers.py` and
    `0021_notification_immutability.py`.
  - Created `inventory_transfer_authorizations` to record transfer receipt
    authority without widening the immutable transfer header.
  - Created `inventory_adjustments` and `inventory_adjustment_authorizations`
    tables with immutable-history trigger and status-shape guards.
  - Extended `ck_stock_movements_type` and `ck_stock_movements_leg` with
    `inventory_adjustment` legs.
- Expanded `apps/api/src/tradeflow_api/models.py` with the new tables and
  movement constraints.
- Added capabilities in `apps/api/src/tradeflow_api/auth.py`:
  - `inventory:adjustment-request`
  - `inventory:adjustment-approve`
- Implemented `apps/api/src/tradeflow_api/inventory_adjustments.py` with:
  - `POST /v1/inventory/adjustments`
  - `POST /v1/inventory/adjustments/{adjustment_id}/post`
  - `POST /v1/inventory/adjustments/{adjustment_id}/reverse`
  - `GET /v1/inventory/adjustments`
  - `GET /v1/inventory/adjustments/{adjustment_id}`
- Updated `apps/api/src/tradeflow_api/inventory_movements.py` to enforce
  maker-checker and Approval Authority checks at transfer receipt and to record
  `inventory_transfer_authorizations`.
- Updated `apps/api/src/tradeflow_api/catalog_inventory.py::rebuild_projections`
  to include adjustment inbound legs.
- Regenerated `openapi/openapi.json` and `packages/api-client/src/schema.d.ts`.
- Added web adjustment workspace:
  - `apps/web/app/inventory/adjustments/page.tsx`
  - `apps/web/components/inventory-adjustment-workspace.tsx`
  - `apps/web/app/api/inventory/adjustments/route.ts`
  - `apps/web/app/api/inventory/adjustments/[adjustmentId]/route.ts`
  - `apps/web/app/api/inventory/adjustments/[adjustmentId]/post/route.ts`
  - `apps/web/app/api/inventory/adjustments/[adjustmentId]/reverse/route.ts`
- Added **Adjustments** navigation to the `/inventory` landing page.
- Added contract tests (`apps/api/tests/test_inventory_adjustment_contract.py`)
  and migration tests
  (`apps/api/tests/test_inventory_adjustment_migration.py`).
- Added Playwright test `apps/web/tests/inventory-adjustments.spec.ts`.
- Updated existing transfer contract and database invariant tests to account for
  the new receipt authorization rules.
- Updated `docs/adr/0019-immutable-inventory-transfers.md`,
  `docs/release-notes/inventory-transfers-2026-08-14.md`, and
  `docs/delivery/first-release-reconciliation.md`.

## Current-head verification

- Full Python API pytest suite — **350 passed, 4 skipped**.
- Worker pytest suite — **1 passed**.
- Mobile native tests — **78 passed, 4 skipped**.
- Package tests — all passed.
- Playwright web suite — all passed, including the new adjustment specs across
  desktop and mobile-web.
- `uv run ruff check apps/api/src apps/api/tests apps/worker/src` — passed.
- `uv run ruff format --check .` — passed.
- `uv run mypy apps/api/src apps/worker/src` — passed.
- `pnpm format` — passed.
- `pnpm lint` — passed.
- `pnpm typecheck` — passed.
- `pnpm build` / `uv build --all-packages` — passed.
- `pnpm openapi:generate` — passed with no drift.
- Alembic `downgrade base` / `upgrade head` full migration cycle — passed.
- `git diff --check` — passed.

## Implemented / pending qualification

- Issue #107 part 1 — Immutable warehouse stock transfers at source cost.
- Issue #107 part 2 — Counted-variance inventory adjustments.

## Shipped

- Merged to `main` via PR #114 at `baf1d48`.
- Explicit approval recorded by the repo owner in PR comment
  https://github.com/Kagerrak/tradeflow-erp/pull/114#issuecomment-5359718827
  (GitHub self-approval is blocked for the sole collaborator).
- GitHub issue #107 closed with evidence from verify run 32394991241 and the
  local release gate.

## Residual risks and follow-ups

- Serial-tracked SKU transfers and transfer cancellation after release remain
  out of scope.
- Customer returns/damage lifecycle remains pending separate issues (#56,
  #65–#70).

## Next issue

- Continue first-release delivery from the next dependency-ready slice.
- Update `docs/delivery/first-release-reconciliation.md` and release notes to
  reflect the merged state.
