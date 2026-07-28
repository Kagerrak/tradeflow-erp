# ADR-0008: Confirm COD delivery with collection

- Status: Accepted
- Date: 2026-07-28

## Context

Cash on Delivery allows fulfillment before payment, but confirming stock
outbound without recording collection would create an unintended receivable.
Collection can also differ when the customer accepts only part of a delivery.

## Decision

Server-accepted Delivery Confirmation for Cash on Delivery atomically records
accepted quantity, Proof of Delivery, and a sufficient COD Payment Receipt.
Cash collected by an authorized delivery user is cleared immediately and later
reconciled; other Payment Methods follow configured verification. The receipt
remains unapplied until the related Invoice posts. Unpaid accepted value
requires authorized conversion to On Account and any required Credit Override.

## Consequences

COD collection matches accepted quantity and cannot be separated from delivery
posting. Offline capture remains Pending Sync. Cash custody needs a
reconciliation workflow, and method-specific verification determines whether a
non-cash receipt can satisfy COD.
