# TradeFlow workflow checkpoint

- Date: August 20, 2026
- Phase: Issue #83 ready for final review — Effective-Dated Expense Categories
  and Policies
- Branch: `feature/expense-categories-83`
- Release PR: to be created after final independent review and full CI gate

## Established

- Added migration
  `apps/api/migrations/versions/1df3f2114a12_expense_categories_and_policies.py`
  extending `0019` with:
  - `expense_categories` table: company-scoped versioning, effective dating,
    allowed evidence types, attribution rules, draft/published status, and
    publication audit columns.
  - `expense_policies` table: company-wide or branch-scoped versioning,
    category-version reference, amount threshold, currencies, receipt and
    evidence rules, attribution rules, effective dating, and publication audit
    columns.
  - Unique indexes for version and active-published-code constraints.
  - Immutable-publication triggers rejecting updates/deletes of published
    versions.
  - Overlap-prevention triggers rejecting intersecting effective ranges for the
    same code (and branch, for policies).
- Extended `apps/api/src/tradeflow_api/models.py` with `expense_categories` and
  `expense_policies` SQLAlchemy Core tables.
- Extended `apps/api/src/tradeflow_api/auth.py` with expense capability
  dependencies.
- Added `apps/api/src/tradeflow_api/expenses.py` mounted in
  `apps/api/src/tradeflow_api/app.py` with endpoints to create, publish, and
  list category and policy versions.
- Implemented maker-checker publication: a publisher cannot be the same user
  who created the draft version.
- Implemented approval-authority checks for publication, including amount-limit
  enforcement for policy publication.
- Implemented branch-scope checks for branch-scoped policies.
- Implemented idempotent command replay with `Idempotency-Key` headers.
- Added focused contract tests in `apps/api/tests/test_expense_contract.py`
  covering happy path, capability guards, self-publication, stale publication,
  overlapping ranges, published-category requirement, branch-scope denial,
  approval-limit denial, idempotency, and database-level immutability.
- Added migration safety tests in `apps/api/tests/test_expense_migration.py`
  verifying schema objects, overlap-trigger behavior, and downgrade/re-upgrade
  round-trips.
- Regenerated OpenAPI contract (`openapi/openapi.json`) and TypeScript client
  (`packages/api-client/src/schema.d.ts`).
- Added release notes at
  `docs/release-notes/finance-expense-categories-policies-2026-08-20.md`.

## Verification evidence

- `uv run pytest -q apps/api/tests/test_expense_contract.py apps/api/tests/test_expense_migration.py` — **14 passed**.
- `uv run pytest -q apps/api/tests` — **273 passed, 4 skipped**.
- `pnpm test` — passed.
- `pnpm format` — passed.
- `pnpm lint` — passed.
- `pnpm typecheck` — passed.
- `pnpm openapi:generate` — passed and deterministic.
- `git diff --check` — passed.

## Final review

- One independent standards/specification review is pending after the green
  draft PR is published.

## Remaining gate

- Push branch, open a PR referencing `Closes #83`, confirm full CI is green, and
  obtain explicit merge approval before squash-merging.

## Deferred scope

- Expense Claim capture, evidence upload, approval workflow, posting, and
  payment status.
- General-ledger and payroll integration.
- Autonomous policy decisions.
- Web and mobile UI screens for category/policy management.

## Next issue

- Parent #59 — Expense management foundation remains open until claim capture,
  evidence, attribution, and posting slices are delivered.
