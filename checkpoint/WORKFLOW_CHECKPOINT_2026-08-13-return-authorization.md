# Workflow checkpoint: Return Authorization

## Scope

- Issue #65: approve a Return Authorization against Delivered Quantity less
  prior authorized quantity.
- Reconciled onto `main` after approved PR #111 merged as `5832ba6`.

## Implemented evidence

- `apps/api/src/tradeflow_api/returns.py`: request, read/list, and maker-checker
  authorization contracts with receipt-chain serialization, bounded batched
  listing, explicit replay headers, and current eligibility lookup.
- `apps/api/migrations/versions/e93736a741bd_return_requests_and_authorizations.py`:
  immutable persistence, direct-write guards, capabilities, and reversible empty
  migration with populated-history downgrade refusal.
- `apps/web/components/return-authorization-workspace.tsx`: responsive pending
  review queue, receipt-driven multi-line selection, and explicit authorization
  confirmation.
- Generated OpenAPI and TypeScript client include all Returns endpoints.
- ADR-0019 and Returns context document source-chain, quantity, classification,
  authority, and no-posting boundaries.

## Verification

- PostgreSQL contract tests cover request creation, no posting effects,
  maker-checker, amount limits, prior authorization arithmetic, exact replay,
  excess rejection, final-quantity concurrency, correction exclusion, and
  database immutability, including direct-write rejection after user,
  capability, Branch-scope, or Warehouse-scope revocation.
- Migration test covers downgrade to current Finance head `0017`, upgrade, and
  re-upgrade on an empty schema.
- Playwright covers receipt-driven multi-line request creation and the web
  review/authorization journey on desktop and mobile projects.
- Focused verification passed after implementation. The one final two-axis
  review completed and its P1/P2 findings were resolved; one current-head full
  CI gate remains before PR #112 is ready for human review.

## Next slice

After #65 is approved and merged, issue #66 adds offline return evidence and
sync without performing physical receipt or stock posting.
