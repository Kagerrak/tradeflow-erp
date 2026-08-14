# TradeFlow workflow checkpoint

- Date: August 14, 2026
- Phase: Issue #107 part 1 ready for review — immutable warehouse stock transfers
  at source cost
- Branch: `feature/inventory-transfers`
- Base branch: `main`
- Release PR: (draft)

## Established

- Added migration `apps/api/migrations/versions/0018_inventory_transfers.py`:
  - Extended `ck_stock_movements_type` and `ck_stock_movements_leg` with
    `transfer` legs.
  - Created `inventory_transfers` table with immutable-history trigger and
    received-shape guard.
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
- Added contract tests (`apps/api/tests/test_inventory_transfer_contract.py`),
  database invariant tests
  (`apps/api/tests/test_inventory_transfer_database_invariants.py`), and
  migration tests (`apps/api/tests/test_inventory_transfer_migration.py`).
- Added Playwright test `apps/web/tests/inventory-transfers.spec.ts`.
- Added ADR `docs/adr/0019-immutable-inventory-transfers.md`, created
  `contexts/inventory/CONTEXT.md`, and release notes at
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
  — passed.
- `pnpm build` / `uv build --all-packages` — passed.
- `git diff --check` — passed.
- Alembic `downgrade 0017` / `upgrade head` round-trip on the migration test
  database — passed.

## Closed / ready for review

- Issue #107 part 1 — Immutable warehouse stock transfers at source cost.

## Shipped

- Not merged; this is a green draft PR awaiting review.

## Residual risks and follow-ups

- The web workspace does not yet retain the idempotency key for manual retries
  after a network failure.
- Counted-variance adjustments, serial-tracked SKU transfers, and transfer
  cancellation after release are intentionally out of scope for this slice.

## Next issue

- Continue first-release delivery from the next dependency-ready slice.
