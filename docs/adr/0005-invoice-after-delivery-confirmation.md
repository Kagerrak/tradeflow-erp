# ADR-0005: Create invoices after delivery confirmation

- Status: Accepted
- Date: 2026-07-28

## Context

Creating invoices at order approval or dispatch can charge customers for goods
they never accept, especially when fulfillment is partial or has delivery
exceptions. Delaying all invoice creation for manual Finance entry would weaken
traceability between physical delivery and billing.

## Decision

Server-accepted Delivery Confirmation creates one Draft Invoice idempotently
for that Delivery's accepted quantities. Refused, short, damaged, and
undelivered quantities are excluded. Each partial Delivery creates its own
Draft Invoice. Finance approval posts the Invoice and its customer-ledger
entries.

## Consequences

Every billed quantity traces to one confirmed Delivery, repeated events cannot
create duplicate drafts, and drafts do not affect receivables. Consolidated
invoicing is outside the first release and would require a later policy
decision.
