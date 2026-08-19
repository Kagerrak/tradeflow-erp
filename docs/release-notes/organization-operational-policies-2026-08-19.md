# Configurable Operational Policies and Versioned Document Templates

**Date:** 2026-08-19  
**Issue:** [#108](https://github.com/Kagerrak/tradeflow-erp/issues/108)  
**Branch:** `feature/operational-policies`

## Summary

The first release can now be configured in the field. Company and branch
timezones are editable, base currency can be corrected before any dependent
postings, branch document series are versioned and configurable, and printable
/shareable document templates are stored as immutable versioned Jinja2 bodies.
All changes are exposed through the API and reflected in the generated client;
web and mobile UIs are out of scope for this slice.

## What changed

### API / backend

- Added `timezone` to `companies` and `branches` with a `NOT NULL DEFAULT 'UTC'`
  migration and non-empty check constraints. Bootstrap persists and returns the
  Company timezone.
- Added `PATCH /v1/organization/company` timezone validation against IANA
  timezone identifiers.
- Added `PATCH /v1/organization/branches/{branch_id}/settings` for updating
  branch `name` and `timezone` independently from lifecycle activation.
- Replaced the unconditional base-currency trigger with
  `prevent_base_currency_change_with_postings()`. Base currency is now mutable
  until `stock_movements` or `customer_ledger_entries` exist, after which it is
  immutable in both the application and the database.
- Added `version` to `document_series` and relaxed the document-type check to
  allow any lowercase snake-case identifier matching `^[a-z][a-z0-9_]{1,38}$`.
- Added `PUT /v1/organization/branches/{branch_id}/document-series/{document_type}`
  for create/update with optimistic concurrency, idempotency, version increment,
  and `next_number` regression guard.
- Added `GET /v1/organization/branches/{branch_id}/document-series` for listing.
- Replaced the strict document-series triggers with version-aware versions:
  same-version consumption keeps the old monotonic identity guarantee, while
  version-bumping configuration updates may change `prefix` and `next_number`.
- Added the `document_templates` table with company/branch scope, versioning,
  effective dating, and Jinja2 body storage.
- Added `PUT /v1/organization/document-templates/{document_type}` for company
  templates and
  `PUT /v1/organization/branches/{branch_id}/document-templates/{document_type}`
  for branch templates.
- Added `GET /v1/organization/document-templates/{document_type}` to list
  versions newest first.
- Added `POST /v1/organization/document-templates/{document_template_id}/preview`
  for deterministic sandboxed Jinja2 rendering with sorted context keys.
- Mounted the new `operational_policies_router` under `/v1/organization`.
- Added `jinja2>=3.1.6` dependency and a mypy override for Jinja2 imports.

### Web / generated client

- Regenerated `openapi/openapi.json` and
  `packages/api-client/src/schema.d.ts` with the new endpoints and models.
- No web or mobile UI implementations are included in this slice.

### Tests

- Added `apps/api/tests/test_organization_config_contract.py` covering timezone
  validation, base-currency guard after postings, branch settings update/replay,
  document-series create/update/version/regression, scope denials, template
  versioning, and deterministic preview.
- Added `apps/api/tests/test_organization_config_migration.py` verifying schema
  objects and upgrade/downgrade/re-upgrade safety.
- Updated `apps/api/tests/test_organization_customer_contract.py` for the new
  `timezone` field and the relaxed base-currency guard.

## Out of scope

- Web and mobile UI implementation beyond endpoint exposure and generated-client
  updates.
- Template rendering as PDF or image export.
- Branch-scoped timezone enforcement in business logic (the column is stored and
  returned; future slices will apply it to cutoff and posting timestamps).

## Verification

- Focused API contract tests: 20 passed.
- Full API test suite: 258 passed, 4 skipped.
- `uv run ruff check src tests` — passed.
- `uv run ruff format --check src tests` — passed.
- `uv run mypy src/tradeflow_api/organization.py src/tradeflow_api/operational_policies.py tests/test_organization_config_contract.py tests/test_organization_config_migration.py` — passed.
- `pnpm openapi:generate` — passed and deterministic.

## Deployment notes

- Migration `0019_operational_policies_timezone_templates_.py` extends head
  `aefae8360657` and is reversible to base.
- Existing `companies` and `branches` receive `timezone = 'UTC'` on upgrade.
- Existing `document_series` rows receive `version = 1` on upgrade.
- The original base-currency trigger and document-series triggers are restored
  on downgrade.

## Decision evidence

- ADR: `docs/adr/0019-operational-policies-and-templates.md`
- Checkpoint: `checkpoint/WORKFLOW_CHECKPOINT_2026-08-19-operational-policies.md`
