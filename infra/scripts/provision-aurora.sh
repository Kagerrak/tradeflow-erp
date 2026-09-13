#!/usr/bin/env bash
# Provision the Aurora PostgreSQL cluster for the serverless demo.
#
# The cluster is created outside CloudFormation on purpose: on an AWS free-plan
# account Aurora PostgreSQL is only available through express configuration, and
# CloudFormation does not expose the WithExpressConfiguration parameter.
# Everything else in the stack is still managed by the data/app templates.
#
# What this creates, and why:
#   * express configuration (the only creation path available on the free plan)
#   * serverless v2 with MinCapacity 0 and a 300 second auto-pause, which is what
#     makes an idle demo cost nothing in compute
#   * the application database, created after the cluster because express
#     configuration cannot create an initial database
#   * deletion protection, so a stack operation cannot remove the data
#
# Express configuration restrictions that shape the architecture:
#   * no VPC association; access is only through the Aurora internet access
#     gateway, so the Lambdas that use it must have internet egress
#   * IAM authentication only; there is no master password
#   * 1 GB storage and 4 ACU on the free plan
#
# Usage: AWS_REGION=ap-southeast-2 ./infra/scripts/provision-aurora.sh
# Prints shell assignments for the caller: DB_ENDPOINT, DB_PORT, DB_RESOURCE_ID.
set -euo pipefail

PROJECT_NAME="${PROJECT_NAME:-tradeflow-demo}"
REGION="${AWS_REGION:-$(aws configure get region)}"
CLUSTER_ID="${CLUSTER_ID:-$PROJECT_NAME-aurora}"
DATABASE_NAME="${DATABASE_NAME:-tradeflow_demo}"
DATABASE_USER="${DATABASE_USER:-tradeflow}"
MAX_ACU="${MAX_ACU:-2}"
AUTO_PAUSE_SECONDS="${AUTO_PAUSE_SECONDS:-300}"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

cluster_status() {
  aws rds describe-db-clusters --region "$REGION" --db-cluster-identifier "$CLUSTER_ID" \
    --query 'DBClusters[0].Status' --output text 2>/dev/null || echo "missing"
}

say "Aurora express-configuration cluster: $CLUSTER_ID"
if [[ "$(cluster_status)" == "missing" ]]; then
  aws rds create-db-cluster --region "$REGION" \
    --db-cluster-identifier "$CLUSTER_ID" \
    --engine aurora-postgresql \
    --with-express-configuration \
    --master-username "$DATABASE_USER" \
    --serverless-v2-scaling-configuration \
      "MinCapacity=0,MaxCapacity=$MAX_ACU,SecondsUntilAutoPause=$AUTO_PAUSE_SECONDS" \
    --tags "Key=Project,Value=$PROJECT_NAME" "Key=Environment,Value=demo" >/dev/null
  echo "  creating (express configuration provisions the writer instance)"
  aws rds wait db-cluster-available --region "$REGION" --db-cluster-identifier "$CLUSTER_ID"
else
  echo "  exists: status $(cluster_status)"
  aws rds modify-db-cluster --region "$REGION" --db-cluster-identifier "$CLUSTER_ID" \
    --serverless-v2-scaling-configuration \
      "MinCapacity=0,MaxCapacity=$MAX_ACU,SecondsUntilAutoPause=$AUTO_PAUSE_SECONDS" \
    --apply-immediately >/dev/null
  echo "  scale-to-zero settings re-applied"
fi

# Idempotent: enabling deletion protection twice is a no-op.
aws rds modify-db-cluster --region "$REGION" --db-cluster-identifier "$CLUSTER_ID" \
  --deletion-protection --apply-immediately >/dev/null

read -r DB_ENDPOINT DB_PORT DB_RESOURCE_ID DB_MIN DB_PAUSE <<<"$(aws rds describe-db-clusters \
  --region "$REGION" --db-cluster-identifier "$CLUSTER_ID" \
  --query 'DBClusters[0].[Endpoint,Port,DbClusterResourceId,ServerlessV2ScalingConfiguration.MinCapacity,ServerlessV2ScalingConfiguration.SecondsUntilAutoPause]' \
  --output text)"

if [[ -z "$DB_ENDPOINT" || "$DB_ENDPOINT" == "None" ]]; then
  echo "Aurora cluster has no endpoint yet." >&2
  exit 1
fi

echo "  endpoint:      $DB_ENDPOINT:$DB_PORT"
echo "  resource id:   $DB_RESOURCE_ID"
echo "  min capacity:  $DB_MIN ACU (auto-pause after ${DB_PAUSE}s)"
echo "  database:      $DATABASE_NAME (created by the deployment workflow)"

cat <<EOF
DB_ENDPOINT=$DB_ENDPOINT
DB_PORT=$DB_PORT
DB_RESOURCE_ID=$DB_RESOURCE_ID
EOF
