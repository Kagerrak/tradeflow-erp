# Staging deployment runbook

This runbook describes how to deploy TradeFlow ERP to a production-like staging
environment using the hardened compose files.

## Prerequisites

- Docker Engine 24.x or later with Docker Compose.
- Access to an OIDC identity provider that exposes a JWKS endpoint.
- Strong credentials for PostgreSQL, Redis, and MinIO.
- A running OpenTelemetry collector if telemetry is enabled.

## Environment variables

Create `infra/.env.staging` from `.env.example` and replace every placeholder.
Never set `TRADEFLOW_AUTH_TEST_SECRET` in staging.

```bash
TRADEFLOW_ENVIRONMENT=preview
TRADEFLOW_AUTH_ISSUER=https://identity.staging.example
TRADEFLOW_AUTH_AUDIENCE=tradeflow-api
TRADEFLOW_AUTH_JWKS_URL=https://identity.staging.example/.well-known/jwks.json
TRADEFLOW_DATABASE_URL=postgresql+asyncpg://tradeflow:${DB_PASSWORD}@postgres:5432/tradeflow
TRADEFLOW_TELEMETRY_ENABLED=true
TRADEFLOW_OTLP_ENDPOINT=https://otel.staging.example/v1/traces
TRADEFLOW_RATE_LIMIT_ENABLED=true
TRADEFLOW_RATE_LIMIT_REQUESTS_PER_MINUTE=120

TRADEFLOW_WORKER_ENVIRONMENT=preview
TRADEFLOW_WORKER_REDIS_URL=redis://redis:6379/0
TRADEFLOW_WORKER_TELEMETRY_ENABLED=true
TRADEFLOW_WORKER_OTLP_ENDPOINT=https://otel.staging.example/v1/traces
```

## Deploy

```bash
cd infra
docker compose -f compose.yaml -f compose.staging.yaml --profile application up -d
```

## Verify

1. Live probe: `curl https://api.staging.example/health/live`
2. Ready probe: `curl https://api.staging.example/health/ready`
3. Session endpoint returns a valid bearer token from the production OIDC issuer.
4. No `TRADEFLOW_AUTH_TEST_SECRET` value is present in any running container:

```bash
docker compose -f compose.yaml -f compose.staging.yaml exec api env | grep TEST_SECRET || true
```

Expected: empty output.

## Rollback

See `migration-rollback.md` for database rollback procedures.
