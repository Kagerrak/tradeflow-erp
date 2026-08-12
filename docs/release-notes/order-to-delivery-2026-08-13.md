# TradeFlow ERP — Order-to-Delivery Release Notes

Release: order-to-delivery vertical  
Date: 2026-08-13  
Release branch: `feat/order-to-delivery-release`  
Release PR: #33 — https://github.com/Kagerrak/tradeflow-erp/pull/33  
Migrations: `0001_platform_command_receipts.py` → `0016_warehouse_scope_approval_authorities.py`

## Scope

This release completes the first end-to-end TradeFlow ERP vertical from platform
shell through customer onboarding, sales order capture, inventory management,
fulfillment, delivery confirmation, payment clearance, delivery exceptions,
immutable delivery receipt corrections, and warehouse-scoped correction
authorization.

## Integrated pull requests

| Issue | PR  | Title                                                                                  |
| ----- | --- | -------------------------------------------------------------------------------------- |
| #2    | #15 | Boot a secured cross-platform TradeFlow shell                                          |
| #3    | #16 | Configure organization scope and onboard a Customer Account                            |
| #4    | #17 | Receive traceable opening stock and query availability                                 |
| #5    | #18 | Capture and synchronize a priced Sales Order draft                                     |
| #6    | #19 | Commercially approve and partially reserve a Sales Order                               |
| #7    | #20 | Clear payment and enforce Prepaid reservation deadlines                                |
| #8    | #21 | Pick tracked stock into Dispatch Staging                                               |
| #9    | #22 | Dispatch stock and assign a mobile Delivery                                            |
| #10   | #23 | Confirm an accepted Delivery from offline proof                                        |
| #11   | #24 | Collect and reconcile Cash on Delivery                                                 |
| #12   | #25 | Resolve Delivery Exceptions without losing custody                                     |
| #13   | #29 | Correct an issued Delivery Receipt immutably                                           |
| #26   | #30 | Extract delivery-correction inventory projection updates into shared inventory service |
| #28   | #31 | Enforce warehouse scope for correction authorizations at the DB trigger layer          |
| #27   | #32 | Frontend/BFF polish for delivery corrections                                           |

## Runtime environment

- Python: `>=3.13,<3.14` (managed by `uv`)
- Node.js: `>=22` (package manager `pnpm@9.7.0`)
- PostgreSQL: 17+ (see `infra/compose.yaml` and `infra/postgres/init/`)
- Object storage: S3-compatible (local MinIO in compose, configurable via env)
- Worker: `arq` Redis-backed async worker
- Mobile: Expo SDK 57 (iOS, Android, web)
- Web: Next.js 16 / Turbopack

## Verification evidence

All gates passed on `feat/order-to-delivery-release` at commit `3146c3d`:

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
  (run `31628402650`, https://github.com/Kagerrak/tradeflow-erp/actions/runs/31628402650).

## Deployment steps

1. Ensure target database is PostgreSQL 17+ and reachable.
2. Apply migrations: `pnpm migrate` (runs `alembic upgrade head`).
3. Start infrastructure: `pnpm infra:up`.
4. Start application: `pnpm app:up`.
5. Verify health endpoints and worker liveness.
6. Run a smoke test of the principal flow (sales order → reservation → pick →
   dispatch → confirm → invoice → payment).

## Rollback

- Database: downgrade one migration at a time with
  `uv run alembic -c apps/api/alembic.ini downgrade -1`.  
  Migration `0016` refuses to downgrade while warehouse-scoped approval
  authorities exist, preventing silent authorization-scope data loss.
- Code: revert to previous release tag/branch; the API and worker are built as
  Python wheels and the web/mobile apps are static exports.
- Projections: availability and customer-balance projections can be rebuilt from
  immutable stock movements and ledger entries if needed.

## Known risks and limitations

- **Stacked PR complexity**: fifteen PRs were integrated for this release. A
  conflict between the shared inventory-projection service (#30) and warehouse
  scope trigger (#31) in `delivery_corrections.py` was resolved on the release
  branch; review this resolution carefully.
- **Node engine mismatch**: local CI currently runs on Node 20.19.1 while
  `package.json` declares `>=22`. All `pnpm` commands pass, but running on the
  declared Node version in production is recommended.
- **Black-box tests**: four black-box contract tests are skipped unless the
  full Docker compose stack is running.
- **Real-device verification**: mobile-web Playwright coverage exists; native
  iOS/Android device journeys should be verified in a staging environment before
  production.
- **Recovery tooling**: projection rebuild and outbox replay paths are tested at
  the unit/contract level; operational runbooks should be exercised in staging.

## Next issue

- #14 follow-up / next vertical: Procurement inbound receipts and landed cost,
  or Finance invoice posting and customer statements, depending on product
  priority.
