# ADR-0001: Derive balances from immutable operational ledgers

- Status: Proposed
- Date: 2026-07-28

## Context

The system must show real-time paid/unpaid customer balances and accurate stock
while supporting partial payments, credits, damaged returns, partial
deliveries, corrections, and concurrent users.

Directly editing balance or quantity fields makes history unreconstructable and
causes modules to disagree after retries or corrections.

## Decision

Posted customer receivable changes are immutable customer-ledger entries.
Posted inventory changes are immutable stock movements. Corrections create
reversing and replacement entries. Statements, aging, on-hand, reserved, and
available quantities are rebuildable projections.

Draft and approval documents do not affect posted balances until their posting
transaction commits.

## Consequences

### Positive

- every balance is traceable to source documents;
- duplicate and reversal behavior can be tested explicitly;
- projections can be rebuilt and reconciled;
- partial payment, return, and correction histories remain intact.

### Negative

- posting and reversal workflows are more complex than CRUD balance updates;
- projections require lag monitoring and reconciliation tooling;
- users need explicit correction actions instead of editing posted history.

## Guardrails

- no API directly overwrites posted customer or stock balances;
- posting commands require idempotency keys;
- entries reference source document, actor, time, and reversal where applicable;
- projection rebuild is tested and operationally documented;
- financial and inventory policy changes are effective-dated, not retroactively
  applied without an explicit controlled recalculation.

