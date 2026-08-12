# TradeFlow workflow checkpoint

- Date: August 13, 2026
- Phase: Issue #14 shipped — order-to-delivery release complete
- Branch: `main`
- Commit: `b016f7c`
- Release PR: #33 — https://github.com/Kagerrak/tradeflow-erp/pull/33
- PR chain integrated: #15 → #16 → #17 → #18 → #19 → #20 → #21 → #22 → #23 → #24 →
  #25 → #29 → #30 → #31 → #32

## Established

- Created a release integration branch from `main`.
- Merged the complete order-to-delivery PR chain locally:
  - Platform shell (#15)
  - Organization/customer onboarding (#16)
  - Opening stock (#17)
  - Sales order draft (#18)
  - Commercial approval (#19)
  - Prepaid payment clearance (#20)
  - Tracked stock picking (#21)
  - Dispatch and mobile delivery (#22)
  - Offline delivery confirmation (#23)
  - Cash on delivery collection/reconciliation (#24)
  - Delivery exception custody (#25)
  - Immutable delivery receipt corrections (#29)
  - Shared inventory-projection service (#30)
  - Warehouse-scoped correction authorizations (#31)
  - Frontend/BFF correction polish (#32)
- Resolved a single import conflict in
  `apps/api/src/tradeflow_api/delivery_corrections.py` between #30 and #31.
- Removed an unused `pg_insert` import surfaced by ruff on the integrated branch.
- Added release notes at
  `docs/release-notes/order-to-delivery-2026-08-13.md`.
- Added Issue #14 acceptance tests:
  - `test_delivery_correction_authorization_matrix` — capability, scope,
    authority grain, and limit denial matrix.
  - `test_correction_outbox_handlers_are_idempotent_and_recover_from_transient_failure`
    — outbox deduplication and recovery after storage failure.
  - `test_correction_projections_reconcile_after_rebuild` — availability/valuation
    equality before and after a projection rebuild.

## Verification evidence

- `uv run ruff check apps/api/src apps/api/tests apps/worker/src` — passed.
- `uv run ruff format --check .` — passed.
- `uv run mypy apps/api/src apps/worker/src` — passed.
- `pnpm format` — passed.
- `pnpm lint` — passed.
- `pnpm typecheck` — passed.
- `pnpm test` — passed.
  - Full Python pytest suite: **125 passed, 4 skipped**.
  - Playwright delivery-corrections spec: **38 passed** (chromium + mobile-web).
- `pnpm build` / `uv build --all-packages` — passed.
- `git diff --check` — passed.
- GitHub Actions CI for PR #33 — completed successfully
  (run `31630189380`, https://github.com/Kagerrak/tradeflow-erp/actions/runs/31630189380).

## Closed / ready for review

- #2 — Boot a secured cross-platform TradeFlow shell (PR #15).
- #3 — Configure organization scope and onboard a Customer Account (PR #16).
- #4 — Receive traceable opening stock and query availability (PR #17).
- #5 — Capture and synchronize a priced Sales Order draft (PR #18).
- #6 — Commercially approve and partially reserve a Sales Order (PR #19).
- #7 — Clear payment and enforce Prepaid reservation deadlines (PR #20).
- #8 — Pick tracked stock into Dispatch Staging (PR #21).
- #9 — Dispatch stock and assign a mobile Delivery (PR #22).
- #10 — Confirm an accepted Delivery from offline proof (PR #23).
- #11 — Collect and reconcile Cash on Delivery (PR #24).
- #12 — Resolve Delivery Exceptions without losing custody (PR #25).
- #13 — Correct an issued Delivery Receipt immutably (PR #29).
- #26 — Extract delivery-correction inventory projection updates into shared
  inventory service (PR #30).
- #28 — Enforce warehouse scope for correction authorizations at the DB trigger
  layer (PR #31).
- #27 — Frontend/BFF polish for delivery corrections (PR #32).

## Merged

- #24 — Collect and reconcile Cash on Delivery (merged to
  `feat/offline-delivery-confirmation`).
- #25 — Resolve Delivery Exceptions without losing custody (merged to
  `feat/cod-collection-reconciliation`).
- #29 — Correct an issued Delivery Receipt immutably (merged to
  `feat/delivery-exception-custody`).
- #33 — Order-to-delivery release integration (merged to `main`).

## Shipped

- #1 — Platform foundation and order-to-delivery (PRD).
- #2 — Boot a secured cross-platform TradeFlow shell (PR #15).
- #3 — Configure organization scope and onboard a Customer Account (PR #16).
- #4 — Receive traceable opening stock and query availability (PR #17).
- #5 — Capture and synchronize a priced Sales Order draft (PR #18).
- #6 — Commercially approve and partially reserve a Sales Order (PR #19).
- #7 — Clear payment and enforce Prepaid reservation deadlines (PR #20).
- #8 — Pick tracked stock into Dispatch Staging (PR #21).
- #9 — Dispatch stock and assign a mobile Delivery (PR #22).
- #10 — Confirm an accepted Delivery from offline proof (PR #23).
- #11 — Collect and reconcile Cash on Delivery (PR #24).
- #12 — Resolve Delivery Exceptions without losing custody (PR #25).
- #13 — Correct an issued Delivery Receipt immutably (PR #29).
- #14 — Rebuild, reconcile, and release the complete order-to-delivery slice
  (PR #33).
- #26 — Extract delivery-correction inventory projection updates into shared
  inventory service (PR #30).
- #27 — Frontend/BFF polish for delivery corrections (PR #32).
- #28 — Enforce warehouse scope for correction authorizations at the DB trigger
  layer (PR #31).

## Residual risks and follow-ups

- Native iOS/Android real-device journeys should be verified in staging.
- Operational runbook exercises for projection rebuild and outbox replay should
  be performed in staging.
- Node engine mismatch (CI on Node 20 vs. `package.json` `>=22`) should be
  aligned before production.

## Next issue

- Procurement inbound receipts and landed cost, or Finance invoice posting and
  customer statements, depending on product priority.
