# ADR-0002: Assign tracked stock identities at pick

- Status: Accepted
- Date: 2026-07-28

## Context

Lot- and serial-tracked stock must remain traceable without making commercial
approval depend on identities that warehouse staff have not yet handled.
Assigning identities during reservation would provide earlier allocation but
would create stale assignments as stock locations, expiry priorities, and
fulfillment plans change.

## Decision

Inventory reservation commits eligible quantity for one SKU and Warehouse
without assigning a Lot Identity or Serial Identity. Picking assigns every
identity required by the SKU's Tracking Policy. Expiration-controlled picking
defaults to FEFO, prohibits expired stock, and requires an authorized reason to
select another eligible lot.

## Consequences

Reservations remain stable while warehouse staff retain control of physical
selection. Availability and pick validation must still account for tracked
identity eligibility, and concurrent picks must prevent the same lot quantity
or serial identity from being posted twice.
