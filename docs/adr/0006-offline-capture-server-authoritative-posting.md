# ADR-0006: Allow offline capture but keep posting server-authoritative

- Status: Accepted
- Date: 2026-07-28

## Context

Sales and delivery staff need to capture work through poor connectivity, but
offline approval and posting would let devices act on stale permissions,
reservations, stock, and workflow state. Automatically merging those conflicts
could violate inventory and financial invariants.

## Decision

The first mobile release supports cached authorized reference data, offline
Sales Order drafts, and offline Proof of Delivery evidence. Each draft uses a
client-generated identity and idempotent outbox command and remains Pending
Sync. Commercial approval, reservation, operational posting, Delivery
Confirmation, stock movement, and invoice creation require server
acknowledgement. Posting conflicts require explicit review.

## Consequences

Field evidence survives interruption and can upload resumably, while the server
remains authoritative. Users must distinguish captured work from posted work,
and some offline actions will pause at Pending Sync until connectivity returns.
