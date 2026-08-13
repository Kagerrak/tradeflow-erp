# TradeFlow workflow checkpoint

- Date: August 13, 2026
- Phase: Issue #37 ready for merge — finance customer statement vertical
- Branch: `feat/customer-statement` (stacked on `feat/payment-allocation`)
- Release PR: #40 — https://github.com/Kagerrak/tradeflow-erp/pull/40

## Established

- Implemented read-only customer statement service
  (`apps/api/src/tradeflow_api/customer_statement.py`) with opening/closing
  balances, ledger lines, document states, and aging buckets.
- Registered the new router in `apps/api/src/tradeflow_api/app.py`.
- Enforced `finance:statement-read` capability and branch-scope filtering.
- Added projection-rebuild contract test reconciling
  `customer_credit_exposure.open_balance` with immutable ledger entries.
- Regenerated OpenAPI contract (`openapi/openapi.json`) and TypeScript client
  (`packages/api-client/src/schema.d.ts`).
- Added web console statement page:
  - `apps/web/app/finance/statement/page.tsx`
  - `apps/web/components/finance-statement-workspace.tsx`
  - `apps/web/app/api/finance/customers/[customerId]/statement/route.ts`
- Added Statement navigation item in `apps/web/components/tradeflow-shell.tsx`
  and supporting CSS in `apps/web/app/finance/finance.css`.
- Added contract tests (`apps/api/tests/test_customer_statement_contract.py`)
  covering empty statements, posted invoices, allocations, overdue aging,
  branch scope, missing capability, and projection reconciliation.
- Added release notes at
  `docs/release-notes/finance-customer-statement-2026-08-13.md`.

## Verification evidence

- `uv run ruff check apps/api/src apps/api/tests apps/worker/src` — passed.
- `uv run ruff format --check .` — passed.
- `uv run mypy apps/api/src apps/worker/src` — passed.
- `pnpm format` — passed.
- `pnpm lint` — passed.
- `pnpm typecheck` — passed.
- `pnpm test` — passed.
  - Full Python pytest suite: **149 passed, 4 skipped**.
  - Playwright web suite: **134 passed, 10 skipped**.
- `pnpm build` / `uv build --all-packages` — passed.
- `git diff --check` — passed.
- Alembic `upgrade head` / `downgrade -1` / `upgrade head` on a clean test
  database — passed.

## Closed / ready for review

- #37 — Customer statement of account projection (PR #40).

## Shipped

- PR #40 merged to `main` at 2026-08-13T05:44:57Z.
- Issue #37 closed automatically by the merge.

## Residual risks and follow-ups

- Procurement inbound receipts / landed cost is now the next dependency-ready
  vertical area.
- The web console reuses the test-access-token BFF pattern; production
  authentication integration is tracked separately.

## Next issue

- #44 — Goods receipt posting against purchase orders (next dependency-ready
  Procurement slice).
