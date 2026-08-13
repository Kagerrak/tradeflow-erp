# TradeFlow ERP — Finance Invoice Posting Release Notes

Release: finance invoice posting vertical  
Date: 2026-08-13  
Release branch: `feat/customer-ledger-invoice-posting`  
Release PR: #38 — https://github.com/Kagerrak/tradeflow-erp/pull/38  
Migrations: `bff3a637931a_customer_ledger_and_invoice_posting.py`

## Scope

This release adds the first Finance vertical slice: moving Draft Invoices that
originate from Delivery Confirmations into the immutable customer ledger. It
introduces the `customer_ledger_entries` table, branch-scoped invoice posting,
voiding, credit notes, read-only listing, and a web console page so Finance
operators can post invoices to the ledger.

## Integrated pull requests

| Issue | PR  | Title                                                   |
| ----- | --- | ------------------------------------------------------- |
| #35   | #38 | Customer ledger and invoice posting                     |

## Runtime environment

- Python: `>=3.13,<3.14` (managed by `uv`)
- Node.js: `>=22` (package manager `pnpm@9.7.0`)
- PostgreSQL: 17+ (see `infra/compose.yaml` and `infra/postgres/init/`)
- Object storage: S3-compatible (local MinIO in compose, configurable via env)
- Worker: `arq` Redis-backed async worker
- Mobile: Expo SDK 57 (iOS, Android, web)
- Web: Next.js 16 / Turbopack

## Verification evidence

All gates passed on `feat/customer-ledger-invoice-posting`:

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

## What changed

- Added `customer_ledger_entries` table with an immutable trigger and
  `credit_exposure_entries` deltas for invoice, void, and credit-note events.
- Added `finance:invoice-post`, `finance:invoice-read`,
  `finance:invoice-void`, and `finance:credit-note-post` capabilities.
- Added `POST /v1/finance/invoices/{draft_invoice_id}/post`,
  `POST /v1/finance/invoices/{draft_invoice_id}/void`,
  `POST /v1/finance/invoices/{draft_invoice_id}/credit-notes`,
  `GET /v1/finance/invoices`, and
  `GET /v1/finance/invoices/{draft_invoice_id}`.
- Invoice status is derived from `customer_ledger_entries`; `draft_invoices`
  remains immutable.
- Updated delivery-correction authorization guard to reject corrections once
  the source invoice has been posted.
- Regenerated `openapi/openapi.json` and `packages/api-client/src/schema.d.ts`.
- Added `/finance` web console page with BFF routes that list invoices and
  post draft invoices to the ledger.

## Known limitations / next slices

- Customer statement projection (#37) and payment allocation (#36) are not yet
  implemented.
- The web console uses the existing test-access-token pattern; production
  authentication will be wired when the identity layer is finalized.
