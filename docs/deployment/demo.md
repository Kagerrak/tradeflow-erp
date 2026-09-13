# Public demo deployment

The public demo is the serverless stack in `ap-southeast-2` (account
`527673188999`).

**Live URL:** <https://dt7yjmo5ppcxs.cloudfront.net>

Status endpoint: <https://dt7yjmo5ppcxs.cloudfront.net/api/demo/status>

## Topology

```
Browser
  -> CloudFront distribution (E36XR7FAZKRYLS)
       /_next/static/*, /product/*  -> private S3 bucket (immutable cache)
       everything else              -> web Lambda Function URL (no cache)
  web Lambda  (Next.js 16.2.12 standalone on the AWS Lambda Web Adapter)
              mints its own short-lived demo credential, no AWS permissions
  -> API Gateway HTTP API -> api Lambda -> Aurora PostgreSQL Serverless v2
                                        -> DynamoDB (demo coordination)
                                        -> S3 (job markers)
  S3 marker -> SQS (tradeflow-demo-jobs) -> worker Lambda -> Aurora, S3, DynamoDB
```

The database is an Aurora **express-configuration** cluster, which is the only
Aurora creation path available on this account's plan. It therefore has no VPC
association and is reached through Aurora's internet access gateway using
**IAM authentication only** — there is no database password. See
`docs/adr/0020-serverless-demo-hosting.md` for the trade-offs this forces,
including that the database is not private.

Full resource inventory and operating limits: `docs/runbooks/serverless-demo.md`.
Cost model: `docs/deployment/serverless-costs.md`.
Cutover and rollback: `docs/runbooks/serverless-cutover.md`.

## Release sequence

Deployments are driven by `.github/workflows/deploy.yml`, which builds the three
Lambda images, publishes the Next.js static assets to the private S3 origin, runs
`infra/scripts/deploy-serverless.sh`, and smoke-tests the public URL.

Manually, the same script performs, in order:

1. Create any missing secrets in SSM Parameter Store under `/tradeflow/demo/*`
   (create-only; existing values are never overwritten).
2. Provision or re-apply the Aurora express cluster
   (`infra/scripts/provision-aurora.sh`), including `MinCapacity: 0`,
   `SecondsUntilAutoPause: 300` and deletion protection.
3. Deploy the `tradeflow-demo-data` stack (S3, DynamoDB, SQS + DLQ).
4. Deploy the `tradeflow-demo-app` stack (Lambdas, HTTP API, CloudFront).
5. Apply runtime configuration from Parameter Store to all four functions.
6. Create the `tradeflow_demo` database, then run migrations as an explicit step.
7. Rebuild the demo dataset if the coordination record is empty.
8. Verify the public endpoints.

## Demo refresh

There is no timer. The API reads a DynamoDB coordination record on every `/v1/`
request; when `next_reset_at` has passed it takes a single-flight lock (15-minute
expiry) and queues a rebuild. The worker truncates the demo database, re-seeds it
by driving the real API in-process, validates the seed contract, and marks the
demo ready with a new 45-minute deadline. While a rebuild runs, `/v1/*` returns
`503 evaluation_refreshing` and the console shows a "Preparing the demo" overlay;
`/v1/demo/state` and `/api/demo/status` stay readable so preparation, readiness
and genuine failure are distinguishable.

Because the trigger is activity, an idle demo performs no work and Aurora scales
to zero. An expired demo is rebuilt on the next visit.

## Provider controls

- A tag-filtered AWS Budget (`tradeflow-demo-budget`) alerts on demo spend at 80%
  of actual and 100% of forecasted. Budget alerts are notifications, not hard
  spending caps.
- CloudWatch log groups for all four functions retain 7 days.
- The database is reachable over the internet access gateway; access requires a
  short-lived IAM token, and the `rds-db:connect` permission is scoped to the
  cluster resource id.
- Forward OTLP telemetry and application errors to the approved provider; never
  include request authorization headers.
- Backups are not the recovery mechanism for the demo: backup retention is capped
  at one day on this plan and the dataset is disposable and re-seeded on demand.
- Keep DNS and TLS at the provider edge where a custom domain is used.
