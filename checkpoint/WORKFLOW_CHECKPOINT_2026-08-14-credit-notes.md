# TradeFlow workflow checkpoint

- Date: August 14, 2026
- Phase: Issue #71 ready for review — immutable credit notes under maker-checker
  control
- Branch: `feature/return-authorization`
- Base branch: `main`
- Release PR: #71

## Established

- Added credit note document migration
  (`apps/api/migrations/versions/0017_credit_note_documents.py`) merging the two
  previous Alembic heads (`d53dcaa7ede3`, `d524a29c32b8`).
- Added `credit_notes` and `credit_note_authorizations` tables with
  immutable-history triggers, deferred authorization validation, and document
  series integration.
- Expanded `document_series` to support `credit_note` and added
  `credit_note_id` to `document_series_number_audit`.
- Expanded `customer_ledger_entries.source_type` to include
  `credit_note_reversal`.
- Added capabilities `finance:credit-note-request`,
  `finance:credit-note-approve`, and `finance:credit-note-read` with dedicated
  auth guards.
- Implemented `apps/api/src/tradeflow_api/credit_notes.py` with request,
  authorize/post, reverse, fetch, and list endpoints.
- Removed the old bare credit-note shortcut from `invoice_posting.py`.
- Registered the credit-note router in `apps/api/src/tradeflow_api/app.py`.
- Regenerated `openapi/openapi.json` and `packages/api-client/src/schema.d.ts`.
- Added web credit-note workspace:
  - `apps/web/app/finance/credit-notes/page.tsx`
  - `apps/web/components/finance-credit-note-workspace.tsx`
  - `apps/web/app/api/finance/credit-notes/route.ts`
  - `apps/web/app/api/finance/credit-notes/[creditNoteId]/route.ts`
  - `apps/web/app/api/finance/credit-notes/[creditNoteId]/post/route.ts`
  - `apps/web/app/api/finance/credit-notes/[creditNoteId]/reverse/route.ts`
  - `apps/web/app/api/finance/invoices/[invoiceId]/credit-notes/route.ts`
- Added a **Credit notes** link in `apps/web/components/tradeflow-shell.tsx` and
  the `/finance` landing page.
- Added contract tests (`apps/api/tests/test_credit_note_contract.py`),
  database invariant tests
  (`apps/api/tests/test_credit_note_database_invariants.py`), and migration
  tests (`apps/api/tests/test_credit_note_migration.py`).
- Updated `apps/api/tests/test_invoice_posting_contract.py` to include the new
  credit-note capabilities and removed the obsolete shortcut test.
- Added ADR `docs/adr/0018-immutable-credit-notes.md`, updated
  `contexts/finance/CONTEXT.md`, and created release notes at
  `docs/release-notes/finance-credit-notes-2026-08-14.md`.

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

## Closed / ready for review

- #71 — Immutable credit notes with maker-checker control (PR #71).

## Shipped

- Not merged; this is a green draft PR awaiting review.

## Residual risks and follow-ups

- The web workspace does not yet retain the idempotency key for manual retries
  after a network failure.
- Multi-currency credit notes, per-line credits, PDF rendering, customer
  notifications, refunds, commission reversals, and inventory restocking are
  intentionally out of scope for this slice.

## Next issue

- Continue first-release delivery from the next dependency-ready slice.
