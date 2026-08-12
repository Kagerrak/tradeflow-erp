# TradeFlow workflow checkpoint

- Date: August 13, 2026
- Phase: Issue #28 implemented; PR #31 published, CI green, and ready for review
- Branch: `feat/warehouse-scope-correction-auth`
- Commit: `3497eb0`
- Stacked base: `feat/immutable-delivery-corrections` (PR #29)
- PR: #31 — https://github.com/Kagerrak/tradeflow-erp/pull/31

## Established

- `approval_authorities` now supports an optional `warehouse_id` scope in addition
  to `branch_id`.
- Partial unique indexes `uq_approval_authority_branch` and
  `uq_approval_authority_warehouse` let the same user hold both branch-level and
  per-warehouse authorities for the same capability.
- `validate_delivery_correction_authorization()` rejects a warehouse-scoped
  authority when it does not match the correction's warehouse.
- The authorization endpoint query filters by branch and either branch-level or
  matching-warehouse authority.
- Bootstrap/configure-user flows validate warehouse existence in the branch and
  that the user is assigned to the warehouse.
- Downgrade migration `0016` refuses to run while warehouse-scoped authorities
  exist, preventing silent data loss.
- Added DB-invariant test
  `test_database_rejects_out_of_warehouse_correction_authorization`.
- Updated `docs/adr/0015-capability-scope-and-limit-authorization.md` to document
  the warehouse grain, indexes, trigger enforcement, and downgrade guard.
- Regenerated `openapi/openapi.json` and `packages/api-client/src/schema.d.ts`
  for the new optional `warehouse_code` field.

## Verification evidence

- `uv run ruff check apps/api/src apps/api/tests apps/worker/src` / `uv run mypy
apps/api/src apps/worker/src` — passed.
- `pnpm format` / `pnpm test` / `pnpm build` / `uv build --all-packages` — passed.
- `uv run pytest apps/api/tests/test_delivery_correction_*.py -q` — 17 passed.
- Full Python pytest suite — 122 passed, 4 skipped.
- `git diff --check` — passed.

## Closed / ready for review

- #26 — Extract delivery-correction inventory projection updates into shared
  inventory service (PR #30).
- #28 — Enforce warehouse scope for correction authorizations at the DB trigger
  layer (PR #31).

## Next dependency-ready issue

- **#27 — Frontend/BFF polish for delivery corrections (P2 UX/consistency).**
  This is the remaining item in the current order-to-delivery vertical before
  production release.

## Decisions needed from user

- Whether to merge the stacked PR chain (#24 → #25 → #29 → #30 → #31) now, or
  keep PRs open until #27 is completed.
- Whether #27 should be picked up next on a new branch
  `feat/delivery-correction-frontend-polish`.
