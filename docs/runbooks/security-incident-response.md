# Security incident response runbook

This runbook describes how to respond to common security events in TradeFlow ERP.

## Suspected credential leak

1. **Contain.** Rotate the leaked credential immediately. For OIDC, rotate the
   client secret at the identity provider. For database or object storage
   credentials, rotate them in the secret store and update environment
   variables.
2. **Verify.** Search logs and repositories for the leaked value. The CI
   `security.yml` workflow blocks test secrets in staging/production files.
3. **Revoke.** Invalidate active sessions and force re-authentication. Remove
   compromised Approval Authority rows if a user account is involved.
4. **Review.** Check audit logs for actions performed with the leaked credential.

## Unauthorized access attempt

1. **Identify.** Correlate failed authentication/authorization logs by subject,
   IP address, and correlation ID.
2. **Block.** If rate limiting is enabled, repeated failures will return
   `429 rate_limit_exceeded`. For persistent attackers, block the source IP at
   the load balancer or WAF.
3. **Alert.** Forward `invalid_token`, `capability_required`, and
   `approval_limit_exceeded` spikes to the security operations channel.

## Malicious insider / over-limit approval

1. **Suspend.** Disable the user account and revoke role templates, scopes, and
   approval authorities.
2. **Investigate.** Query immutable source-linked movements, payment receipts,
   and approval records for the subject.
3. **Remediate.** Post reversing entries where supported by the business domain;
   otherwise coordinate with finance and operations stakeholders.

## Dependency vulnerability

1. The `security.yml` dependency-review job flags vulnerable dependencies on
   pull requests.
2. Review the advisory and determine whether TradeFlow is exposed.
3. Update the dependency and re-run the full CI pipeline.
4. Deploy the patched build through staging before production.

## Evidence preservation

- Do not delete logs during an active investigation.
- Capture correlation IDs, timestamps, affected subjects, and affected source
  document IDs.
- Store evidence outside the production environment.
