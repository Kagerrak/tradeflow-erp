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

## Decision

Replace the single host with a serverless stack described by four CloudFormation
templates under `infra/cloudformation/`, deployed by
`infra/scripts/deploy-serverless.sh` with `aws cloudformation deploy`.

- **Database.** Aurora PostgreSQL Serverless v2 `tradeflow-demo-aurora`
  (engine 17.9, min 0 ACU, max 2 ACU, `SecondsUntilAutoPause` 300, deletion
  protection on). The API uses SQLAlchemy `NullPool` in Lambda so no idle
  connection can hold the cluster awake.
- **Network.** A VPC with two private subnets only, no NAT gateway and no
  internet gateway, and free S3 and DynamoDB gateway endpoints. The database
  stays private; the VPC Lambdas reach exactly the AWS services they need.
- **Compute.** API, worker, and migration Lambdas in the VPC; the web Lambda
  outside it, running the unmodified Next.js 16.2.12 standalone server under the
  AWS Lambda Web Adapter 1.0.1. API Gateway HTTP API `$default` route in front
  of the API, CloudFront in front of the web with the S3 bucket as the origin
  for `/_next/static/*` and `/product/*` and the web Function URL (`AuthType:
  AWS_IAM`, OAC) as the default origin.
- **Jobs.** The API commits business state and its `outbox_events` row in one
  transaction, then writes an S3 marker under `jobs/`; the bucket notification
  publishes to SQS, which triggers the worker. Handlers record
  `outbox_handler_receipts` rows, so redelivery is a no-op, and markers are keyed
  by event id and handler group, so re-publishing is an idempotent overwrite that
  re-fires the notification. Poison messages reach the DLQ after 5 receives.
- **Demo reset.** No timer. Coordination lives in DynamoDB; the API middleware
  reads it on every `/v1/` request and queues a reset when `next_reset_at` has
  passed. The worker takes a single-flight DynamoDB lock with a 15-minute expiry
  plus a PostgreSQL advisory lock, truncates, re-seeds by driving the real API
  in-process over loopback (reusing `scripts/seed_demo.py` unchanged), validates
  the seed contract, and sets `next_reset_at = now + 45 minutes`. While the state
  is not ready, `/v1/` returns 503.
- **Credentials.** The web tier mints a short-lived HS256 demo token on demand
  from the shared signing secret, so no stored two-hour token expires during a
  long idle gap, and the web Lambda needs no AWS SDK. Secrets live in SSM
  Parameter Store and are applied to the Lambda environment by the deploy script.
- **Pipeline.** `.github/workflows/deploy.yml` builds three images from
  `apps/*/Dockerfile.lambda`, syncs the static assets to the private S3 bucket,
  runs the deploy script, and smoke-tests CloudFront. The deploy role is the
  GitHub OIDC role `tradeflow-demo-deploy`, assumable only from
  `repo:Kagerrak/tradeflow-erp:ref:refs/heads/main`.

The stack is implemented but not provisioned. The EC2 deployment keeps serving
until cutover is approved; see `docs/runbooks/serverless-cutover.md`.

## Alternatives considered

- **Stay on EC2 with docker compose.** Rejected. The box bills 24/7 while the
  demo is mostly idle, and the polling worker and 45-minute reset run whether or
  not anyone is visiting. It is also a single point of failure with a nearly
  full root disk and a patching burden that a demo does not justify.
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
  is running SQL. It would remove the property the design depends on.
- **RDS Data API.** Rejected. It is an HTTP API and cannot drive
  SQLAlchemy/asyncpg, so it is not a drop-in for the application's data layer.
  The cluster is created with `EnableHttpEndpoint: false`.
- **NAT gateway or an SQS interface endpoint.** Rejected. The only egress the
  VPC Lambdas need is S3 and DynamoDB, both reachable through free gateway
  endpoints; the job flow works because the S3 marker write is what publishes
  the SQS notification. A NAT gateway would cost about $43/month and an SQS
  interface endpoint about $9.49/month per AZ, for no functional gain.
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
  $0.40/secret/month against standard SSM parameters, which are free, and the
  Aurora master password is resolved through an `ssm-secure` dynamic reference
  so no secret is stored in any template or stack.

## Consequences

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
- The VPC Lambdas have no internet egress. Any future dependency that needs the
  public internet requires an explicit network decision, not a code change.
- The Aurora writer is a single `db.serverless` instance with no reader and no
  failover target, so the demo database is a single-AZ deployment.
- Poison messages and slow rebuilds are now visible operational surfaces: a
  rebuild that exceeds the worker's 900 s timeout fails and lands in the DLQ
  after 5 receives, and log retention is 7 days.
- Four stacks, an SQS DLQ, and a DynamoDB coordination table replace one
  compose file; the runbook and the DLQ redrive procedure are part of the
  operating cost of this decision.
- All serverless cost figures remain estimates until the stack is provisioned
  and measured; the EC2 baseline in `docs/deployment/serverless-costs.md` is the
  only observed number.
