# TradeFlow workflow checkpoint

- Date: August 13, 2026
- Phase: Issue #36 ready for merge — finance payment allocation vertical
- Branch: `feat/payment-allocation`
- Release PR: #39 — https://github.com/Kagerrak/tradeflow-erp/pull/39

## Established

- Added `payment_allocations` migration and model with immutable trigger
  (`apps/api/migrations/versions/427e7443c910_payment_allocations_and_auto_allocation.py`).
- Added finance authorization capabilities:
  `finance:payment-allocate` and `finance:payment-read`.
- Implemented payment allocation service
  (`apps/api/src/tradeflow_api/payment_allocation.py`) with manual allocate,
  list, and auto-allocate-on-post endpoints.
- Registered the new router in `apps/api/src/tradeflow_api/app.py` and wired
  `auto_allocate_invoice` into `apps/api/src/tradeflow_api/invoice_posting.py`.
- Allocations write immutable `customer_ledger_entries` of type `allocation`,
  update `payment_receipt_balances`, and post `posted_open_balance` deltas to
  `credit_exposure_entries`.
- Auto-allocation applies cleared COD receipts linked to the delivery
  confirmation and consumes designated prepayment coverage for the linked
  fulfillment order.
- Added contract tests (`apps/api/tests/test_payment_allocation_contract.py`)
  covering manual allocation, idempotency, receipt/invoice over-allocation,
  authorization, branch scope, listing, and COD auto-allocation on post.
- Updated existing delivery-correction migration tests for the new head
  revision.
- Regenerated OpenAPI contract (`openapi/openapi.json`) and TypeScript client
  (`packages/api-client/src/schema.d.ts`).
- Added web console finance allocations page:
  - `apps/web/app/finance/allocations/page.tsx`
  - `apps/web/components/finance-allocation-workspace.tsx`
  - `apps/web/app/api/finance/payment-receipts/route.ts`
  - `apps/web/app/api/finance/payment-receipts/[receiptId]/allocations/route.ts`
- Added Allocations navigation item in `apps/web/components/tradeflow-shell.tsx`
  and supporting CSS in `apps/web/app/finance/finance.css`.
- Added release notes at
  `docs/release-notes/finance-payment-allocation-2026-08-13.md`.

## Verification evidence

- `uv run ruff check apps/api/src apps/api/tests apps/worker/src` — passed.
- `uv run ruff format --check .` — passed.
- `uv run mypy apps/api/src apps/worker/src` — passed.
- `pnpm format` — passed (fixed Prettier issue in
  `docs/release-notes/finance-invoice-posting-2026-08-13.md`).
- `pnpm lint` — passed.
- `pnpm typecheck` — passed.
- `pnpm test` — passed.
  - Full Python pytest suite: **149 passed, 4 skipped**.
  - Playwright web suite: **134 passed, 10 skipped**.
- `pnpm build` / `uv build --all-packages` — passed.
- `git diff --check` — passed.
- Alembic `upgrade head` / `downgrade -1` / `upgrade head` on a clean test
  database — passed.
- GitHub Actions CI `verify` for PR #39 — passed
  (run 31669045701, https://github.com/Kagerrak/tradeflow-erp/actions/runs/31669045701).

## Closed / ready for review

- #36 — Payment allocation against posted invoices (PR #39).

## Shipped

- None yet — awaiting explicit approval to merge PR #39.

## Residual risks and follow-ups

- Customer statement projection (#37) remains the next open Finance slice.
- The web console reuses the test-access-token BFF pattern; production
  authentication integration is tracked separately.

## Next issue

- #37 — Customer statement of account projection.
