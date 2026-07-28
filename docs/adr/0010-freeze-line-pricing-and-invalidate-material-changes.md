# ADR-0010: Freeze line pricing and invalidate material changes

- Status: Accepted
- Date: 2026-07-28

## Context

Effective-dated prices, order-wide discounts, tax, manual overrides, and
approval thresholds can otherwise produce historical totals that cannot be
reconstructed. Allowing commercial fields to change after approval would also
leave stale credit and inventory commitments.

## Decision

Customer-specific Price Lists precede Branch defaults. Commercial Approval
freezes a line-level Pricing Snapshot containing price source, list price,
manual override, allocated discount, tax treatment, and final unit price.
Order-wide discounts allocate explicitly to lines. Excessive discount or
below-floor price requires maker-checker approval. Changing Customer, Branch,
fulfillment Warehouse, quantity, price, discount, Tax Code, or Payment Timing
Policy invalidates Commercial Approval and reruns pricing, credit, and
reservation checks.

## Consequences

Approved totals remain reproducible and downstream commitments cannot silently
outlive their commercial basis. User interfaces must distinguish material
changes from notes or delivery instructions that do not invalidate approval.
