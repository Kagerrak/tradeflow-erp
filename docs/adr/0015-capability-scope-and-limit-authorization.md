# ADR-0015: Authorize by capability, scope, and limits

- Status: Accepted
- Date: 2026-07-28

## Context

Hard-coded role names cannot express branch and warehouse assignments,
financial limits, maker-checker separation, or delivery assignment. Treating an
administrator as a universal business approver would also bypass operational
controls.

Some capabilities must be authorized at Branch level (e.g., cross-warehouse
commercial approval), while others must be enforceable per Warehouse (e.g.,
authorizing a delivery correction whose stock effects are scoped to the
warehouse that fulfilled the delivery). A single Branch-only grain would force
us to either over-extend authority across warehouses or create duplicate
capability codes.

## Decision

Every business command checks a server-enforced Capability, Operational Scope,
and any required Approval Authority limits. Configurable Role Templates provide
defaults for sales, warehouse, delivery, finance, and administration.
Maker-Checker requires a different eligible approver. Delivery Staff access is
limited to assigned Deliveries. Operations Administrator configures users,
roles, scopes, master data, and policies but receives no business approval
authority merely by being an administrator.

`approval_authorities` supports an optional `warehouse_id` scope in addition to
`branch_id`. A `NULL` warehouse_id grants Branch-level authority for the
capability; a non-NULL `warehouse_id` grants authority only for that warehouse
within the branch. The same user may hold both grains for the same capability
and branch, enforced by two partial unique indexes:

- `uq_approval_authority_branch` unique on `(user_subject, capability_code,
branch_id)` where `warehouse_id IS NULL`;
- `uq_approval_authority_warehouse` unique on `(user_subject, capability_code,
branch_id, warehouse_id)` where `warehouse_id IS NOT NULL`.

For delivery-correction authorizations, the database trigger rejects an
authority whose `warehouse_id` is non-NULL and does not match the correction's
warehouse. API bootstrap and user-configuration flows validate that a
warehouse-scoped authority references a known warehouse in the same branch and
that the user is assigned to that warehouse. Downgrade migrations that would
remove the warehouse grain refuse to run while warehouse-scoped authorities
exist, preventing silent data loss.

## Consequences

Web and mobile clients can hide unavailable actions for usability but cannot
authorize them. Tests must cover capability, scope, limits, assignment,
maker-checker, and administrator non-escalation at API and database boundaries.

Scope tests now include both branch-level and warehouse-level authority grains:
branch-level authority still approves any correction in the branch, while
warehouse-scoped authority is rejected by the database trigger when it does not
match the correction's warehouse. Migrations tests verify that the new partial
indexes coexist with legacy branch authorities and that downgrade is blocked
when warehouse-scoped authorities would be lost.
