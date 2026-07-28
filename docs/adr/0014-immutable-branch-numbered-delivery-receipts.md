# ADR-0014: Issue immutable branch-numbered delivery receipts

- Status: Accepted
- Date: 2026-07-28

## Context

Delivery Receipt identity must survive retries and corrections while remaining
traceable to accepted delivery quantities. Editing or renumbering issued
documents would make customer copies, stock movements, and invoice sources
disagree.

## Decision

One server-accepted Delivery Confirmation issues one immutable Delivery Receipt
for accepted quantity. A Branch-specific Document Series assigns a unique,
never-reused number and records voided or skipped numbers. The receipt snapshots
customer, address, product, unit, quantity, and source-order information. Proof
of Delivery remains separate linked evidence. Retries reuse receipt identity;
corrections create authorized, reasoned reversal and replacement records,
movements, and receipts as needed.

## Consequences

Issued receipts remain reproducible and auditable across clients and document
workers. Number allocation must be transactional and idempotent, and correction
workflows must preserve every original reference.
