# Unapplied and Overpaid Payment Receipts

**Date:** 2026-08-19  
**Issue:** [#72](https://github.com/Kagerrak/tradeflow-erp/issues/72)  
**Branch:** `feature/unapplied-payments`

## Summary
Payment Receipts now expose an explicit application state and unapplied balance.
Allocations consume only the current available cleared value; any excess remains
an explicit Unapplied Payment and is disclosed separately on the Customer
Statement. Finance users can allocate cleared receipts to open invoices through
the web workspace; mobile staff see the unapplied state and value after capture.

## What changed

### API / backend
- Added `payment_balance.py` helpers that derive `not_cleared`, `unapplied`,
  `partially_applied`, and `fully_applied` states from immutable receipt events.
- Payment Receipt responses now include `allocated_amount`, `unapplied_amount`,
  `application_state`, `balance_version`, and `available_for_coverage`.
- `POST /v1/finance/payment-receipts/{id}/allocations` requires
  `expected_version`, enforces the same-customer/same-branch rule, serializes
  concurrent allocations, and rejects overallocation of either the receipt or
  the invoice.
- Allocation commands are idempotent via `Idempotency-Key`; replays revalidate
  branch scope and return `X-Idempotency-Replayed`.
- Added `POST /v1/finance/payment-receipts/projections/rebuild` to rebuild
  receipt balance projections from immutable sources.
- Customer Statement derives unapplied funds as-of from immutable receipt and
  allocation history and discloses them separately without altering the
  receivable closing balance.
- Invoice inquiry exposes `open_balance` and an `open_only` filter.

### Web
- Added `/finance/allocations` workspace for selecting a cleared receipt,
  choosing an open invoice, entering an amount, and viewing retained excess.
- Statement workspace shows the Unapplied Payment total and per-receipt
  unapplied value/application state.
- Payment clearance workspace surfaces the application state after a receipt
  clears.

### Mobile / generated client
- `@tradeflow/payment-clearance` maps the new receipt fields.
- Mobile receipt capture displays the unapplied amount and application state for
  cleared receipts and warns that the value has not reduced an unrelated
  invoice.

### Tests
- Added contract tests for overpayment, version race, projection rebuild,
  replay after scope revocation, and statement disclosure.
- Added Playwright coverage for desktop and mobile-web allocation, expected
  version conflict, stable retry identity, and statement disclosure.
- Updated native/package fixtures and assertions for the new required fields.

## Out of scope
- Partial or full Refund commands are explicitly deferred; the `refunded`
  balance column and event type remain reserved for a future slice.
- General ledger / payroll integration.
- Adjacent Finance slices such as allocation reversal (#73), payment reversal
  (#74), and invoice aging (#75).

## Verification
- Focused API contract tests: 38 passed.
- Full API test suite: 216 passed, 4 skipped.
- Web Playwright focused tests (payment-clearance + finance-allocation): 7
  passed.
- Mobile package tests: 78 passed, 4 skipped.
- `payment-clearance` package tests: 3 passed.
- `pnpm typecheck`, `pnpm lint`, and `pnpm format` pass.
- OpenAPI schema and generated client regenerated.

## Deployment notes
- No new tables. The slice uses existing `payment_receipt_balances`,
  `payment_allocations`, `customer_ledger_entries`, and `payment_receipt_events`
  tables.
- Projection rebuild may be run idempotently for existing receipts after deploy.

## Decision evidence
- Finance context: `contexts/finance/CONTEXT.md` (Application State, Allocated
  Amount, Unapplied Payment, Balance Version).
- Checkpoint: `checkpoint/WORKFLOW_CHECKPOINT_2026-08-19-unapplied-overpaid-payments.md`.
