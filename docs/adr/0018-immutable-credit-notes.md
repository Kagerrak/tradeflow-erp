# ADR-0018: Immutable credit notes under maker-checker control

- Status: Accepted
- Date: 2026-08-14

## Context

The existing `POST /v1/finance/invoices/{id}/credit-notes` shortcut created a
`customer_ledger_entries` row directly from a client-supplied UUID. It had no
`credit_notes` entity, no branch-scoped document numbering, no maker-checker
separation, no read endpoints, and no reversal lifecycle. This made credit
history unauditable and allowed a single user to reduce receivables without
authorization evidence.

## Decision

Replace the shortcut with a controlled credit-note document flow.

A user with `finance:credit-note-request` may request a credit note only against
a posted invoice in the company base currency. The requested amount may not
exceed the invoice's current eligible (open) value after payments, allocations,
prior credits, pending requests, and voids.

A different eligible user with `finance:credit-note-approve`, matching branch
scope, and sufficient `approval_authorities.maximum_amount` authorizes and
posts the note. Operations administrators are not exempt from capability or
authority checks.

Posting consumes the next number from a branch-scoped `credit_note` document
series and records the consumption in `document_series_number_audit`, producing
a stable number such as `CN-MNL-00000001`. The posted note creates one
`customer_ledger_entries` row with `entry_type='credit_note'` and reduces the
customer's `open_balance`.

Reversal preserves the original note and creates exactly one restoring ledger
entry with `source_type='credit_note_reversal'`. A reverser must also be a
different eligible user from the original requester.

The `credit_notes` and `credit_note_authorizations` tables are protected by
database triggers that reject updates and deletes except the allowed status
transitions (`pending_authorization` → `posted`, `posted` → `reversed`) where all
other columns remain unchanged. A deferred trigger enforces maker-checker,
branch, capability, and limit constraints on every authorization row.

Commands require an `Idempotency-Key` header. Replays return the stored response
with `X-Idempotency-Replayed: true`.

## Consequences

Credit history becomes immutable and auditable. Every posted credit has a stable
branch number, an authorization record, and a clear link to the invoice it
reduces. Concurrent requests against the same invoice or note serialize safely
through advisory locks, preventing over-crediting. The review UI must show the
original invoice, eligible value, requester, authorizer, document number, and
reversal chain.
