# ADR-0016: Clear payments by method-specific evidence

- Status: Accepted
- Date: 2026-07-28

## Context

Cash is physically available at collection, while transfer, check, and
manually recorded electronic payments may be unverified, duplicated, rejected,
or later reversed. Treating every recorded receipt as cleared could release
Prepaid work or confirm COD delivery against unavailable funds.

## Decision

Cash collected by an authorized User clears immediately and is later subject to
Cash Reconciliation. Bank-transfer, check, and manually recorded electronic
Payment Receipts begin Pending Verification. Another eligible Finance User
performs Payment Verification using evidence, value date, account or provider,
and External Payment Reference. Active references are unique per Company and
Payment Method. Checks clear only after bank clearance. Approved providers may
confirm electronic settlement through the same contract. Rejections and
reversals preserve immutable history.

## Consequences

Prepaid and COD gates depend only on Cleared Payment. Non-cash collection needs
a maker-checker queue and duplicate-reference controls. Future payment gateways
can supply provider confirmation without changing the receipt lifecycle.
