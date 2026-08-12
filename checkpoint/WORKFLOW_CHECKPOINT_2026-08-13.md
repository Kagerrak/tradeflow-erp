# TradeFlow workflow checkpoint

- Date: August 13, 2026
- Phase: Issue #26 implemented; PR #30 published, CI green, and ready for review
- Branch: `feat/shared-inventory-projection-service`
- Commit: `ba1e081`
- Stacked base: `feat/immutable-delivery-corrections` (PR #29)
- PR: #30 — https://github.com/Kagerrak/tradeflow-erp/pull/30

## Established

- Shared `inventory_projection_service` owns `inventory_availability` and
  `inventory_valuation` UPSERT/decrement/update logic.
- Canonical lock hierarchy centralized: shared `inventory-projection-rebuild`
  advisory lock → per-sku/warehouse advisory lock → `SELECT ... FOR UPDATE` row
  lock.
- Missing valuation rows raise a domain `AppError` instead of a raw SQL 500.
- `catalog_inventory.py` opening-stock posting and projection rebuild replay
  through the shared service without weakening existing assertions.
- `delivery_corrections.py` delegates availability and valuation effects to the
  shared service; the fulfillment module no longer writes projection rows
  directly.
- `docs/architecture.md` and `contexts/fulfillment/CONTEXT.md` document the new
  seam.

## Verification evidence

- `uv run ruff check .` / `uv run ruff format --check .` / `uv run mypy apps/api/src apps/worker/src` — passed.
- `pnpm format` / `pnpm lint` / `pnpm typecheck` / `pnpm test` / `pnpm build` — passed.
- Alembic upgrade head / downgrade -1 / upgrade head — passed.
- `git diff --check` — passed.
- API contract/invariant/migration tests for delivery corrections and catalog
  inventory — 21 passed, 1 skipped.
- CI run `31620221493` for PR #30 — success.

## Residual risks / follow-up issues

- #27 — Frontend/BFF polish for delivery corrections (P2 UX/consistency).
- #28 — Enforce warehouse scope for correction authorizations at the DB trigger
  layer (defense-in-depth; requires authority schema decision).

## Next dependency-ready issue

- **#28 — Enforce warehouse scope for correction authorizations at the DB trigger
  layer.** It is blocked only by #13 and is the next hardening step after #26.

## Decisions needed from user

- Whether to merge the stacked PR chain (#24 → #25 → #29) now, or keep PRs open
  until #27/#28 are completed.
- Whether #28 should extend `approval_authorities` with an optional
  `warehouse_id` column or use a separate warehouse-scoped authority grain.
