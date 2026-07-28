# ADR-0013: Preserve stock custody through delivery exceptions

- Status: Accepted
- Date: 2026-07-28

## Context

Moving stock directly from warehouse on-hand to delivered or immediately
restoring refused goods to Available would lose custody during transit and
bypass inspection. Short, damaged, and refused outcomes must remain
reconcilable to dispatched quantity.

## Decision

Picking transfers reserved stock from Available to Dispatch Staging. Dispatch
moves it to In Transit custody. Delivery Confirmation partitions each
dispatched line exactly into accepted, refused, damaged, short or missing, and
still-undelivered quantity. Accepted quantity posts outbound. Refused or
damaged quantity stays In Transit until Return-to-Warehouse Receipt moves it to
Quarantine. Short or missing quantity moves to Investigation until an approved
recovery, claim, or Inventory Adjustment resolves it.

## Consequences

Every dispatched unit retains a traceable custody state and exceptions cannot
silently re-enter sellable inventory. Availability, warehouse on-hand, and
company-custodied quantity are distinct projections, and retry-safe confirmation
must enforce exact partition equality.
