# TradeFlow ERP — Finance Customer Statement Release Notes

Release: finance customer statement of account projection  
Date: 2026-08-13  
Release branch: `feat/customer-statement`  
Release PR: #40 — https://github.com/Kagerrak/tradeflow-erp/pull/40  
Depends on: #39 (payment allocation) — stacked PR  
Migrations: none (read-only projection over existing `customer_ledger_entries`)

## Scope

This release adds the third Finance vertical slice: a read-only Statement of
Account projection for any customer. It consumes the immutable
`customer_ledger_entries` produced by invoice posting, payment allocation,
credit notes, and voids, and returns opening/closing balances, ledger lines,
invoice document states, and aging buckets.

## Integrated pull requests

| Issue | PR  | Title                                    |
| ----- | --- | ---------------------------------------- |
| #37   | #40 | Customer statement of account projection |

## Runtime environment

- Python: `>=3.13,<3.14` (managed by `uv`)
- Node.js: `>=22` (package manager `pnpm@9.7.0`)
- PostgreSQL: 17+ (see `infra/compose.yaml` and `infra/postgres/init/`)
- Object storage: S3-compatible (local MinIO in compose, configurable via env)
- Worker: `arq` Redis-backed async worker
- Mobile: Expo SDK 57 (iOS, Android, web)
- Web: Next.js 16 / Turbopack

## Verification evidence

All gates passed on `feat/customer-statement`:

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

## What changed

- Added `GET /v1/finance/customers/{customer_id}/statement` returning:
  - opening and closing balances for a date range,
  - ordered ledger lines with running balances,
  - per-invoice document states (`paid`, `partially_paid`, `unpaid`,
    `overdue`, `credited`),
  - aging buckets (`current`, `1-30`, `31-60`, `61-90`, `90+`).
- Enforced `finance:statement-read` capability and branch scope; statements
  include only ledger entries from branches the actor can access.
- Deterministic ordering and aging based on `posted_at` (or `created_at` when
  `posted_at` is absent).
- Added projection-rebuild contract test proving that
  `customer_credit_exposure.open_balance` reconciles with the sum of immutable
  `customer_ledger_entries`.
- Regenerated `openapi/openapi.json` and `packages/api-client/src/schema.d.ts`.
- Added `/finance/statement` web console page:
  - `apps/web/app/finance/statement/page.tsx`
  - `apps/web/components/finance-statement-workspace.tsx`
  - `apps/web/app/api/finance/customers/[customerId]/statement/route.ts`
- Added Statement navigation item in `apps/web/components/tradeflow-shell.tsx`
  and supporting CSS state classes in `apps/web/app/finance/finance.css`.
- Added contract tests (`apps/api/tests/test_customer_statement_contract.py`)
  covering empty statements, posted invoices, partial/full allocations, overdue
  aging, branch scope, missing capability, and projection reconciliation.

## Rollback

- No schema migration was introduced.
- Rollback: revert the code change and redeploy; `customer_ledger_entries`
  remain the immutable source of truth.

## Known limitations / next slices

- The statement reads base-currency ledger entries only; multi-currency
  receivables remain out of scope per ADR-0004.
- Dunning/collections workflow and automated payment reminders are not
  implemented.
- The web console uses the existing test-access-token BFF pattern; production
  authentication integration is tracked separately.

## Next issue

- Procurement inbound receipts / landed cost (next dependency-ready vertical
  once product priority is confirmed).
