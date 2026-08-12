# TradeFlow workflow checkpoint

- Date: August 13, 2026
- Phase: Issue #27 implemented; PR #32 published, CI green, and ready for review
- Branch: `feat/delivery-correction-frontend-polish`
- Commit: `4762f0b`
- Stacked base: `main`
- PR: #32 — https://github.com/Kagerrak/tradeflow-erp/pull/32

## Established

- Refactored `apps/web/app/api/delivery-receipts/[receiptId]/route.ts` to reuse
  shared `correction-api.ts` helpers for detail and signed-access requests.
- Restored `apps/web/lib/correction-api.ts` as the single seam for all
  correction/receipt BFF routes, preserving error codes such as
  `delivery_correction_service_unavailable`.
- Derived workspace correction types from `@tradeflow/api-client` schemas while
  keeping local `CorrectionLine` and `IdentityPosition` types to guarantee
  required `identity_positions` arrays in the UI.
- Added `expected_reversal_count` and `expected_replacement_count` to the backend
  `StockEffect` model and consumed them in the workspace effects panel.
- Added `ReceiptDocumentLink`, which POSTs to the BFF receipt access endpoint and
  opens the returned signed `access_url`.
- Preserved posted-state invoice/receipt behavior with a conditional that uses
  line quantities for pending corrections and persisted IDs for posted
  corrections.
- Added a `mobile-web` Playwright describe block with narrow viewport and touch
  coverage for the delivery-corrections spec.
- Regenerated `openapi/openapi.json` and `packages/api-client/src/schema.d.ts`
  for the new `StockEffect` counts.

## Verification evidence

- `uv run ruff check apps/api/src/tradeflow_api/delivery_corrections.py
apps/api/tests/test_delivery_correction_*.py` — passed.
- `uv run mypy apps/api/src/tradeflow_api/delivery_corrections.py` — passed.
- `uv run pytest apps/api/tests/test_delivery_correction_*.py -q` — 17 passed.
- Full Python pytest suite — 122 passed, 4 skipped.
- `pnpm format` — passed.
- `pnpm --filter @tradeflow/web typecheck` — passed.
- `pnpm --filter @tradeflow/web test -- tests/delivery-corrections.spec.ts` —
  38 passed (chromium + mobile-web).
- `pnpm test` / `pnpm build` / `uv build --all-packages` — passed.
- `git diff --check` — passed.

## Closed / ready for review

- #26 — Extract delivery-correction inventory projection updates into shared
  inventory service (PR #30).
- #28 — Enforce warehouse scope for correction authorizations at the DB trigger
  layer (PR #31).
- #27 — Frontend/BFF polish for delivery corrections (PR #32).

## Next dependency-ready issue

- **#14 — Rebuild, reconcile, and release the complete order-to-delivery slice.**
  This is the remaining production-release milestone in the current vertical.

## Decisions needed from user

- Whether to merge the stacked PR chain (#24 → #25 → #29 → #30 → #31 → #32) now,
  or keep PRs open until release integration testing is complete.
- Whether Issue #14 should be picked up next on a new branch
  `feat/order-to-delivery-release`.
