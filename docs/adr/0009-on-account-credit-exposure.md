# ADR-0009: Calculate and serialize on-account credit exposure

- Status: Accepted
- Date: 2026-07-28

## Context

Checking only posted receivables ignores approved orders that are already
committed but not yet invoiced. Counting both an order and its posted Invoice
would instead duplicate exposure, while concurrent approvals could each observe
the same remaining credit.

## Decision

Credit Exposure equals posted Open Balance plus approved, uncancelled Sales
Order value not yet represented by a posted Invoice. Posting an Invoice
atomically replaces its matching uninvoiced exposure. Commercial Approval for
one Customer Account serializes this check. Credit Hold blocks approval, and an
absent or exceeded Credit Limit requires an order-specific Credit Override with
its approval evidence and exposure snapshot.

## Consequences

On Account commitments consume credit before invoicing without double-counting
after posting. Approval requires contention control per Customer Account, and
cancellations, reductions, payments, credits, and reversals must update exposure
through their immutable workflows.
