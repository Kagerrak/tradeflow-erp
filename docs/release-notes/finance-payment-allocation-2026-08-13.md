# TradeFlow ERP — Finance Payment Allocation Release Notes

Release: finance payment allocation vertical  
Date: 2026-08-13  
Release branch: `feat/payment-allocation`  
Release PR: #39 — https://github.com/Kagerrak/tradeflow-erp/pull/39  
Migrations: `427e7443c910_payment_allocations_and_auto_allocation.py`

## Scope

This release adds the second Finance vertical slice: applying cleared customer
payments to posted invoices. It introduces the immutable `payment_allocations`
table, manual allocation by a Finance operator, and automatic allocation when
an invoice is posted against COD collections and prepayment coverage.

## Integrated pull requests

| Issue | PR  | Title                                      |
| ----- | --- | ------------------------------------------ |
| #36   | #39 | Payment allocation against posted invoices |

## Runtime environment

- Python: `>=3.13,<3.14` (managed by `uv`)
- Node.js: `>=22` (package manager `pnpm@9.7.0`)
- PostgreSQL: 17+ (see `infra/compose.yaml` and `infra/postgres/init/`)
- Object storage: S3-compatible (local MinIO in compose, configurable via env)
- Worker: `arq` Redis-backed async worker
- Mobile: Expo SDK 57 (iOS, Android, web)
- Web: Next.js 16 / Turbopack

## Verification evidence

All gates passed on `feat/payment-allocation`:

- `uv run ruff check apps/api/src apps/api/tests apps/worker/src` — passed.
- `uv run ruff format --check .` — passed.
- `uv run mypy apps/api/src apps/worker/src` — passed.
- `pnpm format` — passed.
- `pnpm lint` — passed.
- `pnpm typecheck` — passed.
- `pnpm test` — passed.
  - Full Python pytest suite: **141 passed, 4 skipped**.
  - Playwright web suite: **134 passed, 10 skipped**.
- `pnpm build` / `uv build --all-packages` — passed.
- `git diff --check` — passed.
- Alembic `upgrade head` / `downgrade -1` / `upgrade head` on a clean test
  database — passed.

## What changed

- Added `payment_allocations` table with an immutable trigger, foreign keys to
  `payment_receipts` and `draft_invoices`, positive-amount check, and indexes on
  receipt and invoice.
- Added `finance:payment-allocate` and `finance:payment-read` capabilities and
  branch-scoped authorization guards.
- Added `POST /v1/finance/payment-receipts/{payment_receipt_id}/allocations` for
  manual allocation with idempotency-key replay (`X-Idempotency-Replayed`
  header).
- Added `GET /v1/finance/payment-receipts/{payment_receipt_id}/allocations` to
  list applied allocations and remaining available balance.
- Added `auto_allocate_invoice` service invoked from invoice posting to apply
  cleared COD receipts linked to the delivery confirmation and to consume
  designated prepayment coverage for the linked fulfillment order.
- Allocations write `allocation` entries to the immutable
  `customer_ledger_entries` table, update `payment_receipt_balances`, and post
  `posted_open_balance` deltas to `credit_exposure_entries`, reducing customer
  open balance.
- Enforced allocation invariants: receipt must be `cleared`, amount cannot
  exceed receipt available balance or invoice open balance, receipt and invoice
  must share customer and branch, and only posted invoices can be allocated.
- Regenerated `openapi/openapi.json` and `packages/api-client/src/schema.d.ts`.
- Added `/finance/allocations` web console page:
  - `apps/web/app/finance/allocations/page.tsx`
  - `apps/web/components/finance-allocation-workspace.tsx`
  - `apps/web/app/api/finance/payment-receipts/route.ts`
  - `apps/web/app/api/finance/payment-receipts/[receiptId]/allocations/route.ts`
- Added Allocations navigation item in `apps/web/components/tradeflow-shell.tsx`
  and supporting CSS in `apps/web/app/finance/finance.css`.
- Added contract tests (`apps/api/tests/test_payment_allocation_contract.py`)
  covering manual full/partial allocation, idempotency, receipt over-allocation,
  invoice over-allocation, missing capability, out-of-scope branch, listing, and
  auto-allocation on invoice posting from COD receipts.

## Known limitations / next slices

- Customer statement projection (#37) is the remaining open Finance slice.
- The web console uses the existing test-access-token pattern; production
  authentication will be wired when the identity layer is finalized.
