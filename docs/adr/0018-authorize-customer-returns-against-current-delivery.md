# ADR-0018: Authorize customer returns against current delivered quantity

- Status: Accepted
- Date: 2026-08-13

## Context

Customer returns must not exceed what was effectively delivered. Delivery
Corrections may replace an issued receipt and change accepted quantity, while
concurrent Return Requests may compete for the last eligible quantity. Treating
a pending request as a reservation would let abandoned drafts block legitimate
returns; treating authorization as an unguarded approval would permit
over-return.

## Decision

A Return Request references only the current Delivery Receipt correction-chain
head and snapshots its effective delivery line, classification, responsibility,
and proportional sales value. The request is immutable after creation and has
no stock, invoice, ledger, or outbox posting effect.

Maker-checker Return Authorization acquires the Delivery Receipt chain lock and
recomputes each line as effective Delivered Quantity less all prior authorized
quantity. Authorization reserves the approved quantity atomically. Return
Receipt later consumes that reservation rather than subtracting both the
authorization and its receipt. An authorized customer return makes Delivery
Correction ineligible; later changes require an explicit Returns correction or
cancellation workflow.

Authorization requires capability, Branch and fulfillment-Warehouse scope, a
different requester and approver, and sufficient Approval Authority against the
snapshotted proportional sales value. PostgreSQL triggers repeat eligibility,
scope, limit, and maker-checker checks for direct writes.

## Consequences

Concurrent final-quantity approvals yield one success and one conflict, exact
command replay is stable, and later Return Receipt/Inspection slices can attach
physical identities without changing the authorization record. This slice does
not yet capture offline evidence, receive stock, inspect condition, choose a
Disposition, or create a credit/replacement.
