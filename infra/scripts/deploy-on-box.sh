#!/usr/bin/env bash
# Runs ON the EC2 instance (invoked by the GitHub Actions deploy job over SSH).
# Fetches this customer's config from SSM Parameter Store, writes .env,
# pulls the published images, restarts the stack, and health-checks it.
#
# Required env: ECR_REGISTRY, IMAGE_TAG (provided by the workflow)
# Optional env: CUSTOMER (default: demo)
set -euo pipefail
cd "$(dirname "$0")"

CUSTOMER="${CUSTOMER:-demo}"
PREFIX="tradeflow-$CUSTOMER"
IMDS="http://169.254.169.254/latest"
IMDS_TOKEN="$(curl -s --max-time 5 -X PUT "$IMDS/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 21600" || true)"
if [ -n "$IMDS_TOKEN" ]; then
  META() { curl -s --max-time 5 -H "X-aws-ec2-metadata-token: $IMDS_TOKEN" "$IMDS/$1"; }
else
  META() { curl -s --max-time 5 "$IMDS/$1"; }
fi
AZ="$(META meta-data/placement/availability-zone)"
REGION="${AZ%?}" # ap-southeast-2a -> ap-southeast-2
: "${REGION:?could not determine region from instance metadata}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${ECR_REGISTRY:-$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com}"
IMAGE_TAG="${IMAGE_TAG:?IMAGE_TAG is required (git SHA from the workflow)}"

echo "==> Fetching configuration from SSM (/$PREFIX/*)"
PARAMS="$(aws ssm get-parameters --region "$REGION" \
  --names "/$PREFIX/database-password" "/$PREFIX/reset-token" "/$PREFIX/auth-test-secret" \
          "/$PREFIX/auth-issuer" "/$PREFIX/public-url" \
  --with-decryption --query 'Parameters[*].[Name,Value]' --output text)"

getp() { printf '%s\n' "$PARAMS" | awk -v k="/$PREFIX/$1" '$1 == k {print $2}'; }

PUBLIC_URL="$(getp public-url)"
HOST="${PUBLIC_URL#*://}"
if [[ "$HOST" =~ ^[0-9.]+$ ]]; then
  SITE_ADDRESS=":80"   # bare IP: no TLS possible until a domain is attached
else
  SITE_ADDRESS="$HOST"
fi

cat >.env <<EOF
TRADEFLOW_DEMO_DATABASE_PASSWORD=$(getp database-password)
TRADEFLOW_DEMO_RESET_TOKEN=$(getp reset-token)
TRADEFLOW_AUTH_TEST_SECRET=$(getp auth-test-secret)
TRADEFLOW_AUTH_ISSUER=$(getp auth-issuer)
TRADEFLOW_PUBLIC_URL=$PUBLIC_URL
SITE_ADDRESS=$SITE_ADDRESS
ECR_REGISTRY=$REGISTRY
IMAGE_TAG=$IMAGE_TAG
EOF
chmod 600 .env
echo "==> public-url: $PUBLIC_URL (caddy address: $SITE_ADDRESS)"

echo "==> Logging in to ECR"
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY" >/dev/null

echo "==> Pulling images (tag: $IMAGE_TAG)"
docker compose -f compose.demo.yaml -f compose.deploy.yaml pull

echo "==> Restarting stack"
docker compose -f compose.demo.yaml -f compose.deploy.yaml up -d --remove-orphans

echo "==> Waiting for /demo to answer"
for attempt in $(seq 1 60); do
  if curl -fsS "http://localhost/demo" >/dev/null 2>&1; then
    echo "==> Healthy. Demo is live at $PUBLIC_URL/demo"
    exit 0
  fi
  sleep 5
done

echo "==> Health check failed — recent logs:" >&2
docker compose -f compose.demo.yaml -f compose.deploy.yaml logs --tail=50 >&2
echo "==> Rollback: IMAGE_TAG=<previous-sha> bash deploy-on-box.sh" >&2
exit 1
