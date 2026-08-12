# TradeFlow workflow checkpoint

- Date: July 29, 2026
- Phase: Issue #2 platform shell verified; publishing in progress
- Branch: `feat/platform-shell`
- Baseline: `8dd085f`

## Established

- The uv and pnpm workspaces expose repeatable bootstrap, format, lint,
  typecheck, test, build, migration, infrastructure, and OpenAPI commands.
- Pinned local PostgreSQL, Redis, and MinIO containers are healthy.
- FastAPI validates deployment configuration, verifies OIDC-compatible bearer
  tokens, emits stable correlated errors, and exposes public liveness and
  database-backed readiness probes.
- API startup refuses a reachable but unmigrated PostgreSQL database.
- The authenticated session contract and persisted idempotent command contract
  run against migrated PostgreSQL.
- The ARQ worker connects to real Redis.
- OpenAPI generation produces the shared typed client deterministically,
  including the same stable error envelopes returned at runtime.
- Next.js and Expo use one generated-client session journey, shared operational
  state copy and color tokens, W3C trace context, and correlated recovery
  states.
- Playwright covers the desktop and narrow web shells; React Native Testing
  Library covers the corresponding native shell states.
- CI verifies migrations, generated-contract drift, formatting, linting,
  Python and TypeScript types, Expo configuration, tests, real-stack web/mobile
  journeys, and package builds.

## Verification evidence

- `pnpm bootstrap` completed from the frozen lockfiles.
- `pnpm format` passed.
- `pnpm lint` passed.
- `pnpm typecheck` passed.
- The full unit and component suite passed across Python, TypeScript, web, and
  mobile.
- Next.js production build, Expo web/iOS/Android exports, and Python source and
  wheel distributions passed.
- Expo Doctor passed all 20 checks.
- The rebuilt API container returned database-backed readiness.
- External-process black-box acceptance passed through HTTP into migrated
  PostgreSQL, including durable idempotent replay.
- Unmocked web and rendered native journeys both reached the generated client,
  live API process, and real migrated PostgreSQL database.
- The API log preserved the client correlation ID without logging bearer-token
  or secret values.

## Remaining in issue #2

- Complete the clean release-gate rerun.
- Commit and push `feat/platform-shell`.
- Open the issue #2 pull request and monitor CI.

## Deferred human policy decisions

- Commission basis and earning trigger, deferred to the commission slice.
- International landed-cost allocation, deferred to the procurement slice.
- Organization-specific deployment values such as Base Currency, tax seeds,
  approval limits, payment deadlines, and document-series formats.
- Production OIDC provider selection; the application boundary remains
  standards-based and deployment-configurable.
