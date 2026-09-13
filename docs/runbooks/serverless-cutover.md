# Serverless demo cutover

Cutover checklist for moving the public demo from the single EC2 host
(`i-0dbb59b359b95f12c`, Elastic IP `52.64.5.66`, docker compose) to the
serverless stack in `ap-southeast-2`, account `527673188999`. Nothing in this
document has been executed: the serverless stack has not been provisioned yet.

The EC2 host keeps serving until the cutover is approved. `deploy-ec2-legacy.yml`
is manual-only, so the legacy pipeline cannot redeploy the box on a push to
`main`.

## Approvals

Two roles approve, and both must be named in the cutover issue before Phase 2
starts. Names are not recorded in this document.

| Role | Authority |
| --- | --- |
| Demo owner | Approves every step in this checklist, in writing, in the cutover issue. Owns the go/no-go decision and the rollback decision. |
| AWS account owner (account `527673188999`) | Co-approves anything that deletes a billed or retained resource: instance termination, Elastic IP release, volume deletion, S3 bucket deletion, Aurora cluster deletion. |

Rules:

- A destructive step with no written approval does not proceed. The step waits;
  it is not silently skipped and it is not improvised.
- Approval is per step, recorded in the issue, with the approver's name and the
  timestamp.
- Any step that changes the cost profile (the Aurora pause configuration, Lambda
  concurrency, or CloudWatch Logs retention) is treated as destructive for
  approval purposes because it changes what the cost model in
  `docs/deployment/serverless-costs.md` describes.

## Phase 1: Pre-flight (no changes)

- [ ] Open the cutover issue. Record the named demo owner and AWS account owner,
      the migration window, and the rollback decision-maker.
- [ ] Confirm the EC2 demo is healthy and record the baseline:
      `curl -fsS http://52.64.5.66/api/demo/status`.
- [ ] Record the image tag currently running on the box, and the ECR tags
      available for rollback:
      `aws ecr describe-images --region ap-southeast-2 --repository-name tradeflow-demo-api --query 'sort_by(imageDetails,&imagePushedAt)[*].imageTags'`.
- [ ] Confirm the three ECR repositories exist: `tradeflow-demo-api`,
      `tradeflow-demo-worker`, `tradeflow-demo-web`.
- [ ] Confirm the demo dataset on the EC2 box is disposable. The compose database
      is a volume on the instance and re-seeds from the image, so terminating
      the instance is expected to lose nothing. The root disk is at 96% (313 MB
      free): do not add work that writes to the box during the migration.
- [ ] Confirm the GitHub OIDC deploy role is in place (stack `tradeflow-demo-ci`).
      It must exist before GitHub Actions can run the deploy script.
- [ ] Decide and record the final public URL before provisioning. The serverless
      stack's public origin is the CloudFront distribution domain. No custom
      domain or ACM certificate configuration is part of the four templates, so
      retaining the existing demo hostname is **unverified** and must be
      resolved here if the URL must not change.
- [ ] Confirm the SSM parameter path `/tradeflow/demo/*` is empty in the account
      or holds values you intend to reuse. The deploy script creates missing
      parameters and never overwrites existing ones.
- [ ] Set the cost alerts for the serverless stack and record them. Budget alerts
      are notifications, not hard spending caps.
- [ ] Freeze unrelated infrastructure changes for the duration of the cutover.

## Phase 2: Provision (serverless stack only, no user traffic)

The deploy script deploys the `network`, `data`, and `app` stacks in order. The
`ci` stack is a one-time bootstrap deployed directly, because the deploy role
must already exist for GitHub Actions to run the script.

- [ ] Bootstrap the deploy identity (once, if not already deployed):

      ```bash
      aws cloudformation deploy --region ap-southeast-2 \
        --stack-name tradeflow-demo-ci \
        --template-file infra/cloudformation/ci.yaml \
        --capabilities CAPABILITY_NAMED_IAM \
        --parameter-overrides \
          OidcProviderArn=arn:aws:iam::527673188999:oidc-provider/token.actions.githubusercontent.com
      ```

- [ ] Provision the stack:

      ```bash
      AWS_REGION=ap-southeast-2 IMAGE_TAG=<sha> ./infra/scripts/deploy-serverless.sh
      ```

      This creates the SSM parameters, deploys `tradeflow-demo-network`,
      `tradeflow-demo-data`, and `tradeflow-demo-app`, applies the runtime
      secrets to the four Lambda functions, runs migrations as an explicit step,
      queues an initial demo rebuild when the coordination state is empty, and
      verifies the public endpoints.

- [ ] Confirm all stacks are `CREATE_COMPLETE` or `UPDATE_COMPLETE` and that the
      app stack outputs are present (API URL, distribution domain, function
      names).
- [ ] Confirm no secret value appears in any template or stack event, and that
      the Aurora master password is still only an `ssm-secure` dynamic reference.
- [ ] Record the distribution domain, API URL, and image tag in the issue. The
      EC2 stack is untouched and still serving.

## Phase 3: Seed and validate on the CloudFront URL

Validate against the distribution domain, not the Lambda Function URL, so that
CloudFront caching and origin behaviour are exercised.

- [ ] Confirm the initial rebuild: read `pk=demo, sk=state` in
      `tradeflow-demo-coordination` until `status` is `ready`. `refreshing` is
      normal; `failed` is an incident.
- [ ] `curl -fsS https://<distribution-domain>/api/demo/status` and
      `curl -fsS https://<distribution-domain>/api/health`.
- [ ] Run the demo browser journey against the distribution domain:

      ```bash
      PLAYWRIGHT_BASE_URL=https://<distribution-domain> TRADEFLOW_SEEDED_DEMO=1 \
        pnpm --filter @tradeflow/web test:demo
      ```

- [ ] Force a reset and confirm the gating contract: `/v1/` returns 503
      `demo_refreshing` (never partial data), the "Preparing the demo" overlay
      appears, and the state returns to `ready` with `next_reset_at = now + 45
      minutes`.
- [ ] Confirm the activity trigger: idle past `next_reset_at`, then issue one
      `/v1/` request and confirm a `demo_reset` job is queued and consumed.
- [ ] Confirm the cold path: let the cluster pause, then issue the first request
      and confirm it either succeeds after about 15 s or returns 504 and
      succeeds on the bounded retry. Confirm the API Lambda uses `NullPool` and
      that `ServerlessDatabaseCapacity` returns to 0 within about five minutes
      of the last request.
- [ ] Confirm the job flow end to end: a mutating `/v1/` request writes a marker
      under `jobs/`, SQS delivers it, the worker records an
      `outbox_handler_receipts` row, and a redelivery of the same marker is a
      no-op.
- [ ] Confirm document evidence: presigned multipart upload and download through
      the browser.
- [ ] Confirm `tradeflow-demo-jobs` and `tradeflow-demo-jobs-dlq` are empty.
- [ ] Confirm log groups exist with 7-day retention and that logs contain no
      secret values.
- [ ] Record command output and screenshots in the issue.

## Phase 4: DNS and URL switch

- [ ] Confirm Phase 3 evidence is complete and the demo owner has approved the
      switch in writing.
- [ ] If a custom hostname is required, resolve the domain and certificate path
      first (see Phase 1; not covered by the templates).
- [ ] If DNS is being repointed, lower the TTL ahead of the window and switch
      during the agreed window. If the distribution domain is used directly,
      publish the new URL instead.
- [ ] After the switch, verify from an external network: landing page, demo
      console, `/api/demo/status`, one full session, and one document upload.
- [ ] Keep the EC2 host running and reachable. It is the rollback target until
      the soak completes.

## Phase 5: Soak

- [ ] Run both stacks in parallel for the agreed soak window. The EC2 host stays
      authoritative until the demo owner signs off.
- [ ] Each day of the soak: run one full demo session on the serverless URL, then
      confirm `ServerlessDatabaseCapacity` returns to 0 ACU, the coordination
      state returns to `ready`, both queues are empty, and no `demo_refresh_failed`
      state has appeared.
- [ ] Watch the worker log volume and the DLQ; a rebuild that exceeds the 900 s
      worker timeout fails and lands in the DLQ after 5 receives.
- [ ] Compare actual spend with `docs/deployment/serverless-costs.md` and record
      the variance. Investigate a non-zero capacity average during idle time as
      an incident, not a slow month.
- [ ] Exit criteria: every Phase 3 check passes twice on separate days, no
      unresolved incident, no DLQ messages, and the demo owner records go.

## Phase 6: Decommission (destructive; approval required)

Each step in this phase needs written approval from the AWS account owner before
it runs. None of them are urgent; if there is any doubt, defer them to a later
window.

- [ ] Confirm Phase 5 exit criteria and the demo owner's go.
- [ ] Confirm the rollback window has closed and record that decision.
- [ ] Terminate the EC2 instance `i-0dbb59b359b95f12c`.
- [ ] Release the Elastic IP `52.64.5.66` only after the instance is terminated.
- [ ] Confirm the EBS volumes are gone and delete any that remain.
- [ ] Keep the three ECR repositories: they hold the rollback image tags.
- [ ] Keep the serverless S3 buckets, the Aurora cluster, the DynamoDB table,
      and the SQS queues. Deleting them is a separate, later decision.
- [ ] Leave the pre-existing GitHub OIDC provider in place: it is shared account
      infrastructure, not part of this stack.
- [ ] Remove nothing belonging to LuckyALPA.

## Rollback

### Application rollback (preferred)

Images are tagged with the git SHA. Redeploy the last known-good tag without
running migrations, because the schema has not changed:

```bash
AWS_REGION=ap-southeast-2 IMAGE_TAG=<previous-sha> SKIP_MIGRATIONS=1 \
  ./infra/scripts/deploy-serverless.sh
```

`SKIP_MIGRATIONS=1` is what makes this a code-only rollback. This is the default
recovery for a bad application release and requires no schema decision.

### Schema rollback

Only if the schema must go back and only if it is backward compatible with the
image you are rolling back to, invoke the migration function explicitly:

```bash
aws lambda invoke --region ap-southeast-2 \
  --function-name tradeflow-demo-migration \
  --payload '{"action":"downgrade","revision":"<rev>"}' \
  --cli-binary-format raw-in-base64-out /dev/stdout
```

Constraints:

- A downgrade is only safe when the older image tolerates the older schema. If
  the schema is not backward compatible, do not downgrade; fix forward.
- **Any data written after cutover must be exported before a downgrade.** The
  downgrade does not preserve rows the older schema cannot represent.
- The export path is **unverified and not currently defined**. The VPC has no
  NAT gateway and no internet gateway, the Lambda security group has no egress
  beyond the VPC and the S3/DynamoDB gateway endpoints, and there is no bastion,
  so a dump cannot simply be pulled to a workstation. Decide and test an export
  path before relying on a downgrade. The demo dataset is re-seedable, so in
  most cases the correct recovery is to let the reset re-seed rather than to
  downgrade.

### Cutover rollback

If the serverless stack cannot be made to behave during Phase 3 or the soak,
roll the public URL back to the EC2 host:

- [ ] Repoint DNS, or republish the old URL, to `52.64.5.66`.
- [ ] Confirm the EC2 demo answers on `/api/demo/status` and run one session.
- [ ] Leave the serverless stack in place for diagnosis. Do not delete it; the
      templates are the record of what was tried.
- [ ] Record the failure mode, the evidence, and the decision in the cutover
      issue before scheduling another attempt.

## Must not be done without approval

Do not, under any circumstances short of a written approval recorded in the
cutover issue:

- terminate the EC2 instance `i-0dbb59b359b95f12c`;
- release the Elastic IP `52.64.5.66`;
- delete the EC2 volumes;
- delete the retained serverless S3 buckets (web, documents, artifacts) or the
  `tradeflow-demo-aurora` cluster;
- delete the ECR repositories, which hold the rollback image tags;
- touch any LuckyALPA resource;
- change the Aurora pause configuration (`MinCapacity` 0, `SecondsUntilAutoPause`
  300), turn on RDS Proxy or the RDS Data API, or add a NAT gateway or interface
  endpoint: each changes the cost and behaviour described in
  `docs/deployment/serverless-costs.md` and `docs/runbooks/serverless-demo.md`;
- store a demo secret in a template, a stack, a workflow file, or a log.
