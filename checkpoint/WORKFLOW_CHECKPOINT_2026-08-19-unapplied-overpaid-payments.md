# TradeFlow workflow checkpoint

- Date: August 19, 2026
- Phase: Issue #72 ready for final review — unapplied and overpaid Payment
  Receipts
- Branch: `feature/unapplied-payments`
- Release PR: to be created after final independent review and full CI gate

## Established

- Added `apps/api/src/tradeflow_api/payment_balance.py` with explicit
  `not_cleared`, `unapplied`, `partially_applied`, and `fully_applied` states
  and balance helpers.
- Extended Payment Receipt responses to expose `allocated_amount`,
  `unapplied_amount`, `application_state`, `balance_version`, and
  `available_for_coverage` (`apps/api/src/tradeflow_api/payment_fulfillment.py`).
- Allocation command (`apps/api/src/tradeflow_api/payment_allocation.py`) now
  requires `expected_version`, revalidates branch scope on replay, returns a
  stale-version conflict, prevents receipt and invoice overallocation, and
  serializes concurrent allocation via advisory lock.
- Added `POST /v1/finance/payment-receipts/projections/rebuild` to rebuild
  scoped payment receipt projections from immutable events and allocations.
- Customer Statement (`apps/api/src/tradeflow_api/customer_statement.py`)
  derives unapplied funds as-of from immutable history and discloses them
  separately without altering the receivable closing balance.
- Invoice inquiry (`apps/api/src/tradeflow_api/invoice_posting.py`) exposes
  `open_balance` and an `open_only` filter for allocation selection.
- Refactored Payment Receipt list endpoint to apply status and application
  state filters at query level, support bounded pagination (`limit`/`offset`),
  and batch-fetch receipt details instead of N+1.
- Added/updated web components:
  - `apps/web/components/finance-allocation-workspace.tsx`
  - `apps/web/components/finance-statement-workspace.tsx`
  - `apps/web/components/payment-clearance-workspace.tsx`
- Added BFF routes:
  - `apps/web/app/api/finance/payment-receipts/route.ts`
  - `apps/web/app/api/finance/payment-receipts/[receiptId]/allocations/route.ts`
  - `apps/web/app/api/finance/invoices/route.ts`
- Added Playwright coverage in
  `apps/web/tests/finance-allocation.spec.ts` for desktop/mobile-web
  allocation, expected-version conflict, stable retry identity, retained
  excess, and statement disclosure.
- Updated mobile/package fixtures and assertions for the new receipt fields:
  - `packages/payment-clearance/src/index.test.ts`
  - `apps/mobile/components/payment-receipt-capture.test.tsx`
  - `apps/mobile/offline/payment-receipt-sync.test.ts`
- Updated `payment-clearance.spec.ts` mocks for the new fields.
- Regenerated OpenAPI contract (`openapi/openapi.json`) and TypeScript client
  (`packages/api-client/src/schema.d.ts`).
- Updated finance domain language in `contexts/finance/CONTEXT.md`.
- Added release notes at
  `docs/release-notes/finance-unapplied-overpaid-payments-2026-08-19.md`.

## Verification evidence

- `uv run pytest -q apps/api/tests/test_payment_allocation_contract.py
apps/api/tests/test_customer_statement_contract.py
apps/api/tests/test_payment_clearance_contract.py
apps/api/tests/test_invoice_posting_contract.py` — **38 passed**.
- `uv run pytest -q apps/api/tests` — **216 passed, 4 skipped**.
- `uv run pytest` (full Python gate incl. worker tests) — **217 passed, 4 skipped**.
- `pnpm test` (full Node + Python gate):
  - Package vitest/jest suites — passed.
  - Playwright web suite (chromium + mobile-web) — **146 passed, 10 skipped**.
  - Python pytest — **217 passed, 4 skipped**.
- `pnpm build` — passed (web static export, mobile export, Python wheel/sdist).
- `uv run ruff check .` — passed.
- `uv run ruff format --check .` — passed.
- `uv run mypy apps/api/src apps/worker/src` — passed.
- `pnpm lint` — passed.
- `pnpm format` — passed.
- `pnpm typecheck` — passed.
- `pnpm openapi:generate` — passed and deterministic.
- `git diff --check` — passed.

## Final review

- One independent standards/specification review was performed on
  2026-08-19. See the review summary in the PR body for findings and
  resolutions.

## Remaining gate

- Publish a reviewed green draft PR and obtain explicit merge approval.
- Do **not** merge without PR-specific approval.

## Deferred scope

- Refund commands remain explicitly out of scope. The `refunded` balance column
  and event type are reserved; the projection rebuild treats a `refunded` event
  as a full refund because no partial-refund command exists.
- Allocation reversal (#73), payment reversal (#74), and invoice aging (#75)
  remain dependency-ready future slices.

## Next issue

- #110 — Baseline current workflows and measure first-release success outcomes
  (Phase 0 discovery / scope lock).
