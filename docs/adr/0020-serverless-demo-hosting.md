# ADR-0020: Host the public demo on serverless AWS

- Status: Accepted
- Date: 2026-09-13

## Context

The public demo runs on one EC2 instance (`i-0dbb59b359b95f12c`, Elastic IP
`52.64.5.66`) running docker compose: Next.js web, FastAPI API, PostgreSQL,
Redis/ARQ worker, MinIO, Caddy, and a 45-minute reset loop. The measured
baseline is a t3.small with 2 vCPU, the root disk 96% full with 313 MB free,
1.18 GB of 1.9 GB RAM used, load average about 0.2, CPU about 6%, and about
$18.11 for roughly 13 days of September (about a $24/month run rate, entirely
offset by credits).

Two of those behaviours cost money and load with zero visitors: the worker
polled two PostgreSQL outboxes every 15 seconds, and the demo reset ran every
45 minutes including while the demo was idle. The demo is recruiter-facing and
mostly idle, so a fixed 24/7 host is the wrong shape for the traffic. The box is
also a single point of failure that needs patching, and its disk is nearly full.

Constraints: keep the running application as close to unchanged as possible and
keep PostgreSQL, because the ERP's invariants are relational; drive idle cost
toward zero without giving up the demo; keep secrets out of the repository and
out of templates; deploy from the existing GitHub identity with no long-lived
AWS keys. Target is `ap-southeast-2`, account `527673188999`.

Account `527673188999` is also on the AWS **free plan**, which turned out to be
the decisive constraint. The first attempt to create the Aurora cluster for a
private-VPC design failed with:

```text
To use Aurora clusters with free plan accounts you need to set WithExpressConfiguration.
```

On that plan only express-configuration clusters are supported; full
configuration is not available. Per the Aurora documentation, express
configuration allows up to 4 ACU and 1 GB of storage per cluster, with at most 2
clusters and 2 instances per account. An express cluster cannot be associated
with a VPC, is reachable only through the Aurora internet access gateway, uses
IAM authentication only (there is no master password and it cannot be disabled),
does not allow selecting an engine version, and can only be created through the
API/CLI: CloudFormation does not expose `WithExpressConfiguration`.

That invalidates the requirement that the database stay private. The
private-VPC design was built first and is preserved, but it is not what runs;
this ADR records the outcome honestly rather than dropping the requirement.

## Decision

Replace the single host with a serverless stack in account `527673188999`,
described by the CloudFormation templates under `infra/cloudformation/` and
deployed by `infra/scripts/deploy-serverless.sh` with `aws cloudformation
deploy`. The cluster itself is the one component that is not in a stack.

- **Database.** Aurora PostgreSQL **17.7** express-configuration cluster
  `tradeflow-demo-aurora`
  (`tradeflow-demo-aurora.cluster-c9m6sguycrg7.ap-southeast-2.rds.amazonaws.com`,
  port 5432, resource id `cluster-OU3EFK4P5KDUKJPSOJLGDFC4NA`, master user
  `tradeflow`). Serverless v2 with `MinCapacity` 0, `MaxCapacity` 2 and
  `SecondsUntilAutoPause` 300, all confirmed set; deletion protection enabled;
  IAM authentication only. It is created and owned by
  `infra/scripts/provision-aurora.sh`, which is idempotent: it creates the
  cluster if missing, otherwise re-applies the scale-to-zero settings and
  deletion protection after waiting for the cluster to leave
  `modifying`/`backing-up`. The cluster is script-owned because CloudFormation
  cannot express express configuration.
- **Network.** None for the database or the Lambdas. An express cluster cannot
  be associated with a VPC, so the API, worker and migration functions have
  ordinary internet egress and reach the cluster through the Aurora internet
  access gateway. `infra/cloudformation/network.yaml` — two private subnets,
  free S3 and DynamoDB gateway endpoints, no NAT gateway and no internet
  gateway — is the private-VPC design that was built first. It is preserved for
  the day the account moves off the free plan, but
  `deploy-serverless.sh` does not deploy it, and the `tradeflow-demo-network`
  stack is unused.
- **Compute.** API, worker, migration, and web Lambdas, none of them in a VPC.
  The web Lambda runs the unmodified Next.js 16.2.12 standalone server under the
  AWS Lambda Web Adapter 1.0.1. API Gateway HTTP API `$default` route in front
  of the API; CloudFront in front of the web, with the S3 bucket as the origin
  for `/_next/static/*` and `/product/*` and the web Function URL (`AuthType:
  NONE`) as the default origin. Both Function URL invocation permissions are
  explicit. CloudFront uses CachingDisabled and AllViewerExceptHostHeader,
  preserving cookies while using the origin hostname required by Lambda.
  Direct origin access is accepted for this public demo; CloudFront is not an
  exclusive access boundary. The earlier 403 had two independently verified
  causes: a missing invocation permission and forwarding the viewer Host.
  OAC was not revalidated after correcting those causes, so it is not claimed
  to be unsupported. S3 still uses OAC and remains private.
- **Database connectivity.** No VPC and no stored password.
  `apps/api/src/tradeflow_api/database.py` defines an `IamAuthTokenProvider`
  that is passed to asyncpg as the `password` callable, so a fresh RDS IAM auth
  token (15-minute lifetime) is minted for every new connection, with
  `ssl="require"`. It is enabled by `TRADEFLOW_DB_IAM_AUTH=true` on the API and
  migration functions and `TRADEFLOW_WORKER_DB_IAM_AUTH=true` on the worker.
  `apps/api/migrations/env.py` uses the same engine factory, so Alembic
  authenticates identically. The IAM permission is `rds-db:connect` on
  `arn:aws:rds-db:<region>:<account>:dbuser:cluster-OU3EFK4P5KDUKJPSOJLGDFC4NA/tradeflow`,
  granted to the API, worker and migration roles.
- **Initial database and schema.** Express configuration cannot create an
  initial database, so `tradeflow_demo` does not exist at cluster creation. The
  deployment invokes the migration Lambda with
  `{"action":"create-database","name":"tradeflow_demo"}`, then
  `{"action":"upgrade","revision":"head"}`. Migrations were verified to apply
  (revision `0024`).
- **Jobs.** The API commits business state and its `outbox_events` row in one
  transaction, then writes a small JSON marker under `jobs/`; the bucket
  notification publishes to SQS, which triggers the worker. Handlers record
  `outbox_handler_receipts` rows, so redelivery is a no-op, and markers are keyed
  by event id and handler group, so re-publishing is an idempotent overwrite that
  re-fires the notification. Poison messages reach the DLQ after 5 receives.
  With the Lambdas outside a VPC, publishing straight to SQS would also work;
  the S3 marker path was kept deliberately because it is a durable, replayable
  dispatch record.
- **Demo reset.** No timer. Coordination lives in DynamoDB; the API middleware
  reads it on every `/v1/` request and queues a reset when `next_reset_at` has
  passed. The worker takes a single-flight DynamoDB lock with a 15-minute expiry
  plus a PostgreSQL advisory lock, truncates, re-seeds by driving the real API
  in-process over loopback (reusing `scripts/seed_demo.py` unchanged), validates
  the seed contract, and sets `next_reset_at = now + 45 minutes`. While the state
  is not ready, `/v1/` returns 503.
- **Credentials.** The web tier mints a short-lived HS256 demo token on demand
  from the shared signing secret, so no stored two-hour token expires during a
  long idle gap, and the web Lambda needs no AWS SDK. The demo secrets
  (`reset-token`, `auth-test-secret`, `auth-issuer`) live in SSM Parameter Store
  and are applied to the Lambda environment by the deploy script. There is no
  database password to store: the cluster authenticates with a per-connection
  IAM token.
- **Pipeline.** `.github/workflows/deploy.yml` builds three images from
  `apps/*/Dockerfile.lambda`, syncs the static assets to the private S3 bucket,
  runs the deploy script, and smoke-tests CloudFront. The images are built on
  `public.ecr.aws/lambda/python:3.13` (a plain Python base image fails with
  `Runtime.InvalidEntrypoint`), copying the uv venv's site-packages into
  `/var/lang/lib/python3.13/site-packages` and adding a `.pth` that appends
  `/var/task` (a custom `PYTHONPATH` is ignored by the runtime). The build runs
  `chmod -R a+rX /var/task` because the runtime is non-root. The deploy role is
  the GitHub OIDC role `tradeflow-demo-deploy`, temporarily assumable from
  `repo:Kagerrak@*/tradeflow-erp@*:ref:refs/heads/main` and
  `repo:Kagerrak@*/tradeflow-erp@*:ref:refs/heads/feat/serverless-aws-demo`.
  Narrow trust to main after this branch merges.

The express cluster and the `tradeflow-demo-ci`, `tradeflow-demo-data`,
`tradeflow-demo-app` and `tradeflow-demo-budget` stacks are provisioned; the
demo answers on `dt7yjmo5ppcxs.cloudfront.net`. The EC2 deployment remains the
rollback target throughout cutover; see
`docs/runbooks/serverless-cutover.md`.

## Alternatives considered

- **Stay on EC2 with docker compose.** Rejected. The box bills 24/7 while the
  demo is mostly idle, and the polling worker and 45-minute reset run whether or
  not anyone is visiting. It is also a single point of failure with a nearly
  full root disk and a patching burden that a demo does not justify.
- **A private-VPC Aurora cluster (the original design).** Rejected by the
  account plan, not by preference. Creating a fully configured cluster on a
  free-plan account fails with the `WithExpressConfiguration` error quoted
  above, and an express cluster cannot be attached to a VPC at all. The
  VPC/private-subnet design in `infra/cloudformation/network.yaml` is kept as
  the target for when the account leaves the free plan.
- **A script-owned cluster outside CloudFormation.** Chosen. CloudFormation
  does not expose `WithExpressConfiguration`, so no template can create the
  cluster the plan permits. `infra/scripts/provision-aurora.sh` owns it and is
  idempotent; every other resource stays in a stack.
- **Vercel or Amplify Hosting for Next.js.** Rejected. Both host the front end
  only. The demo's API, worker, database, and document storage would still need
  a home, so the idle database cost and the backend operational surface would
  remain, and the demo would be split across two providers with separate
  secrets, logs, and deploy paths.
- **OpenNext (`@opennextjs/aws` 4.1.5) for the Next.js server.** Rejected. Its
  peer dependency range is `>=15.5.24 <16 || >=16.3.3` and the app is on Next
  16.2.12, so adopting it would force a framework upgrade plus an unverified
  `next/og` route. The Lambda Web Adapter instead runs the app's own standalone
  server unchanged, which keeps the web tier on the same Next.js version as
  local development.
- **RDS Proxy.** Rejected. It holds a connection open, and any open
  user-initiated connection prevents Aurora auto-pause regardless of whether it
  is running SQL. It would remove the property the design depends on, and it
  would require the VPC this deployment does not have.
- **RDS Data API.** Rejected. It is an HTTP API and cannot drive
  SQLAlchemy/asyncpg, so it is not a drop-in for the application's data layer.
- **NAT gateway or an SQS interface endpoint.** Rejected, and moot. There is no
  VPC: the Lambdas have normal internet egress and reach the cluster through the
  Aurora internet access gateway, so neither a NAT gateway (about $43/month) nor
  an interface endpoint (about $9.49/month per AZ) has anything to attach to.
- **Scheduled EventBridge reset.** Rejected. It re-creates the idle cost the
  migration removes: a timer would wake Aurora every 45 minutes around the
  clock, including when nobody is looking. The activity-gated trigger means a
  fully idle month costs storage only.
- **DynamoDB instead of PostgreSQL.** Rejected. The ERP's invariants are
  relational: multi-table transactions, posted-ledger projections, Alembic
  migrations, and advisory locks for the reset. The seeder drives the real
  application API, so replacing the database would mean rewriting the
  application rather than the hosting.
- **Secrets Manager instead of SSM Parameter Store.** Rejected on cost:
  $0.40/secret/month against standard SSM parameters, which are free. There is
  no database credential to store at all now, so only the three demo secrets are
  parameterised.

## Consequences

- **The private-database requirement is not met.** The cluster cannot be
  associated with a VPC, so it is reachable over the internet through the Aurora
  internet access gateway. Access is guarded by IAM authentication and TLS
  (`ssl="require"`); there is no password to leak because there is no password.
  The private-VPC design survives only in `infra/cloudformation/network.yaml`,
  which is not deployed. Any statement that the demo database is private is
  false and should not be repeated.
- **The free plan bounds the design, not just the bill.** Express configuration
  only (no full configuration), up to 4 ACU, 1 GB of storage per cluster, at
  most 2 clusters and 2 instances per account; IAM authentication only and it
  cannot be disabled; no engine-version choice (the cluster is on 17.7); and the
  cluster cannot be created by CloudFormation.
- **Lambda concurrency cannot be reserved.** The account quota is 10 concurrent
  executions with a minimum of 10 unreserved, so no function can hold a reserved
  concurrency. The concurrency bound is the account quota itself, plus API
  Gateway throttling and `ScalingConfig.MaximumConcurrency: 2` on the worker's
  SQS event source.
- **Aurora backup retention is capped at 1 day** on this plan; a 7-day request
  was rejected. Backups are a convenience, not the recovery mechanism: the demo
  dataset is rebuilt by the seeder. The seeded dataset measured 17 MB across 144
  tables, well inside the 1 GB storage cap.
- Idle cost falls to Aurora storage. Compute, requests, and logs are billed only
  when someone visits, and there is no polling loop and no timer.
- The pause is the whole cost model. Anything that holds a connection open —
  including the rejected RDS Proxy — would take the monthly cost from storage
  only to roughly $73 at the 0.5 ACU awake floor.
- Every request path must tolerate a paused cluster: the first request can
  return 504 and resume takes about 15 s (30 s or more after a pause longer than
  24 hours). The shared retry wrapper is bounded to 20 s per attempt and 3
  attempts, and only retries safe methods or requests carrying an
  `Idempotency-Key`. The API Gateway integration timeout is 30 s, so no single
  request can outlast that; longer work belongs on the queue.
- Nobody can read a partially reset demo: `/v1/` returns 503 while the state is
  not ready, and the console shows a preparation overlay that distinguishes
  preparation, readiness, and genuine failure.
- The Lambdas have ordinary internet egress. The trust boundary is IAM
  authentication, TLS, and the cluster's own authorization rather than network
  placement; a compromised function is not contained by a VPC.
- The Aurora writer is a single `db.serverless` instance with no reader and no
  failover target, so the demo database is a single-AZ deployment.
- Poison messages and slow rebuilds are visible operational surfaces: a rebuild
  that exceeds the worker's 900 s timeout fails and lands in the DLQ after 5
  receives, and log retention is 7 days.
- Four stacks, a script-owned cluster, an SQS DLQ, and a DynamoDB coordination
  table replace one compose file; the runbook and the DLQ redrive procedure are
  part of the operating cost of this decision.
- The cost model was not re-measured after provisioning, so its totals remain
  estimates. The 17 MB / 144-table dataset and the confirmed scale-to-zero
  configuration (`MinCapacity` 0, `MaxCapacity` 2, `SecondsUntilAutoPause` 300)
  are observed facts, not estimates.
