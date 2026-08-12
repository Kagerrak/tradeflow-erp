# ADR-0017: Correct delivery receipts by linked reversal and replacement

- Status: Accepted
- Date: 2026-08-11

## Context

An issued Delivery Receipt may contain an incorrect accepted quantity even
though its number, snapshot, Proof of Delivery, stock posting, and Draft
Invoice source have already become authoritative. Editing any of those records
would make customer copies, inventory valuation, billing preparation, and
projection rebuilds disagree.

## Decision

An authorized requester proposes one complete corrected Delivery Confirmation
partition, including every tracked Lot or Serial Identity, against the current
Delivery Receipt chain head. A different eligible approver authorizes and posts
the proposal under Branch and Warehouse scope and explicit affected-value
authority.

Posting preserves the original Confirmation, evidence, Delivery Receipt,
document number, stock movements, and Draft Invoice. It creates immutable
reversal and replacement Stock Movements at the original outbound unit cost,
linked reversal and replacement Draft Invoice sources without customer-ledger
entries, and replacement Delivery Exception custody records. When corrected
accepted quantity remains, a replacement Delivery Receipt receives a new
Branch Document Series number and links bidirectionally to the prior receipt.
When accepted quantity becomes zero, no replacement receipt or number is
created.

Only the current correction-chain head is eligible. A correction is rejected
after its Draft Invoice posts or after exception quantity has been returned,
resolved, or assigned to a retry Delivery; those later workflows require their
own correcting transactions. Commands, outbox handlers, generated documents,
and effect identities are idempotent.

## Consequences

Every customer-visible and operational version remains readable, numbers are
never reused, and inventory quantity, value, Moving Average Unit Cost, custody,
and invoice preparation can be rebuilt from immutable history. The review UI
must show the original snapshot, complete proposed partition, maker-checker
separation, expected stock and document effects, and the full correction chain.
