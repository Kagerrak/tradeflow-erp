#!/usr/bin/env bash
# Deploy the serverless TradeFlow demo.
#
# Order of operations (each step is separate so a failure is unambiguous):
#   1. ensure the demo secrets exist in SSM Parameter Store (create-only)
#   2. deploy the network stack   (VPC, private subnets, gateway endpoints)
#   3. deploy the data stack      (Aurora Serverless v2, S3, DynamoDB, SQS)
#   4. deploy the application     (Lambdas, HTTP API, CloudFront)
#   5. apply runtime secrets to the Lambda configuration
#   6. run schema migrations as an explicit step
#   7. optionally rebuild the demo dataset
#   8. verify the public endpoints
#
# No secret is ever written to this repository, to a CloudFormation template or
# to a log. Secrets live in Parameter Store and are handed to the Lambdas here.
#
# Usage:
#   AWS_REGION=ap-southeast-2 IMAGE_TAG=<git-sha> ./infra/scripts/deploy-serverless.sh
#
# Options (environment):
#   IMAGE_TAG       image tag built by the workflow (required)
#   AWS_REGION      defaults to the configured region
#   PROJECT_NAME    defaults to tradeflow-demo
#   SEED_DEMO       "1" (default) to rebuild demo data when the state is empty
#   SKIP_MIGRATIONS "1" to skip step 6 (used by the rollback procedure)
set -euo pipefail

PROJECT_NAME="${PROJECT_NAME:-tradeflow-demo}"
IMAGE_TAG="${IMAGE_TAG:?IMAGE_TAG is required (the git SHA built by the workflow)}"
REGION="${AWS_REGION:-$(aws configure get region)}"
SEED_DEMO="${SEED_DEMO:-1}"
SKIP_MIGRATIONS="${SKIP_MIGRATIONS:-0}"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com"
NETWORK_STACK="$PROJECT_NAME-network"
DATA_STACK="$PROJECT_NAME-data"
APP_STACK="$PROJECT_NAME-app"
SECRET_PREFIX="/tradeflow/demo"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

# AWS CLI shorthand splits unescaped commas, so list-valued parameters are
# always passed through a JSON file rather than Key=Value overrides.
params_file() { # json
  local file
  file="$(mktemp)"
  chmod 600 "$file"
  printf '%s' "$1" >"$file"
  printf '%s' "$file"
} 

# A stack whose very first create failed is stuck in ROLLBACK_COMPLETE and
# cannot be updated. It holds no deployed state, so it is safe to remove before
# retrying; retained resources (S3, DynamoDB, Aurora) survive and are reported.
reset_failed_stack() { # stack
  local status
  status="$(aws cloudformation describe-stacks --region "$REGION" --stack-name "$1" \
    --query 'Stacks[0].StackStatus' --output text 2>/dev/null || true)"
  case "$status" in
    ROLLBACK_COMPLETE|REVIEW_IN_PROGRESS)
      echo "  $1 is $status; deleting the empty stack before retrying"
      aws cloudformation delete-stack --region "$REGION" --stack-name "$1"
      aws cloudformation wait stack-delete-complete --region "$REGION" --stack-name "$1"
      ;;
  esac
}

stack_output() { # stack, output-key
  aws cloudformation describe-stacks --region "$REGION" --stack-name "$1" \
    --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" --output text
}

ensure_secret() { # name, generator
  local name="$SECRET_PREFIX/$1" generator="$2"
  if aws ssm get-parameter --region "$REGION" --name "$name" >/dev/null 2>&1; then
    echo "  exists: $name"
  else
    aws ssm put-parameter --region "$REGION" --name "$name" --type SecureString \
      --value "$($generator)" >/dev/null
    echo "  created: $name"
  fi
}

ensure_string() { # name, value
  local name="$SECRET_PREFIX/$1" value="$2"
  if aws ssm get-parameter --region "$REGION" --name "$name" >/dev/null 2>&1; then
    echo "  exists: $name"
  else
    aws ssm put-parameter --region "$REGION" --name "$name" --type String --value "$value" >/dev/null
    echo "  created: $name"
  fi
}

get_secret() {
  aws ssm get-parameter --region "$REGION" --name "$SECRET_PREFIX/$1" \
    --with-decryption --query Parameter.Value --output text
}

# ---------------------------------------------------------------- 1. secrets
say "Secrets in SSM Parameter Store ($SECRET_PREFIX/*)"
# Aurora master passwords are limited to 41 characters and must avoid a few
# symbols, so this is a separate parameter from the compose-era database
# password and is generated to fit.
ensure_secret aurora-master-password "openssl rand -hex 20"
ensure_secret reset-token "openssl rand -hex 32"
ensure_secret auth-test-secret "openssl rand -hex 32"
ensure_string auth-issuer "https://identity.tradeflow.invalid"

DATABASE_PASSWORD="$(get_secret aurora-master-password)"
RESET_TOKEN="$(get_secret reset-token)"
AUTH_TEST_SECRET="$(get_secret auth-test-secret)"
AUTH_ISSUER="$(get_secret auth-issuer)"

# ------------------------------------------------------------------ 2. network
say "Network stack: $NETWORK_STACK"
aws cloudformation deploy --region "$REGION" --stack-name "$NETWORK_STACK" \
  --template-file infra/cloudformation/network.yaml \
  --parameter-overrides "ProjectName=$PROJECT_NAME" \
  --tags "Project=$PROJECT_NAME" "Environment=demo" \
  --no-fail-on-empty-changeset

VPC_ID="$(stack_output "$NETWORK_STACK" VpcId)"
SUBNET_IDS="$(stack_output "$NETWORK_STACK" PrivateSubnetIds)"
LAMBDA_SG="$(stack_output "$NETWORK_STACK" LambdaSecurityGroupId)"
DATABASE_SG="$(stack_output "$NETWORK_STACK" DatabaseSecurityGroupId)"
echo "  vpc: $VPC_ID  subnets: $SUBNET_IDS"

# --------------------------------------------------------------------- 3. data
say "Data stack: $DATA_STACK (Aurora Serverless v2, min 0 ACU)"
reset_failed_stack "$DATA_STACK"
DATA_PARAMS="$(params_file "$(jq -n \
  --arg project "$PROJECT_NAME" \
  --arg vpc "$VPC_ID" \
  --arg subnets "$SUBNET_IDS" \
  --arg sg "$DATABASE_SG" \
  --arg secret "$SECRET_PREFIX/aurora-master-password" \
  '[
    {ParameterKey:"ProjectName",ParameterValue:$project},
    {ParameterKey:"VpcId",ParameterValue:$vpc},
    {ParameterKey:"PrivateSubnetIds",ParameterValue:$subnets},
    {ParameterKey:"DatabaseSecurityGroupId",ParameterValue:$sg},
    {ParameterKey:"DatabasePasswordParameter",ParameterValue:$secret}
  ]')")"
aws cloudformation deploy --region "$REGION" --stack-name "$DATA_STACK" \
  --template-file infra/cloudformation/data.yaml \
  --parameter-overrides "file://$DATA_PARAMS" \
  --tags "Project=$PROJECT_NAME" "Environment=demo" \
  --no-fail-on-empty-changeset
rm -f "$DATA_PARAMS"

DB_ENDPOINT="$(stack_output "$DATA_STACK" DbClusterEndpoint)"
DB_PORT="$(stack_output "$DATA_STACK" DbClusterPort)"
DOCUMENTS_BUCKET="$(stack_output "$DATA_STACK" DocumentsBucketName)"
ARTIFACTS_BUCKET="$(stack_output "$DATA_STACK" ArtifactsBucketName)"
WEB_BUCKET="$(stack_output "$DATA_STACK" WebAssetsBucketName)"
COORDINATION_TABLE="$(stack_output "$DATA_STACK" CoordinationTableName)"
JOBS_QUEUE_ARN="$(stack_output "$DATA_STACK" JobsQueueArn)"
JOBS_QUEUE_URL="$(stack_output "$DATA_STACK" JobsQueueUrl)"
echo "  aurora: $DB_ENDPOINT:$DB_PORT"

DATABASE_URL="postgresql+asyncpg://tradeflow:${DATABASE_PASSWORD}@${DB_ENDPOINT}:${DB_PORT}/tradeflow_demo"

# ---------------------------------------------------------------- 4. application
say "Application stack: $APP_STACK"
reset_failed_stack "$APP_STACK"
APP_PARAMS="$(params_file "$(jq -n \
  --arg project "$PROJECT_NAME" \
  --arg subnets "$SUBNET_IDS" \
  --arg sg "$LAMBDA_SG" \
  --arg endpoint "$DB_ENDPOINT" \
  --arg port "$DB_PORT" \
  --arg documents "$DOCUMENTS_BUCKET" \
  --arg artifacts "$ARTIFACTS_BUCKET" \
  --arg web "$WEB_BUCKET" \
  --arg coordination "$COORDINATION_TABLE" \
  --arg queue "$JOBS_QUEUE_ARN" \
  --arg api "$REGISTRY/$PROJECT_NAME-api:$IMAGE_TAG" \
  --arg worker "$REGISTRY/$PROJECT_NAME-worker:$IMAGE_TAG" \
  --arg webimage "$REGISTRY/$PROJECT_NAME-web:$IMAGE_TAG" \
  '[
    {ParameterKey:"ProjectName",ParameterValue:$project},
    {ParameterKey:"PrivateSubnetIds",ParameterValue:$subnets},
    {ParameterKey:"LambdaSecurityGroupId",ParameterValue:$sg},
    {ParameterKey:"DbClusterEndpoint",ParameterValue:$endpoint},
    {ParameterKey:"DbClusterPort",ParameterValue:$port},
    {ParameterKey:"DocumentsBucketName",ParameterValue:$documents},
    {ParameterKey:"ArtifactsBucketName",ParameterValue:$artifacts},
    {ParameterKey:"WebAssetsBucketName",ParameterValue:$web},
    {ParameterKey:"CoordinationTableName",ParameterValue:$coordination},
    {ParameterKey:"JobsQueueArn",ParameterValue:$queue},
    {ParameterKey:"ApiImageUri",ParameterValue:$api},
    {ParameterKey:"WorkerImageUri",ParameterValue:$worker},
    {ParameterKey:"WebImageUri",ParameterValue:$webimage}
  ]')")"
aws cloudformation deploy --region "$REGION" --stack-name "$APP_STACK" \
  --template-file infra/cloudformation/app.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides "file://$APP_PARAMS" \
  --tags "Project=$PROJECT_NAME" "Environment=demo" \
  --no-fail-on-empty-changeset
rm -f "$APP_PARAMS"

API_FUNCTION="$(stack_output "$APP_STACK" ApiFunctionName)"
WORKER_FUNCTION="$(stack_output "$APP_STACK" WorkerFunctionName)"
WEB_FUNCTION="$(stack_output "$APP_STACK" WebFunctionName)"
MIGRATION_FUNCTION="$(stack_output "$APP_STACK" MigrationFunctionName)"
API_URL="$(stack_output "$APP_STACK" ApiUrl)"
DISTRIBUTION_DOMAIN="$(stack_output "$APP_STACK" DistributionDomainName)"
PUBLIC_URL="https://$DISTRIBUTION_DOMAIN"

# --------------------------------------------- 5. runtime secrets (never in CFN)
apply_environment() { # function-name, extra json object
  local function_name="$1" extra="$2"
  local current merged
  current="$(aws lambda get-function-configuration --region "$REGION" \
    --function-name "$function_name" --query 'Environment.Variables' --output json)"
  # jq writes the merge to a file so no secret ever reaches the process list.
  merged="$(mktemp)"
  chmod 600 "$merged"
  jq -n --argjson current "${current:-{\}}" --argjson extra "$extra" \
    '$current + $extra' >"$merged"
  aws lambda update-function-configuration --region "$REGION" \
    --function-name "$function_name" \
    --environment "file://$merged" >/dev/null
  rm -f "$merged"
  aws lambda wait function-updated --region "$REGION" --function-name "$function_name"
}

say "Applying runtime configuration (from Parameter Store)"
API_ENV="$(jq -n \
  --arg url "$DATABASE_URL" \
  --arg token "$RESET_TOKEN" \
  --arg secret "$AUTH_TEST_SECRET" \
  --arg issuer "$AUTH_ISSUER" \
  '{TRADEFLOW_DATABASE_URL: $url, TRADEFLOW_DEMO_RESET_TOKEN: $token, TRADEFLOW_AUTH_TEST_SECRET: $secret, TRADEFLOW_AUTH_ISSUER: $issuer}')"
apply_environment "$API_FUNCTION" "$API_ENV"
apply_environment "$WORKER_FUNCTION" "$API_ENV"
apply_environment "$MIGRATION_FUNCTION" "$(jq -n --arg url "$DATABASE_URL" '{TRADEFLOW_DATABASE_URL: $url}')"
apply_environment "$WEB_FUNCTION" "$(jq -n \
  --arg secret "$AUTH_TEST_SECRET" \
  --arg issuer "$AUTH_ISSUER" \
  --arg url "$PUBLIC_URL" \
  '{TRADEFLOW_AUTH_TEST_SECRET: $secret, TRADEFLOW_AUTH_ISSUER: $issuer, TRADEFLOW_PUBLIC_URL: $url}')"
echo "  runtime configuration applied to 4 functions"

# ------------------------------------------------------------- 6. migrations
if [[ "$SKIP_MIGRATIONS" == "1" ]]; then
  say "Skipping migrations (SKIP_MIGRATIONS=1)"
else
  say "Running schema migrations as a deployment step"
  RESULT_FILE="$(mktemp)"
  aws lambda invoke --region "$REGION" --function-name "$MIGRATION_FUNCTION" \
    --payload '{"action":"upgrade","revision":"head"}' \
    --cli-binary-format raw-in-base64-out "$RESULT_FILE" >/dev/null
  cat "$RESULT_FILE"
  rm -f "$RESULT_FILE"
fi

# ------------------------------------------------------------- 7. demo data
if [[ "$SEED_DEMO" == "1" ]]; then
  say "Checking demo coordination state"
  STATE="$(aws dynamodb get-item --region "$REGION" --table-name "$COORDINATION_TABLE" \
    --key '{"pk":{"S":"demo"},"sk":{"S":"state"}}' \
    --query 'Item.status.S' --output text 2>/dev/null || true)"
  if [[ "$STATE" == "ready" || "$STATE" == "refreshing" ]]; then
    echo "  demo state: $STATE (no rebuild queued)"
  else
    echo "  demo state: empty — queuing an initial rebuild"
    MESSAGE="$(jq -nc --arg owner "bootstrap-$(date +%s)" \
      '{kind:"demo_reset", marker_id:$owner, payload:{owner:$owner}}')"
    aws sqs send-message --region "$REGION" --queue-url "$JOBS_QUEUE_URL" \
      --message-body "$MESSAGE" >/dev/null
    echo "  queued. The worker will report 'ready' in the coordination table."
  fi
fi

# ---------------------------------------------------------------- 8. verify
say "Verifying public endpoints"
for attempt in $(seq 1 60); do
  STATUS="$(curl -fsS --max-time 20 "$PUBLIC_URL/api/demo/status" 2>/dev/null \
    | jq -r '.status' 2>/dev/null || true)"
  if [[ "$STATUS" == "ready" || "$STATUS" == "refreshing" ]]; then
    echo "  web: $PUBLIC_URL/api/demo/status -> $STATUS"
    break
  fi
  if [[ "$attempt" == "60" ]]; then
    echo "  web endpoint did not answer; CloudFront may still be deploying." >&2
    exit 1
  fi
  sleep 10
done

curl -fsS --max-time 20 "$API_URL/health/live" >/dev/null && echo "  api: $API_URL/health/live -> ok"

cat <<EOF

Deployed.
  public url:   $PUBLIC_URL
  api url:      $API_URL
  distribution: $DISTRIBUTION_DOMAIN
  image tag:    $IMAGE_TAG
  aurora:       $DB_ENDPOINT:$DB_PORT (0-2 ACU, auto-pause 300s)

Rollback: re-run with IMAGE_TAG=<previous-sha> (see docs/runbooks/serverless-demo.md).
EOF
