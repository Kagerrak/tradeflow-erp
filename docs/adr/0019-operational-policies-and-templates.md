# ADR-0019: Configurable operational policies and versioned document templates

- Status: Accepted
- Date: 2026-08-19

## Context

TradeFlow ships with a single Company and immutable branch-scoped document
series, but first-release operators need to configure operational policies without
losing historical snapshots. Before this change the Company had no timezone, the
base currency could not be changed even before any postings existed, document
series identities were locked after creation, and printable document templates
were hard-coded. This blocked multi-timezone operations, late-binding document
layouts, and corrective configuration before go-live.

## Decision

Introduce configurable operational policies and versioned document templates,
all protected by existing capability/scope, optimistic concurrency, and
idempotency patterns.

- `companies` and `branches` gain a required `timezone` column, defaulting to
  `UTC` and validated against IANA timezone identifiers. Bootstrap persists and
  returns the Company timezone; `PATCH /v1/organization/company` and
  `PATCH /v1/organization/branches/{id}/settings` can update it.

- Base currency is immutable only after dependent postings exist. The guard
  checks `stock_movements` and `customer_ledger_entries` in the application layer
  and is enforced by a database trigger on `companies.base_currency` updates.
  Before any dependent postings, administrators may correct the base currency.

- Branch settings (`name`, `timezone`) are updated through a dedicated
  `PATCH /v1/organization/branches/{id}/settings` endpoint that uses `If-Match`
  and `Idempotency-Key` headers and preserves the branch lifecycle endpoint for
  activation/deactivation.

- Document series become configurable per branch. A new `version` column tracks
  every policy change. `PUT /v1/organization/branches/{id}/document-series/{type}`
  creates a series with `If-Match: 0` or updates it with the current version.
  The application rejects `next_number` regression, and the database preserves
  monotonic sequence identity for same-version consumption while allowing
  version-bumping configuration changes to `prefix` and `next_number`.

- Document templates are immutable, versioned, and optionally branch-scoped.
  `PUT /v1/organization/document-templates/{type}` and
  `PUT /v1/organization/branches/{id}/document-templates/{type}` append a new
  version. `GET /v1/organization/document-templates/{type}` lists versions in
  descending order. `POST /v1/organization/document-templates/{id}/preview`
  renders a deterministic preview from a Jinja2 sandboxed template with sorted
  context keys.

- Commands continue to require `Idempotency-Key` and, where applicable,
  `If-Match`. Replays return the stored response with
  `X-Idempotency-Replayed: true`.

## Consequences

Administrators can now configure timezones, correct base currency before
postings, and manage document series and templates without code changes. Every
template and document-series change is versioned, creating an auditable history
for the first release. Existing delivery-receipt and credit-note consumption
continues to enforce strict monotonic numbering because those updates do not
bump the series version. UI teams can consume the new endpoints through the
regenerated OpenAPI client; no web or mobile screens are included in this slice.
