# TradeFlow ERP — Immutable Credit Notes Release Notes

Release: immutable credit notes with maker-checker control
Date: 2026-08-14
Release branch: `feature/return-authorization`
Release PR: #71
Migrations: `0017_credit_note_documents.py`

## Scope

This release replaces the bare `POST /v1/finance/invoices/{id}/credit-notes`
shortcut with a controlled, immutable credit-note document lifecycle:
request → authorize/post → reverse. It introduces the `credit_notes` and
`credit_note_authorizations` tables, branch-scoped document numbering, and
database-enforced maker-checker separation.

## Integrated pull requests

| Issue | PR  | Title                            |
| ----- | --- | -------------------------------- |
| #71   | #71 | Immutable credit notes (this PR) |

## Runtime environment

- Python: `>=3.13,<3.14` (managed by `uv`)
- Node.js: `>=22` (package manager `pnpm@9.7.0`)
- PostgreSQL: 17+ (see `infra/compose.yaml` and `infra/postgres/init/`)
- Object storage: S3-compatible (local MinIO in compose, configurable via env)
- Worker: `arq` Redis-backed async worker
- Mobile: Expo SDK 57 (iOS, Android, web)
- Web: Next.js 16 / Turbopack

## Verification evidence

All gates passed on `feature/return-authorization`:

- `uv run ruff check apps/api/src apps/api/tests apps/worker/src` — passed.
- `uv run ruff format --check .` — passed.
- `uv run mypy apps/api/src apps/worker/src` — passed.
- `pnpm format` — passed.
- `pnpm lint` — passed.
- `pnpm typecheck` — passed.
- Targeted Python pytest suite:
  ```
  pytest apps/api/tests/test_credit_note_contract.py \
         apps/api/tests/test_credit_note_database_invariants.py \
         apps/api/tests/test_credit_note_migration.py \
         apps/api/tests/test_invoice_posting_contract.py -v
  ```
  — **21 passed**.
- Playwright web suite for credit notes:
  `pnpm --filter @tradeflow/web exec playwright test tests/finance-credit-notes.spec.ts`
  — **4 passed**.
- `pnpm build` / `uv build --all-packages` — passed.
- `git diff --check` — passed.
- Alembic `downgrade d53dcaa7ede3` / `upgrade head` round-trip on the migration
  test database — passed.

## What changed

- Added `credit_notes` table with `credit_note_id`, `draft_invoice_id`,
  `customer_id`, `branch_id`, `document_series_id`, `series_number`, `number`,
  `amount`, `currency`, `reason`, `requested_by`, `requested_at`, `posted_by`,
  `posted_at`, `ledger_entry_id`, `reversed_by`, `reversed_at`,
  `reversal_reason`, `reversal_ledger_entry_id`, `status`, `correlation_id`,
  and `idempotency_key`, plus status-shape, amount, reason, and actor-key
  constraints.
- Added `credit_note_authorizations` table with `credit_note_id`,
  `authorized_by`, `approval_authority_id`, `idempotency_key`, `correlation_id`,
  and `authorized_at`, plus immutable-history and append-guard triggers.
- Expanded `document_series` to support `document_type = 'credit_note'` and
  added `credit_note_id` to `document_series_number_audit`.
- Expanded `customer_ledger_entries.source_type` to include
  `'credit_note_reversal'`.
- Added `finance:credit-note-request`, `finance:credit-note-approve`, and
  `finance:credit-note-read` capabilities with dedicated auth guards.
- Added `apps/api/src/tradeflow_api/credit_notes.py` with endpoints:
  - `POST /v1/finance/invoices/{draft_invoice_id}/credit-notes`
  - `POST /v1/finance/credit-notes/{credit_note_id}/post`
  - `POST /v1/finance/credit-notes/{credit_note_id}/reverse`
  - `GET /v1/finance/credit-notes/{credit_note_id}`
  - `GET /v1/finance/credit-notes`
- Removed the old credit-note shortcut from `invoice_posting.py`.
- Registered the `credit_notes_router` in `app.py`.
- Regenerated `openapi/openapi.json` and `packages/api-client/src/schema.d.ts`.
- Added web credit-note workspace:
  - `apps/web/app/finance/credit-notes/page.tsx`
  - `apps/web/components/finance-credit-note-workspace.tsx`
  - `apps/web/app/api/finance/credit-notes/route.ts`
  - `apps/web/app/api/finance/credit-notes/[creditNoteId]/route.ts`
  - `apps/web/app/api/finance/credit-notes/[creditNoteId]/post/route.ts`
  - `apps/web/app/api/finance/credit-notes/[creditNoteId]/reverse/route.ts`
  - `apps/web/app/api/finance/invoices/[invoiceId]/credit-notes/route.ts`
- Added a **Credit notes** link to `apps/web/components/tradeflow-shell.tsx` and
  the `/finance` landing page.
- Added contract tests (`apps/api/tests/test_credit_note_contract.py`) covering
  request → post → statement update, idempotency, reversal, over-credit,
  wrong-currency, self-approval denial, approver limit, missing capability, and
  branch scope.
- Added database invariant tests
  (`apps/api/tests/test_credit_note_database_invariants.py`) covering immutable
  triggers, posted-shape enforcement, consecutive document-series numbers,
  reversal preservation, voided-invoice rejection, and serialized concurrent
  posts.
- Added migration tests (`apps/api/tests/test_credit_note_migration.py`)
  covering blocked downgrade while history exists, empty downgrade/uprade
  round-trip, and expected schema objects.
- Updated `apps/api/tests/test_invoice_posting_contract.py` to include the new
  credit-note capabilities in `FINANCE_CAPABILITIES` and removed the obsolete
  credit-note shortcut test.
- Added ADR `docs/adr/0018-immutable-credit-notes.md` and updated
  `contexts/finance/CONTEXT.md`.

## Known limitations / next slices

- Credit notes are supported only in the company base currency and against a
  single posted invoice; per-line credits and multi-currency notes are planned.
- The web workspace does not retain the idempotency key for manual retries after
  a network failure; a future enhancement can surface and preserve the last key.
- PDF rendering, customer notifications, refunds, commission reversals, and
  inventory restocking are out of scope and tracked in later slices.
