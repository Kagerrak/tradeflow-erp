# ADR-0007: Reserve before prepayment and collect before pick

- Status: Accepted
- Date: 2026-07-28

## Context

Collecting before reservation can take funds for unavailable stock, while
waiting until dispatch can consume warehouse effort for an unpaid Prepaid
order. Partial reservation also means the amount ready for fulfillment may be
less than the total Sales Order.

## Decision

Commercial Approval attempts Inventory Reservation before Prepaid collection.
Cleared Customer Prepayment covering the reserved quantity selected for a
Fulfillment Order is required before Pick Release. Backorder Demand is not
collected until later reserved. Prepayment remains unapplied until the related
Delivery's Invoice posts, when it is allocated.

## Consequences

Customers pay only for fulfillable quantity and warehouses do not pick unpaid
Prepaid demand. Cancellation or reduction preserves the immutable Payment
Receipt; Finance must refund it or retain it as Unapplied Payment through an
approved workflow.
