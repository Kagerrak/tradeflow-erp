# Serverless demo cutover and rollback

Target: <https://dt7yjmo5ppcxs.cloudfront.net>, account `527673188999`, region
`ap-southeast-2`. Cutover means publishing this URL in the README and deployment
guide after validation. No custom domain or DNS change is required.

The owner authorized provisioning, validation, and cutover on 2026-09-14
(Asia/Manila). There is no additional sign-off gate. **EC2 remains running as
the rollback target**, including instance `i-0dbb59b359b95f12c` and EIP
`52.64.5.66`. Decommissioning is outside this migration.

## Validation checklist

Evidence is recorded in `docs/validation/` and the final migration report.

- [x] App, data, CI, budget, and unused network stacks complete.
- [x] Aurora configured for 0–2 ACU, 300-second auto-pause, IAM authentication,
      deletion protection, and one-day backup retention.
- [x] CloudFront returns 200 for `/`, `/operations`, `/api/health`,
      `/api/demo/status`, `/robots.txt`, and a `/_next/static/` asset.
- [x] Demo status is `ready`; migration handler reports revision `0024`.
- [x] Browser saves a priced Sales Order Draft; authenticated business reads work.
- [x] Delivery confirmation verifies evidence, creates an outbox event, renders
      a downloadable PDF, and produces one draft invoice.
- [x] Duplicate worker delivery preserves handler receipts and invoice count.
- [x] S3 evidence download matches the uploaded SHA-256 digest.
- [x] Separate visitor cookie jars receive distinct, uncached dynamic responses;
      missing and invalid API credentials return 401.
- [ ] Failed dispatch recovers through later activity.
- [ ] Poison message reaches DLQ and is redriven.
- [ ] Concurrent resets and an interrupted reset recover safely.
- [ ] Controlled idle window reaches 0 ACU and subsequent activity wakes Aurora.
- [ ] Cold-start and representative-session measurements recorded.
- [ ] Previous image rollback serves successfully with `SKIP_MIGRATIONS=1`.
- [ ] OIDC GitHub Actions deployment succeeds.
- [ ] README and deployment guide publish the CloudFront URL.
- [ ] Final ready status, queue health, and EC2 rollback health recorded.

The historical CloudWatch sample already contains 0 ACU datapoints. This is
observed capacity, but does not replace the controlled idle-and-wake check.
Monthly cost totals remain estimates, not measured bills.

## Deployment

Build and push all three `linux/amd64` images at one Git SHA, then run:

```bash
AWS_REGION=ap-southeast-2 IMAGE_TAG=<sha> ./infra/scripts/deploy-serverless.sh
```

Aurora express configuration is owned by `infra/scripts/provision-aurora.sh`,
because CloudFormation does not support its creation flag. The data and app
stacks are deployed by the script. CI and the migration-specific budget are
already bootstrapped. Leave the unused network stack intact.

Dynamic CloudFront requests use CachingDisabled and AllViewerExceptHostHeader.
The origin Host must match the Lambda Function URL. The public Function URL
requires both `lambda:InvokeFunctionUrl` and `lambda:InvokeFunction` resource
permissions; the latter is restricted to invocation through the URL. Direct
origin access is accepted for the demo and documented in ADR-0020.

Publish static assets additively. Do not delete previous content-hashed assets
when deploying or rolling back: existing browser sessions may still request
those builds. Never run a root-bucket sync with `--delete` after uploading
`_next/static`, because it erases the static prefix.

## Application rollback

Record the current and previous image digests before deployment. Redeploy a
known-good tag with the same schema compatibility:

```bash
AWS_REGION=ap-southeast-2 IMAGE_TAG=<previous-sha> SKIP_MIGRATIONS=1 \
  ./infra/scripts/deploy-serverless.sh
```

Verify CloudFront health, ready status, a business read, and static assets. The
flag skips schema migrations; it does not restore data. Reusing a mutable tag
does not update Lambda code through CloudFormation: use immutable tags or
explicit `update-function-code` with a digest and wait for each function update.

## Schema recovery

Prefer a forward fix or a demo reseed. Before a necessary downgrade, export the
database and verify that the previous image supports the target schema. IAM
authentication through the Aurora internet access gateway provides an export
path. Do not print the token or enable shell tracing:

```bash
task_db_host=tradeflow-demo-aurora.cluster-c9m6sguycrg7.ap-southeast-2.rds.amazonaws.com
task_db_token="$(aws rds generate-db-auth-token --region ap-southeast-2 \
  --hostname "$task_db_host" --port 5432 --username tradeflow)"
PGPASSWORD="$task_db_token" PGSSLMODE=verify-full PGCONNECT_TIMEOUT=30 \
  pg_dump "host=$task_db_host port=5432 dbname=tradeflow_demo user=tradeflow" \
  --format=custom --file=demo-before-downgrade.dump
unset task_db_token
```

Use a PostgreSQL client compatible with the server version. Protect the export
as application data and keep it out of Git. Tokens expire for new connection
authentication after 15 minutes; an established dump connection does not require
a token refresh. The one-day Aurora backup retention is not a substitute for
this export.

Invoke a downgrade only for the explicitly selected compatible revision:

```bash
aws lambda invoke --region ap-southeast-2 \
  --function-name tradeflow-demo-migration \
  --payload '{"action":"downgrade","revision":"<revision>"}' \
  --cli-binary-format raw-in-base64-out /tmp/tradeflow-downgrade-result.json
```

Check the invocation metadata for `FunctionError`, read the result, and verify
`{"action":"current"}` before serving traffic. No downgrade is needed for the
code rollback validation at revision `0024`.

## URL rollback and protected resources

If serverless cannot serve, republish the EC2 URL in the README and deployment
guide and verify its status endpoint. Leave serverless resources in place for
diagnosis. Record the failure and rollback time in the migration report.

Never terminate the rollback EC2 host, release its EIP, delete retained S3
buckets, delete `tradeflow-demo-aurora`, delete the coordination table or its
retained data, modify existing account budgets, or touch LuckyALPA or
`database-1`. Normal application resets use the existing demo lifecycle; do not
purge the coordination table as a recovery shortcut.

After this branch merges, narrow the OIDC trust from main plus the migration
branch to main only. Until merged, both branches remain necessary for this
migration's deployment validation.
