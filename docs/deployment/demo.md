# Public demo deployment

The demo topology keeps PostgreSQL, Redis, object storage, the FastAPI service, and the worker on a private network. Only the Next.js web container publishes a port. Browser requests reach the API through server-side Next route handlers; the shared bearer credential is owned by the web container's fixed UID in a mode-`0400` file on the private `demo-state` volume and never enters public environment variables, HTML, JSON, or client bundles.

## Release sequence

1. Copy `.env.demo.example` into the deployment provider's secret store and supply strong values.
2. Set `TRADEFLOW_PUBLIC_URL` to the final HTTPS origin.
3. Run `docker compose --env-file .env.demo -f infra/compose.demo.yaml up -d --build`.

Set `TRADEFLOW_WEB_PORT` when port 3000 is already in use (for example,
`TRADEFLOW_WEB_PORT=3200` for a parallel local smoke test).

4. The API upgrades migrations before reporting healthy. The reset service
   takes a PostgreSQL advisory lock, marks the demo refreshing, truncates only
   the explicitly named `tradeflow_demo` database, seeds through application
   commands, validates every required lifecycle state, and publishes a fresh
   two-hour Demo Operator credential.
5. Verify `/`, `/demo`, and `/health/ready` from inside the private network.
   Run the real-stack browser smoke journey with
   `PLAYWRIGHT_BASE_URL=https://demo.example.com TRADEFLOW_SEEDED_DEMO=1 pnpm --filter @tradeflow/web test:demo`.
   Configure an external uptime check for `/` and `/api/demo/status`.

The reset repeats every 45 minutes. While it runs, `/v1/*` rejects traffic with
HTTP 503 and `Retry-After`, while health endpoints remain available. A second
reset refuses to run while the advisory lock is held. The reset completes only
after the required lifecycle, inventory, and finance evidence is present.
Failures leave `status.json` in `failed` state and preserve logs for alerting.

## Provider controls

- Set monthly spend alerts and hard resource limits before exposing the service.
- Forward OTLP telemetry and application errors to the approved provider; do not include request authorization headers.
- Alert when the public landing page, demo status, API migration revision, or seed version becomes unhealthy.
- Backups are optional for the disposable demo database; production data must use a different project, database, credentials, and configuration.
- Keep DNS and TLS at the provider edge. Do not publish the API, PostgreSQL, Redis, or MinIO ports.
