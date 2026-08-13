# Workflow checkpoint: Return Authorization

## Scope

- Issue #65: approve a Return Authorization against Delivered Quantity less
  prior authorized quantity.
- Stacked on the tracker-reconciliation branch pending explicit merge approval
  for PR #111.

## Implemented evidence

- `apps/api/src/tradeflow_api/returns.py`: request, read/list, and maker-checker
  authorization contracts with receipt-chain serialization and stable replay.
- `apps/api/migrations/versions/e93736a741bd_return_requests_and_authorizations.py`:
  immutable persistence, direct-write guards, capabilities, and reversible empty
  migration with populated-history downgrade refusal.
- `apps/web/components/return-authorization-workspace.tsx`: responsive pending
  review queue and explicit authorization confirmation.
- Generated OpenAPI and TypeScript client include all Returns endpoints.
- ADR-0018 and Returns context document source-chain, quantity, classification,
  authority, and no-posting boundaries.

## Verification

- PostgreSQL contract tests cover request creation, no posting effects,
  maker-checker, amount limits, prior authorization arithmetic, exact replay,
  excess rejection, final-quantity concurrency, correction exclusion, and
  database immutability.
- Migration test covers downgrade, upgrade, and re-upgrade on an empty schema.
- Playwright covers the web review and authorization journey.
- Full repository gates and independent reviews remain required before the
  stacked draft PR is ready.

## Next slice

After #65 is approved and merged, issue #66 adds offline return evidence and
sync without performing physical receipt or stock posting.
