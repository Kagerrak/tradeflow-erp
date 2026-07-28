# Local development

## Prerequisites

- Node.js 22 or later
- pnpm 9.7
- Python 3.13
- uv 0.8
- Docker Desktop with Compose

## Bootstrap

1. Copy `.env.example` to `.env` and keep it local.
2. Run `pnpm bootstrap`.
3. Run `pnpm infra:up`.
4. Run `pnpm migrate`.
5. Run `pnpm openapi:generate`.

PostgreSQL listens on `5433`, Redis on `6380`, the S3-compatible API on `9000`,
and its local console on `9001`.

If Docker Desktop's credential helper blocks public image pulls, fix or
reauthenticate that local Docker installation. Do not commit registry
credentials or replace the public image references with private credentials.

## Run

- API: `pnpm api:dev`
- Worker: `pnpm worker:dev`
- Web and mobile: `pnpm dev`
- Containerized API and worker: `pnpm app:up`

The API requires an OIDC-compatible bearer token. Development and automated
tests may use the configured test signing secret; preview and production reject
test-token configuration and require a JWKS URL.

To exercise the web and mobile shells against the local API:

1. Run `uv run --env-file .env python scripts/create_test_token.py`.
2. Copy the short-lived output into `TRADEFLOW_WEB_TEST_ACCESS_TOKEN` and
   `EXPO_PUBLIC_TRADEFLOW_TEST_ACCESS_TOKEN` in `.env`.
3. Start the API, then start web or mobile. Never use these test-token settings
   in preview or production.

## Verify

- Format: `pnpm format`
- Lint: `pnpm lint`
- Typecheck: `pnpm typecheck`
- Tests: `pnpm test`
- Builds: `pnpm build`
- Expo configuration: `pnpm --filter @tradeflow/mobile run doctor`
- Migrations: `uv run alembic -c apps/api/alembic.ini upgrade head`

API acceptance tests use real PostgreSQL. Worker health tests use real Redis.
Playwright covers the web shell at desktop and mobile breakpoints. Expo
component journeys cover the corresponding native states, and the Expo export
build bundles web, iOS, and Android. OpenAPI generation is deterministic, and
CI fails if generated contracts drift.

CI also starts an external API process and runs an unmocked authenticated
journey from both the Next.js shell and the rendered native client through the
generated client into migrated PostgreSQL. The same gate exercises durable
idempotent command replay over black-box HTTP.

Clients propagate both `X-Correlation-ID` and W3C `traceparent`. The API binds
the correlation identity to structured request logs, server spans, and explicit
database-check spans. Startup fails when PostgreSQL is reachable but Alembic is
not at the expected revision.

## Configuration safety

- Never commit `.env`.
- Use separate secrets and databases for development, preview, and production.
- Test-token signing is forbidden in preview and production.
- `EXPO_PUBLIC_` values are embedded in the client; the test token is local-only
  and short-lived. Production authentication must use the configured OIDC
  provider and secure platform storage.
- Correlation IDs may be logged; bearer tokens and secrets may not.
