# Serverless demo cost model

Cost model for the serverless form of the public demo in `ap-southeast-2`,
account `527673188999`. Prices are USD list prices from the AWS Price List Query
API for that region. The express-configuration stack is provisioned, but this
model was written before provisioning and has not been re-measured; every
serverless total here is an estimate. See "What is observed vs estimated" below
before quoting any number.

## Validation update (2026-09-14)

The historical planning totals below are retained: approximately **$0.11 idle,
$1.17 for ten sessions, and $3.20 for daily sessions**. They are not measured
monthly totals and are incomplete as all-in account totals. The rollback EC2
host remains running and continues to incur its own costs.

Current observations are stored in `docs/validation/`:

- PostgreSQL reported 17,990,899 bytes after the live delivery validation.
- CloudWatch contains actual 0 ACU datapoints, followed by capacity between
  0.5 and 2 ACU during validation. This verifies that pausing can occur;
  a controlled idle-and-wake result is recorded separately.
- The three ECR repositories contain image manifests whose reported sizes sum
  to 7,334,942,968 bytes. This is an **upper-bound inventory sum**, not billed
  storage: manifests can reference shared layers and image indexes. The old
  model omitted ECR. At the published $0.10/GB-month storage rate this sum
  represents roughly $0.73/month before deduplication or credits; actual ECR
  billing must be reconciled separately. No rollback images were deleted.
- The 1 GB Aurora storage row is a conservative modelling allowance based on
  the plan cap, not a measurement of occupied or billed storage. Database size
  is also not identical to Aurora's billed cluster volume.
- The ten-session API row has a historical arithmetic error: 10,000 requests
  at $1.29/million is $0.0129 (about $0.01), not $0.03. With the other rounded
  rows unchanged, that scenario sums to about $1.15 before ECR. The retained
  $1.17 figure is therefore a rough original planning figure only.

See [Amazon ECR pricing](https://aws.amazon.com/ecr/pricing/) for the ECR rate.
Credits and shared account allowances are not assumed to make these resources
free. No sub-$12 all-in monthly total has been measured.

## How the meter actually runs

Three facts drive almost every total:

- Aurora Serverless v2 bills per ACU-hour. The cluster is configured with a
  minimum of 0 ACU and a maximum of 2 ACU, `SecondsUntilAutoPause` 300, so a
  paused cluster costs $0 in instance compute and still bills storage. Any open
  user-initiated connection prevents pause regardless of whether it is running
  SQL.
- There is no timer. The reset is triggered by activity: the API middleware
  reads the DynamoDB coordination record on every `/v1/` request and queues a
  `demo_reset` when `next_reset_at` has passed. An idle demo therefore costs
  nothing to run, and cost is proportional to how often someone uses it.
- There is no VPC, no NAT gateway and no interface endpoint. The Lambdas reach
  Aurora through the Aurora internet access gateway, authenticating with a
  short-lived IAM token over TLS, so there is no networking line item and no
  database password to store. The free plan caps the cluster at 1 GB of storage.

## Assumptions

Shared by all three scenarios unless stated otherwise.

| Assumption | Value | Source |
| --- | --- | --- |
| Currency and region | USD, `ap-southeast-2` | AWS Price List Query API |
| Aurora engine | Aurora PostgreSQL 17.7 express configuration, Serverless v2, 0-2 ACU, `SecondsUntilAutoPause` 300 | Confirmed set on `tradeflow-demo-aurora` |
| ACU while awake | 0.5 ACU | **Assumption.** Serverless v2 does not scale below its smallest non-zero step while awake; not measured. |
| Reseed time and peak | 2 min at 2 ACU per reset | **Assumption.** The worker timeout is 900 s, not a measurement of seed duration. |
| Awake after last connection | 5 min | `SecondsUntilAutoPause` 300 s |
| Session definition | 30 min of browser interaction | **Assumption.** |
| Reset frequency | One reset per session | A session follows more than 45 idle minutes, so `next_reset_at` has passed. |
| Cluster storage | 1 GB in all scenarios — the free-plan cap | **Cap, observed.** The plan allows 1 GB per cluster. The seeded dataset measured 17 MB across 144 tables, so the cap is not approached. |
| Backup storage | Within 100% of the cluster size | Backup retention is capped at 1 day on this plan, so backups stay inside the free allowance and are not billed. |
| Database credentials | IAM authentication only; a token is minted per connection | There is no master password and no stored database secret. |
| Timed jobs | None | The Redis/ARQ worker was deleted; there is no polling loop and no scheduled EventBridge rule. |
| Free tiers | Account `527673188999` has no other consumer of the Lambda, SQS, DynamoDB, or CloudFront free tiers | **Assumption.** Free tiers are per account per month. |
| API Gateway | No free tier is recorded for HTTP APIs in the price data used here; treated as billed from the first request | Price list |

Per session, Aurora awake time works out to:

```
35 min awake at 0.5 ACU (30 min session + 5 min pause delay) = 0.5833 h x 0.5 ACU = 0.2917 ACU-h
 2 min reseed at 2.0 ACU                                     = 0.0333 h x 2.0 ACU = 0.0667 ACU-h
                                                                           per session = 0.3583 ACU-h
                                                                  at $0.20/ACU-h = $0.0717
```

## Scenario A: fully idle month

Nobody loads the demo for 30 days. No `/v1/` request is made, so no reset
fires, the cluster stays paused, and no Lambda is invoked. Only storage is
billed.

| Line item | Basis | Arithmetic | Cost |
| --- | --- | --- | --- |
| Aurora compute | 0 ACU-h | 0 x $0.20 | $0.00 |
| Aurora storage | 1 GB (the free-plan cap) | 1 GB x $0.11 | $0.11 |
| Aurora I/O | No SQL executed | 0 x $0.22/M | $0.00 |
| Aurora backup | Within 100% of cluster size, 1-day retention | - | $0.00 |
| Lambda requests | 0 | Inside the 1M/month free tier | $0.00 |
| Lambda duration | 0 GB-s | Inside the 400,000 GB-s free tier | $0.00 |
| API Gateway | 0 requests | 0 x $1.29/M | $0.00 |
| SQS | 0 requests | Inside the 1M/month free tier | $0.00 |
| DynamoDB | 0 requests, under 25 GB | Free | $0.00 |
| S3 | 0.1 GB web assets | 0.1 GB x $0.025 | $0.00 |
| CloudFront | 0 requests, 0 GB | Inside the 1 TB / 10M request free tier | $0.00 |
| CloudWatch Logs | No invocations | 0 x $0.67/GB | $0.00 |
| SSM Parameter Store | 3 standard parameters (no database password) | Standard parameters and standard-throughput API calls are free | $0.00 |
| **Total** | | | **$0.11** |

Range: **$0.10-$0.20**, driven by any stray request that wakes Aurora (one
wake-up is at least 5 min at 0.5 ACU, about $0.01, plus I/O). A monitor aimed at
the web origin does not touch the database; a monitor that polls an API `/v1/`
path does, and would also trigger a reset once `next_reset_at` had passed.

## Scenario B: occasional demos, about 10 sessions per month

Same session shape, ten times in the month.

| Line item | Basis | Arithmetic | Cost |
| --- | --- | --- | --- |
| Aurora compute | 3.58 ACU-h | 10 x 0.3583 ACU-h x $0.20 | $0.72 |
| Aurora storage | 1 GB (the free-plan cap) | 1 GB x $0.11 | $0.11 |
| Aurora I/O | 1.0M requests | 10 x 100,000 x $0.22/M | $0.22 |
| Aurora backup | Within 100% of cluster size, 1-day retention | - | $0.00 |
| Lambda requests | 13,500 (10,000 API + 3,000 web + 500 worker) | 0.0135M x $0.20 = $0.003, inside the 1M free tier | $0.00 |
| Lambda duration | 5,800 GB-s | 10,000 x 0.3 s + 3,000 x 0.2 s + 500 x 2 s + 10 x 120 s at 1 GB, inside the 400,000 GB-s free tier | $0.00 |
| API Gateway | 10,000 requests | 0.01M x $1.29 | $0.03 |
| SQS | 500 job notifications | Inside the 1M/month free tier | $0.00 |
| DynamoDB | 10,000 reads, about 200 writes, under 25 GB | 0.01M x $0.1425 + 0.0002M x $0.71 | $0.00 |
| S3 storage | 0.1 GB assets + 0.5 GB documents + markers | 0.6 GB x $0.025 | $0.02 |
| S3 requests | 5,000 PUT + 5,000 GET | 0.005M x $5.50 + 0.005M x $0.44 | $0.03 |
| CloudFront | 3,000 requests, 0.3 GB | Inside the 1 TB / 10M free tier | $0.00 |
| CloudWatch Logs | 50 MB ingested, 7-day retention | 0.05 GB x $0.67 + 0.05 GB x $0.033 | $0.04 |
| SSM Parameter Store | 3 standard parameters (no database password) | Free | $0.00 |
| **Total** | | | **$1.17** |

Range: **$1-$3**. Aurora I/O and CloudWatch Logs are the two line items most
sensitive to assumptions; both scale linearly with session count.

## Scenario C: sustained daily use, one 30-minute session per day

Thirty sessions in the month.

| Line item | Basis | Arithmetic | Cost |
| --- | --- | --- | --- |
| Aurora compute | 10.75 ACU-h | 30 x 0.3583 ACU-h x $0.20 | $2.15 |
| Aurora storage | 1 GB (the free-plan cap) | 1 GB x $0.11 | $0.11 |
| Aurora I/O | 3.0M requests | 30 x 100,000 x $0.22/M | $0.66 |
| Aurora backup | Within 100% of cluster size, 1-day retention | - | $0.00 |
| Lambda requests | 40,500 (30,000 API + 9,000 web + 1,500 worker) | 0.0405M x $0.20 = $0.008, inside the 1M free tier | $0.00 |
| Lambda duration | 17,400 GB-s | 30,000 x 0.3 s + 9,000 x 0.2 s + 1,500 x 2 s + 30 x 120 s at 1 GB, about 4% of the 400,000 GB-s free tier | $0.00 |
| API Gateway | 30,000 requests | 0.03M x $1.29 | $0.04 |
| SQS | 1,500 job notifications | Inside the 1M/month free tier | $0.00 |
| DynamoDB | 30,000 reads, about 600 writes, under 25 GB | 0.03M x $0.1425 + 0.0006M x $0.71 = $0.005, rounded down | $0.00 |
| S3 storage | 0.1 GB assets + 1.5 GB documents | 1.6 GB x $0.025 | $0.04 |
| S3 requests | 15,000 PUT + 15,000 GET | 0.015M x $5.50 + 0.015M x $0.44 | $0.09 |
| CloudFront | 9,000 requests, 0.9 GB | Inside the 1 TB / 10M free tier | $0.00 |
| CloudWatch Logs | 150 MB ingested, 7-day retention | 0.15 GB x $0.67 + 0.15 GB x $0.033 | $0.11 |
| SSM Parameter Store | 3 standard parameters (no database password) | Free | $0.00 |
| **Total** | | | **$3.20** |

A previously published $3.31 total for this scenario assumed 2 GB of cluster
storage; the free plan caps a cluster at 1 GB, so that line is $0.11 and the
corrected total is about $3.20.

Range: **$2-$6**. The totals here all sit far below the measured EC2 demo, which
ran at about $24/month (about $18.11 for roughly 13 days of September, entirely
offset by credits).

## Sensitivity: the pause is the whole model

Aurora compute is linear in awake time, so the largest risk is the cluster never
pausing. If something holds a connection open for the full month at the 0.5 ACU
awake floor:

```
730 h x 0.5 ACU x $0.20/ACU-h = $73.00
```

over 20x the scenario C total. This is why the API uses SQLAlchemy `NullPool` in
Lambda, and why RDS Proxy is deliberately not used: a proxy holds a connection
open and prevents auto-pause (and would require the VPC this deployment does not
have). Treat a non-zero `ServerlessDatabaseCapacity` average across an idle
period as an incident, not a slow month.

## What is deliberately not paid for

| Avoided resource | List price | Why it is not needed |
| --- | --- | --- |
| NAT gateway | $0.059/h plus $0.059/GB, about $43/month | There is no VPC at all. The Lambdas have ordinary internet egress and reach Aurora through the Aurora internet access gateway. |
| SQS interface endpoint | $0.013 per AZ-hour, about $9.49/month per AZ | Not needed for the same reason: with no VPC there is nothing to attach an endpoint to. |
| RDS Proxy | Not priced in this model | Holds a connection open and prevents auto-pause, and it requires a VPC. |
| Secrets Manager | $0.40/secret/month | There is no database credential to store; the three demo parameters live in SSM Parameter Store, whose standard parameters and standard-throughput API calls are free. |
| Application Load Balancer | $0.0252/h plus LCUs, about $18.40/month before LCUs | CloudFront plus the web Lambda Function URL terminates the public traffic. |
| Fixed EC2 host | Measured about $24/month run rate | Replaced by request-metered compute. |

Even with all three scenarios, the avoided fixed cost is larger than the
estimated serverless total.

## What is observed vs estimated

**Observed** (measured on the current EC2 demo before migration, confirmed
against the provisioned resources, and from list pricing):

- The EC2 host is a t3.small: 2 vCPU, root disk 96% full with 313 MB free,
  1.18 GB of 1.9 GB RAM used, load average about 0.2, CPU about 6%.
- Cost was about $18.11 for roughly 13 days of September, about a $24/month run
  rate, entirely offset by credits.
- The Redis/ARQ worker polled two PostgreSQL outboxes every 15 seconds, and the
  demo reset ran every 45 minutes including while the demo was idle. Those two
  behaviours are what the serverless design removes: no polling loop, no timer.
- The seeded dataset measured 17 MB across 144 tables, well inside the 1 GB cap.
- The scale-to-zero configuration is confirmed set on `tradeflow-demo-aurora`:
  `MinCapacity` 0, `MaxCapacity` 2, `SecondsUntilAutoPause` 300, with deletion
  protection enabled and IAM authentication only.
- All unit prices quoted here are list prices retrieved from the AWS Price List
  Query API for `ap-southeast-2`.

**Estimated**: everything else about the serverless stack.

- This cost model was not re-measured after provisioning. No serverless figure
  here has been reconciled against Cost Explorer or the Cost and Usage Report;
  the totals remain estimates.
- The ACU awake floor, reseed duration and ACU peak, session length, request
  counts, Aurora I/O request counts, S3 object and transfer volumes, CloudWatch
  Logs volume, storage growth, and the assumption that no other workload in the
  account consumes the free tiers are all assumptions, not measurements.
- Free tiers are modelled as absorbing the demo's CloudFront, SQS, Lambda
  request, and Lambda duration usage. That is a statement about the demo's own
  traffic shape, not a guarantee: a scraper or a misconfigured monitor can move
  any of those line items off zero.
- The three totals should be read as a plausible monthly range of roughly
  **$0.10-$0.20 idle, $1-$3 for occasional demos, and $2-$6 for daily use**, not
  as forecasts.

**Budget alerts are notifications, not spending caps.** The `tradeflow-demo-budget`
stack deploys a tag-filtered monthly budget ($10/month, with an 80% actual and a
100% forecasted email alert), but neither CloudFormation nor the application
rejects traffic at a spend threshold. The only structural ceilings in the stack
are the HTTP API throttle (burst 50 / rate 25), the account's 10-execution
Lambda concurrency quota (which leaves no room to reserve concurrency on any
function), and the worker's `MaximumConcurrency: 2`; these bound request rate
and concurrent compute, not monthly spend. Any cost alert should be answered by
checking `ServerlessDatabaseCapacity` and the worker log volume first, because a
cluster that fails to pause dominates every other line item.
