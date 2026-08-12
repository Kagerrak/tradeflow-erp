# TradeFlow workflow checkpoint

- Date: August 12, 2026
- Phase: Issue #13 implemented; draft stacked PR published and CI green
- Branch: `feat/immutable-delivery-corrections`
- Commit: `1a04b3a`
- Stacked base: `feat/delivery-exception-custody` (PR #25)
- Draft PR: #29 — https://github.com/Kagerrak/tradeflow-erp/pull/29

## Established

- Authorized, immutable Delivery Receipt corrections per ADR-0017.
- Maker-checker separation with branch/warehouse scope and approval authority.
- Linked stock reversal/replacement movements preserving tracked identities and
  moving-average valuation.
- Draft Invoice source reversed/replaced idempotently without customer-ledger
  postings in this slice.
- Replacement Delivery Receipt issued from a new Branch-series number when
  corrected accepted quantity is positive; zero-accepted corrections create no
  replacement receipt/invoice.
- Sequential correction chains reconstruct invoice lines from root invoice
  snapshots.
- Web correction ledger/dossier, maker/checker flows, evidence selection,
  pending/posted effect preview, and Playwright coverage.
- Migration `0015_immutable_delivery_corrections` with DB invariants,
  downgrade safety, OpenAPI export, and generated TypeScript client.

## Verification evidence

- `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build` — passed locally.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy apps/api/src apps/worker/src` — passed.
- Alembic upgrade head / downgrade -1 / upgrade head — passed.
- `git diff --check` — passed.
- CI run `31608218659` for PR #29 — success.

## Residual risks / follow-up issues

- #26 — Extract correction inventory-projection updates into a shared inventory
  service (architecture boundary; dependency-ready now that #13 is implemented).
- #27 — Frontend/BFF polish for delivery corrections (P2 UX/consistency).
- #28 — Enforce warehouse scope for correction authorizations at the DB trigger
  layer (defense-in-depth; requires authority schema decision).

## Next dependency-ready issue

- **#26 — Extract delivery-correction inventory projection updates into shared
  inventory service.** It is blocked only by #13 and addresses the most
  significant architectural debt surfaced by review.
- The next vertical ERP slice after the stacked dependency chain merges is
  **#14 — Rebuild, reconcile, and release the complete order-to-delivery
  slice**, which is currently blocked by open PRs for #11, #12, and #13.

## Decisions needed from user

- Whether to merge the stacked PR chain (#24 → #25 → #29) in order, or keep the
  draft PRs open until #26/#27/#28 are completed.
- Whether #28 should extend `approval_authorities` with a `warehouse_id` column
  or use a separate warehouse-scoped authority grain.
