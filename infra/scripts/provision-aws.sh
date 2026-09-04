#!/usr/bin/env bash
# Provision the AWS infrastructure for one TradeFlow demo stack.
#
# Idempotent: every resource is check-then-create, so re-running is safe.
# Everything is done through the AWS CLI (no console) and every resource is
# prefixed with "tradeflow-<customer>-" so a future second stack never collides.
#
# Usage:
#   ./infra/scripts/provision-aws.sh                 # demo stack, sensible defaults
#   CUSTOMER=acme DOMAIN=tradeflow.app ./infra/scripts/provision-aws.sh
#
# Prerequisites:
#   - aws CLI v2 configured (aws sts get-caller-identity works)
#   - DOMAIN is optional; without it the demo serves plain HTTP on the Elastic IP
set -euo pipefail

CUSTOMER="${CUSTOMER:-demo}"
INSTANCE_TYPE="${INSTANCE_TYPE:-t3.small}"
DOMAIN="${DOMAIN:-}"                       # e.g. tradeflow.app (no subdomain)
SUBDOMAIN="${SUBDOMAIN:-$CUSTOMER}"        # A-record name, e.g. demo
REPO="Kagerrak/tradeflow-erp"

REGION="$(aws configure get region)"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com"
PREFIX="tradeflow-$CUSTOMER"
MY_IP="$(curl -s https://checkip.amazonaws.com)/32"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

# ---------------------------------------------------------------- ECR repos
say "ECR repositories ($REGISTRY)"
REPOS=("$PREFIX-api" "$PREFIX-worker" "$PREFIX-web")
for repo in "${REPOS[@]}"; do
  if aws ecr describe-repositories --repository-names "$repo" >/dev/null 2>&1; then
    echo "  exists: $repo"
  else
    aws ecr create-repository --repository-name "$repo" >/dev/null
    echo "  created: $repo"
  fi
  aws ecr put-lifecycle-policy --repository-name "$repo" --lifecycle-policy-text \
    '{"rules":[{"rulePriority":1,"description":"Keep last 20 images","selection":{"tagStatus":"any","countType":"imageCountMoreThan","countNumber":20},"action":{"type":"expire"}}]}' \
    >/dev/null
done

# ------------------------------------------------- GitHub OIDC provider + role
say "GitHub Actions OIDC identity provider"
OIDC_ARN="arn:aws:iam::$ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com"
if aws iam list-open-id-connect-providers | grep -q "token.actions.githubusercontent.com"; then
  echo "  exists: token.actions.githubusercontent.com"
else
  aws iam create-open-id-connect-provider \
    --url https://token.actions.githubusercontent.com \
    --client-id-list sts.amazonaws.com \
    --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1 >/dev/null
  echo "  created: token.actions.githubusercontent.com"
fi

say "IAM role for GitHub Actions: $PREFIX-github-actions"
TRUST_POLICY="$(mktemp)"
cat >"$TRUST_POLICY" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Federated": "$OIDC_ARN" },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
        "token.actions.githubusercontent.com:sub": "repo:$REPO:ref:refs/heads/main"
      }
    }
  }]
}
EOF
if aws iam get-role --role-name "$PREFIX-github-actions" >/dev/null 2>&1; then
  aws iam update-assume-role-policy --role-name "$PREFIX-github-actions" --policy-document "file://$TRUST_POLICY"
  echo "  updated trust policy"
else
  aws iam create-role --role-name "$PREFIX-github-actions" --assume-role-policy-document "file://$TRUST_POLICY" >/dev/null
  echo "  created"
fi

ECR_PUSH_POLICY="$(mktemp)"
cat >"$ECR_PUSH_POLICY" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*" },
    { "Effect": "Allow", "Action": [
        "ecr:BatchCheckLayerAvailability", "ecr:PutImage", "ecr:InitiateLayerUpload",
        "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:BatchGetImage"
      ],
      "Resource": [
        "arn:aws:ecr:$REGION:$ACCOUNT_ID:repository/$PREFIX-api",
        "arn:aws:ecr:$REGION:$ACCOUNT_ID:repository/$PREFIX-worker",
        "arn:aws:ecr:$REGION:$ACCOUNT_ID:repository/$PREFIX-web"
      ]
    }
  ]
}
EOF
aws iam put-role-policy --role-name "$PREFIX-github-actions" --policy-name ecr-push --policy-document "file://$ECR_PUSH_POLICY"
echo "  attached ecr-push policy"

# --------------------------------------------- EC2 instance role + SSM secrets
say "IAM role for the EC2 instance: $PREFIX-ec2"
if ! aws iam get-role --role-name "$PREFIX-ec2" >/dev/null 2>&1; then
  aws iam create-role --role-name "$PREFIX-ec2" --assume-role-policy-document \
    '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}' \
    >/dev/null
  echo "  created"
fi
EC2_POLICY="$(mktemp)"
cat >"$EC2_POLICY" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": ["ssm:GetParameters", "ssm:GetParameter"],
      "Resource": "arn:aws:ssm:$REGION:$ACCOUNT_ID:parameter/$PREFIX/*" },
    { "Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*" },
    { "Effect": "Allow", "Action": [
        "ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer",
        "ecr:BatchGetImage", "ecr:DescribeRepositories"
      ],
      "Resource": [
        "arn:aws:ecr:$REGION:$ACCOUNT_ID:repository/$PREFIX-api",
        "arn:aws:ecr:$REGION:$ACCOUNT_ID:repository/$PREFIX-worker",
        "arn:aws:ecr:$REGION:$ACCOUNT_ID:repository/$PREFIX-web"
      ]
    }
  ]
}
EOF
aws iam put-role-policy --role-name "$PREFIX-ec2" --policy-name instance-policy --policy-document "file://$EC2_POLICY"
if ! aws iam get-instance-profile --instance-profile-name "$PREFIX-ec2" >/dev/null 2>&1; then
  aws iam create-instance-profile --instance-profile-name "$PREFIX-ec2" >/dev/null
  aws iam add-role-to-instance-profile --instance-profile-name "$PREFIX-ec2" --role-name "$PREFIX-ec2" >/dev/null
  echo "  instance profile ready"
fi

say "Secrets in SSM Parameter Store (/$PREFIX/*)"
put_secret() { # name, value — create only, never overwrite an existing secret
  local name="/$PREFIX/$1" value="$2"
  if aws ssm get-parameter --name "$name" >/dev/null 2>&1; then
    echo "  exists: $name"
  else
    aws ssm put-parameter --name "$name" --value "$value" --type SecureString >/dev/null
    echo "  created: $name"
  fi
}
put_secret database-password "$(openssl rand -hex 24)"
put_secret reset-token "$(openssl rand -hex 32)"
put_secret auth-test-secret "$(openssl rand -hex 32)"
put_secret auth-issuer "https://identity.tradeflow.invalid"
if ! aws ssm get-parameter --name "/$PREFIX/public-url" >/dev/null 2>&1; then
  aws ssm put-parameter --name "/$PREFIX/public-url" --value "http://placeholder" --type String >/dev/null
  echo "  created: /$PREFIX/public-url (placeholder, updated after EIP is known)"
fi

# ------------------------------------------------------ Security group + SSH
say "Security group: $PREFIX-sg"
SG_ID="$(aws ec2 describe-security-groups --group-names "$PREFIX-sg" --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || true)"
if [[ -z "$SG_ID" || "$SG_ID" == "None" ]]; then
  SG_ID="$(aws ec2 create-security-group --group-name "$PREFIX-sg" --description "TradeFlow $CUSTOMER stack" --query GroupId --output text)"
  echo "  created: $SG_ID"
fi
aws ec2 authorize-security-group-ingress --group-id "$SG_ID" --protocol tcp --port 80 --cidr 0.0.0.0/0 >/dev/null 2>&1 || true
aws ec2 authorize-security-group-ingress --group-id "$SG_ID" --protocol tcp --port 443 --cidr 0.0.0.0/0 >/dev/null 2>&1 || true
OLD_SSH="$(aws ec2 describe-security-groups --group-ids "$SG_ID" --query 'SecurityGroups[0].IpPermissions[?FromPort==`22`].IpRanges[*].CidrIp' --output text || true)"
for cidr in $OLD_SSH; do
  aws ec2 revoke-security-group-ingress --group-id "$SG_ID" --protocol tcp --port 22 --cidr "$cidr" >/dev/null || true
done
aws ec2 authorize-security-group-ingress --group-id "$SG_ID" --protocol tcp --port 22 --cidr "$MY_IP" >/dev/null
echo "  ingress: 80/443 open, 22 restricted to $MY_IP"

# ------------------------------------------------------------ Deploy SSH key
say "Deploy key pair: $PREFIX-deploy"
KEY_FILE="$HOME/.ssh/${PREFIX}_deploy.pem"
if aws ec2 describe-key-pairs --key-names "$PREFIX-deploy" >/dev/null 2>&1; then
  echo "  exists: $PREFIX-deploy"
else
  aws ec2 create-key-pair --key-name "$PREFIX-deploy" --query KeyMaterial --output text >"$KEY_FILE"
  chmod 600 "$KEY_FILE"
  echo "  created, private key saved to $KEY_FILE"
fi

# ----------------------------------------------------------------- EC2 + EIP
say "EC2 instance: $PREFIX ($INSTANCE_TYPE)"
INSTANCE_ID="$(aws ec2 describe-instances --filters "Name=tag:Name,Values=$PREFIX" "Name=instance-state-name,Values=pending,running,stopped" --query 'Reservations[0].Instances[0].InstanceId' --output text)"
if [[ -z "$INSTANCE_ID" || "$INSTANCE_ID" == "None" ]]; then
  AMI="$(aws ssm get-parameters --names /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id --query 'Parameters[0].Value' --output text)"
  USER_DATA="$(mktemp)"
  cat >"$USER_DATA" <<'EOF'
#!/bin/bash
export DEBIAN_FRONTEND=noninteractive
curl -fsSL https://get.docker.com | sh
apt-get update
apt-get install -y unzip
curl -fsSL https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip -o /tmp/awscliv2.zip
unzip -q /tmp/awscliv2.zip -d /tmp && /tmp/aws/install --update
usermod -aG docker ubuntu
mkdir -p /opt/tradeflow
EOF
  INSTANCE_ID="$(aws ec2 run-instances \
    --image-id "$AMI" --instance-type "$INSTANCE_TYPE" \
    --key-name "$PREFIX-deploy" --security-group-ids "$SG_ID" \
    --iam-instance-profile "Name=$PREFIX-ec2" \
    --user-data "file://$USER_DATA" \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$PREFIX}]" \
    --query 'Instances[0].InstanceId' --output text)"
  echo "  created: $INSTANCE_ID"
fi
aws ec2 wait instance-running --instance-ids "$INSTANCE_ID"
EIP_ALLOC="$(aws ec2 describe-addresses --filters "Name=tag:Name,Values=$PREFIX-eip" --query 'Addresses[0].AllocationId' --output text)"
if [[ -z "$EIP_ALLOC" || "$EIP_ALLOC" == "None" ]]; then
  EIP_ALLOC="$(aws ec2 allocate-address --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=Name,Value=$PREFIX-eip}]" --query AllocationId --output text)"
fi
if ! aws ec2 describe-addresses --allocation-ids "$EIP_ALLOC" --query 'Addresses[0].InstanceId' --output text | grep -q "$INSTANCE_ID"; then
  aws ec2 associate-address --allocation-id "$EIP_ALLOC" --instance-id "$INSTANCE_ID" >/dev/null
fi
EIP="$(aws ec2 describe-addresses --allocation-ids "$EIP_ALLOC" --query 'Addresses[0].PublicIp' --output text)"
aws ssm put-parameter --name "/$PREFIX/public-url" --value "http://$EIP" --type String --overwrite >/dev/null
echo "  elastic IP: $EIP"
echo "  public-url parameter set to http://$EIP"

# -------------------------------------------------------------------- DNS
if [[ -n "$DOMAIN" ]]; then
  say "Route53 hosted zone for $DOMAIN"
  ZONE_ID="$(aws route53 list-hosted-zones --query "HostedZones[?Name=='$DOMAIN.'].Id" --output text | cut -d/ -f3)"
  if [[ -z "$ZONE_ID" ]]; then
    ZONE_ID="$(aws route53 create-hosted-zone --name "$DOMAIN" --caller-reference "$DOMAIN-$(date +%s)" --query HostedZone.Id --output text | cut -d/ -f3)"
    echo "  created zone $ZONE_ID — delegate these NS records at your registrar:"
    aws route53 get-hosted-zone --id "$ZONE_ID" --query 'DelegationSet.NameServers' --output text | tr '\t' '\n' | sed 's/^/    /'
  fi
  aws route53 change-resource-record-sets --hosted-zone-id "$ZONE_ID" --change-batch "{
    \"Changes\": [{
      \"Action\": \"UPSERT\",
      \"ResourceRecordSet\": {
        \"Name\": \"$SUBDOMAIN.$DOMAIN\",
        \"Type\": \"A\", \"TTL\": 300,
        \"ResourceRecords\": [{\"Value\": \"$EIP\"}]
      }
    }]
  }" >/dev/null
  aws ssm put-parameter --name "/$PREFIX/public-url" --value "https://$SUBDOMAIN.$DOMAIN" --type String --overwrite >/dev/null
  echo "  A record: $SUBDOMAIN.$DOMAIN -> $EIP"
  echo "  public-url parameter set to https://$SUBDOMAIN.$DOMAIN"
fi

# ------------------------------------------------------------------ Summary
say "Done. Summary"
cat <<EOF
  account:    $ACCOUNT_ID ($REGION)
  instance:   $INSTANCE_ID ($INSTANCE_TYPE) at $EIP
  registry:   $REGISTRY ($PREFIX-api, $PREFIX-worker, $PREFIX-web)
  actions:    arn:aws:iam::$ACCOUNT_ID:role/$PREFIX-github-actions
  ssh:        ssh -i $KEY_FILE ubuntu@$EIP

Next steps:
  1. Add GitHub secrets (gh CLI is authenticated):
       gh secret set -R $REPO DEMO_HOST       # value: $EIP
       gh secret set -R $REPO DEMO_SSH_KEY < $KEY_FILE
  2. Run the test suite, commit, push — or trigger "Deploy demo" manually.
  3. Verify: http://$EIP/demo  (HTTPS once a domain is attached)
EOF
