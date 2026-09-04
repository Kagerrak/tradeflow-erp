# Demo deployment runbook (AWS)

End-to-end guide for the recruiter-facing TradeFlow demo. All infrastructure is
created with `infra/scripts/provision-aws.sh` (AWS CLI only, no console), the
CD pipeline is `.github/workflows/deploy.yml`, and the running stack is the
existing `infra/compose.demo.yaml` (demo environment: seeded data, auto-reset
every 45 minutes, rate-limited).

## Architecture

```
GitHub push to main
  -> CI (.github/workflows/ci.yml)
  -> Deploy demo (.github/workflows/deploy.yml)
       -> build api/worker/web images, push to ECR (auth via OIDC role)
       -> SSM SendCommand to the instance (no inbound SSH from GitHub)
            -> decode compose files + deploy script into /opt/tradeflow
            -> fetch config from SSM Parameter Store -> .env
            -> docker login (instance IAM role), compose pull, up -d
            -> health check http://localhost/demo
EC2 (t3.small): Caddy (:80/:443) -> web:3000 -> api:8000 (internal only)
                + worker + reset + postgres + redis + minio (all in compose)
```

Deploys use AWS Systems Manager (SSM) instead of SSH: the instance needs no
inbound admin access from GitHub's runners (port 22 stays restricted to the
owner's IP for manual access), and every deploy command is logged in AWS.

## One-time setup

1. **AWS CLI configured** with an admin IAM user (never root).
2. **Provision** (idempotent — safe to re-run; resources are `tradeflow-demo-*`):

   ```bash
   ./infra/scripts/provision-aws.sh            # HTTP on the Elastic IP
   DOMAIN=tradeflow.app ./infra/scripts/provision-aws.sh   # with DNS + HTTPS
   ```

   Creates: 3 ECR repos, GitHub OIDC provider + deploy role, EC2 + security
   group + Elastic IP + instance role, deploy SSH key
   (`~/.ssh/tradeflow-demo_deploy.pem`), and secrets in SSM (`/tradeflow/demo/*`).

3. **No GitHub secrets are needed for deploys** — OIDC + SSM carry the whole
   path. The deploy SSH key (`~/.ssh/tradeflow-demo_deploy.pem`) is only for
   your own manual access; port 22 is restricted to your IP.

4. **Domain (for HTTPS)**: buy one anywhere, then
   `DOMAIN=yourdomain.com ./infra/scripts/provision-aws.sh` creates the hosted
   zone, prints the NS records to enter at your registrar, creates the A record,
   and updates the `public-url` SSM parameter. Caddy obtains the TLS cert
   automatically on the next deploy. Until then the demo works over HTTP at the
   Elastic IP.

## Deploying

- Automatic on every push to `main` that touches `apps/**` or the infra files.
- Or manually: GitHub → Actions → "Deploy demo" → Run workflow.
- Watch: images build (~5–10 min cold, ~1–2 min with cache), then the instance
  pulls and restarts (~1 min), then the health check polls `/demo` for up to
  5 minutes while migrations and seed run.

## Verify

```bash
curl -fsS http://<EIP>/demo        # or https://demo.yourdomain.com/demo
ssh -i ~/.ssh/tradeflow-demo_deploy.pem ubuntu@<EIP>
ssh ... 'cd /opt/tradeflow && docker compose -f compose.demo.yaml -f compose.deploy.yaml ps'
```

Open `/demo` in a browser: it shows the auto-generated demo login credentials.
The data re-seeds every 45 minutes; nothing visitors do persists.

## Rollback

Images are tagged with the git SHA, so rollback is a re-deploy of a known SHA:

```bash
ssh -i ~/.ssh/tradeflow-demo_deploy.pem ubuntu@<EIP> \
  'cd /opt/tradeflow && ECR_REGISTRY=<acct>.dkr.ecr.ap-southeast-2.amazonaws.com IMAGE_TAG=<previous-sha> CUSTOMER=demo bash deploy-on-box.sh'
```

## Rotate secrets

```bash
aws ssm put-parameter --name /tradeflow/demo/reset-token --type SecureString --overwrite --value "<new>"
# then re-run the deploy so the box rewrites .env from SSM
```

## Teardown

```bash
aws ec2 terminate-instances --instance-ids <id>
aws ec2 release-address --allocation-id <alloc>     # only after termination
aws ecr delete-repository --repository-name tradeflow-demo-api --force   # + worker, web
aws iam delete-role --role-name tradeflow-demo-github-actions            # detach policies first
```

The demo database lives in a compose volume on the instance — terminating loses
nothing (data re-seeds from the image on next boot).

## Adding a customer later

Everything above is parameterized by `CUSTOMER` and becomes a Terraform
module (`infra/terraform/modules/tradeflow-stack`). Customer #2 = copy the two
`terraform.tfvars` files, apply, add DNS + GitHub environment — this runbook is
the acceptance checklist that module must satisfy.
