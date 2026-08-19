# Effective-Dated Expense Categories and Policies

**Date:** 2026-08-20  
**Issue:** [#83](https://github.com/Kagerrak/tradeflow-erp/issues/83)  
**Branch:** `feature/expense-categories-83`

## Summary

The first release can now configure effective-dated, immutable Expense
Categories and Expense Policies. Categories define allowed evidence types and
attribution rules; policies add amount thresholds, allowed currencies, receipt
requirements, and branch-scoped effective ranges. Publication is a
maker-checker command: a user with `finance:expense-*-create` drafts a version,
and a different user with `finance:expense-*-publish` approval authority
publishes it. Published versions cannot be edited or deleted, and their
effective ranges cannot overlap.

## What changed

### API / backend

- Added `expense_categories` table with company-scoped versioning,
  effective-dating, allowed evidence types, attribution rules, draft/published
  status, and publication audit columns.
- Added `expense_policies` table with company-wide or branch-scoped versioning,
  category-version reference, amount threshold, allowed currencies, receipt
  requirement, evidence override, attribution rules, effective dating, and
  publication audit columns.
- Added migration `1df3f2114a12_expense_categories_and_policies.py` extending
  head `0019`.
- Added immutable-publication triggers that reject any `UPDATE` or `DELETE` of
  a published category or policy version.
- Added overlap-prevention triggers that reject publishing a version whose
  effective range intersects an already-published version for the same code
  (and branch, for policies).
- Added unique indexes enforcing version uniqueness and a single active
  published code per scope.
- Added new capabilities:
  - `finance:expense-category-read`
  - `finance:expense-category-create`
  - `finance:expense-category-publish`
  - `finance:expense-policy-read`
  - `finance:expense-policy-create`
  - `finance:expense-policy-publish`
- Added `POST /v1/finance/expense-categories` to draft a category version.
- Added `POST /v1/finance/expense-categories/{category_code}/versions/{version}/publish`
  to publish a category version, enforcing self-publication rejection,
  approval authority, and non-overlapping effective ranges.
- Added `GET /v1/finance/expense-categories` and
  `GET /v1/finance/expense-categories/{category_code}` for listing versions.
- Added `POST /v1/finance/expense-policies` to draft a policy version,
  requiring a published category version and branch scope.
- Added `POST /v1/finance/expense-policies/{policy_code}/versions/{version}/publish`
  to publish a policy version, enforcing branch scope, maker-checker,
  approval-authority amount limits, and non-overlapping effective ranges.
- Added `GET /v1/finance/expense-policies` and
  `GET /v1/finance/expense-policies/{policy_code}` for listing versions.
- All publication commands require an `Idempotency-Key` header and replay
  stable responses.
- Mounted the new `expenses_router` under `/v1/finance`.

### Web / generated client

- Regenerated `openapi/openapi.json` and
  `packages/api-client/src/schema.d.ts` with the new endpoints and models.
- No web or mobile UI implementations are included in this slice; the mobile
  offline receipt-capture/approval workflow will consume these endpoints in a
  later slice.

### Tests

- Added `apps/api/tests/test_expense_contract.py` covering:
  - category/policy create-and-publish happy path,
  - capability checks,
  - self-publication rejection,
  - stale-publication rejection,
  - overlapping effective-range rejection,
  - policy requiring a published category,
  - branch-scope denial for policies,
  - approval-limit denial for policy publication,
  - idempotent publication replay,
  - published-category immutability at the database level.
- Added `apps/api/tests/test_expense_migration.py` verifying schema objects,
  overlap-trigger behavior, and downgrade/re-upgrade safety.

## Out of scope

- Expense Claim capture, evidence upload, approval workflow, posting, and
  payment status.
- General-ledger and payroll effects.
- Autonomous policy decisions.
- Web and mobile UI screens beyond endpoint exposure and generated-client
  updates.

## Verification

- Focused API contract tests: 14 passed.
- Full API test suite: 273 passed, 4 skipped.
- Full `pnpm test` (packages + web + API): passed.
- `pnpm format` — passed.
- `pnpm lint` — passed.
- `pnpm typecheck` — passed.
- `pnpm openapi:generate` — passed and deterministic.

## Deployment notes

- Migration `1df3f2114a12_expense_categories_and_policies.py` extends head
  `0019` and is reversible to base.
- No deterministic backfill is required; the tables are empty on upgrade.
- Downgrade drops the new tables, triggers, and functions after removing any
  data. Downgrade is blocked by the immutable-publication triggers only while
  rows exist; the migration test verifies a clean downgrade/re-upgrade cycle.

## Decision evidence

- Domain context: `contexts/finance/CONTEXT.md`
- Checkpoint: `checkpoint/WORKFLOW_CHECKPOINT_2026-08-20-expense-categories-policies.md`
