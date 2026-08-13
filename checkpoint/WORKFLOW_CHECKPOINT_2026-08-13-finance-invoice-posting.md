# TradeFlow workflow checkpoint

- Date: August 13, 2026
- Phase: Issue #35 merged — finance invoice posting vertical shipped
- Branch: `feat/customer-ledger-invoice-posting` (merged to `main`)
- Commit: `a9bd18a`
- Release PR: #38 — https://github.com/Kagerrak/tradeflow-erp/pull/38

## Established

- Added `customer_ledger_entries` migration and model with immutable trigger
  (`apps/api/migrations/versions/bff3a637931a_customer_ledger_and_invoice_posting.py`).
- Added finance authorization capabilities:
  `finance:invoice-post`, `finance:invoice-read`, `finance:invoice-void`,
  `finance:credit-note-post`, `finance:payment-allocate`,
  `finance:statement-read`.
- Implemented invoice posting service
  (`apps/api/src/tradeflow_api/invoice_posting.py`) with post, void, credit
  note, list, and get endpoints.
- Registered the new router in `apps/api/src/tradeflow_api/app.py`.
- Derived invoice status from `customer_ledger_entries`; left `draft_invoices`
  immutable and updated the delivery-correction guard to use the ledger.
- Added contract tests (`apps/api/tests/test_invoice_posting_contract.py`)
  covering post, idempotency, authorization, branch scope, repost rejection,
  void, credit note, and listing.
- Updated existing delivery-correction tests for the new ledger-based posted
  guard and the new head migration revision.
- Regenerated OpenAPI contract (`openapi/openapi.json`) and TypeScript client
  (`packages/api-client/src/schema.d.ts`).
- Added web console finance page:
  - `apps/web/app/finance/page.tsx`
  - `apps/web/app/finance/finance.css`
  - `apps/web/components/finance-invoice-workspace.tsx`
  - `apps/web/app/api/finance/invoices/route.ts`
  - `apps/web/app/api/finance/invoices/[invoiceId]/post/route.ts`
- Added Finance navigation item in `apps/web/components/tradeflow-shell.tsx`.
- Added release notes at
  `docs/release-notes/finance-invoice-posting-2026-08-13.md`.

## Verification evidence

- `uv run ruff check apps/api/src apps/api/tests apps/worker/src` — passed.
- `uv run ruff format --check .` — passed.
- `uv run mypy apps/api/src apps/worker/src` — passed.
- `pnpm format` — passed.
- `pnpm lint` — passed.
- `pnpm typecheck` — passed.
- `pnpm test` — passed.
  - Full Python pytest suite: **133 passed, 4 skipped**.
  - Playwright web suite: **134 passed, 10 skipped**.
- `pnpm build` / `uv build --all-packages` — passed.
- `git diff --check` — passed.
- Alembic `upgrade head` / `downgrade base` / `upgrade head` on a clean test
  database — passed.

## Closed / ready for review

- #35 — Customer ledger and invoice posting (PR #38).

## Shipped

- #35 — Customer ledger and invoice posting (PR #38).

## Residual risks and follow-ups

- Customer statement projection (#37) and payment allocation (#36) remain open
  and are the next dependency-ready finance slices.
- The web console reuses the test-access-token BFF pattern; production
  authentication integration is tracked separately.

## Next issue

- #36 — Payment allocation against posted invoices, depending on product
  priority relative to #37 (Customer statement of account projection).
