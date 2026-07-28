# ADR-0015: Authorize by capability, scope, and limits

- Status: Accepted
- Date: 2026-07-28

## Context

Hard-coded role names cannot express branch and warehouse assignments,
financial limits, maker-checker separation, or delivery assignment. Treating an
administrator as a universal business approver would also bypass operational
controls.

## Decision

Every business command checks a server-enforced Capability, Operational Scope,
and any required Approval Authority limits. Configurable Role Templates provide
defaults for sales, warehouse, delivery, finance, and administration.
Maker-Checker requires a different eligible approver. Delivery Staff access is
limited to assigned Deliveries. Operations Administrator configures users,
roles, scopes, master data, and policies but receives no business approval
authority merely by being an administrator.

## Consequences

Web and mobile clients can hide unavailable actions for usability but cannot
authorize them. Tests must cover capability, scope, limits, assignment,
maker-checker, and administrator non-escalation at API and database boundaries.
