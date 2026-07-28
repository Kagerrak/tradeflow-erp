# ADR-0011: Expire only unpaid prepaid reservations

- Status: Accepted
- Date: 2026-07-28

## Context

Automatic expiry of every reservation would break firm wholesale commitments,
while indefinite Prepaid reservations could hold scarce stock without
collection. Payment, expiry, manual release, and fulfillment may also race.

## Decision

On Account and Cash on Delivery reservations persist until fulfillment,
cancellation, an approval-invalidating change, or authorized Reservation
Release. Each Prepaid reservation has a Branch-configured Payment Deadline. If
sufficient Cleared Payment is absent at the deadline, an idempotent command
releases the quantity to Backorder Demand and applies Payment Hold. Later
payment requires a new successful reservation before Pick Release.

## Consequences

Firm B2B commitments are stable, while unpaid Prepaid demand cannot hold stock
indefinitely. Manual release retains actor, reason, quantity, and source.
Payment, release, and fulfillment operations require transactional concurrency
control.
