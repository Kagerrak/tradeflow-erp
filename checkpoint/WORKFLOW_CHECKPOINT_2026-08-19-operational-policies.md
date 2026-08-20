# TradeFlow workflow checkpoint

- Date: August 19, 2026
- Phase: Issue #108 ready for final review — Configurable Operational Policies
  and Versioned Document Templates
- Branch: `feature/operational-policies`
- Release PR: to be created after final independent review and full CI gate

## Established

- Added migration `apps/api/migrations/versions/0019_operational_policies_timezone_templates_.py`
  extending `aefae8360657` with:
  - `timezone` columns and non-empty constraints on `companies` and `branches`.
  - `version` column and relaxed document-type check on `document_series`.
  - `document_templates` table with versioning, effective dating, and Jinja2 body
    storage plus unique indexes.
  - Version-aware replacement of the base-currency and document-series triggers.
- Extended `apps/api/src/tradeflow_api/organization.py` with timezone fields,
  IANA validators, base-currency posting-count guard, and
  `PATCH /v1/organization/branches/{branch_id}/settings`.
- Added `apps/api/src/tradeflow_api/operational_policies.py` mounted in
  `apps/api/src/tradeflow_api/app.py` with endpoints for document series and
  document templates including deterministic Jinja2 preview.
- Added `jinja2>=3.1.6` dependency in `apps/api/pyproject.toml` and a mypy
  override in workspace `pyproject.toml`.
- Added focused contract tests in
  `apps/api/tests/test_organization_config_contract.py` covering timezone,
  base-currency guard, branch settings, document series versioning/regression,
  template versioning, preview determinism, scope denials, and idempotent
  replays.
- Added migration safety tests in
  `apps/api/tests/test_organization_config_migration.py` verifying schema
  objects and downgrade/re-upgrade round-trips.
- Updated `apps/api/tests/test_organization_customer_contract.py` for the new
  `timezone` field and the relaxed base-currency guard.
- Regenerated OpenAPI contract (`openapi/openapi.json`) and TypeScript client
  (`packages/api-client/src/schema.d.ts`).
- Added ADR at `docs/adr/0019-operational-policies-and-templates.md` and release
  notes at `docs/release-notes/organization-operational-policies-2026-08-19.md`.

## Verification evidence

- `uv run pytest -q apps/api/tests/test_organization_customer_contract.py apps/api/tests/test_organization_config_contract.py apps/api/tests/test_organization_config_migration.py` — **20 passed**.
- `uv run pytest -q apps/api/tests` — **258 passed, 4 skipped**.
- `uv run ruff check src tests` — passed.
- `uv run ruff format --check src tests` — passed.
- `uv run mypy src/tradeflow_api/organization.py src/tradeflow_api/operational_policies.py tests/test_organization_config_contract.py tests/test_organization_config_migration.py` — passed.
- `pnpm openapi:generate` — passed and deterministic.
- `git diff --check` — passed.

## Final review

- One independent standards/specification review is pending after the green
  draft PR is published.

## Remaining gate

- Push branch, open a PR referencing `Closes #108`, confirm full CI is green, and
  obtain explicit merge approval before squash-merging.

## Deferred scope

- Web and mobile UI screens for policy/template management.
- PDF/image rendering for document templates.
- Timezone-aware business-logic cutoffs and posting timestamps.

## Next issue

- #110 — Baseline current workflows and measure first-release success outcomes
  (Phase 0 discovery / scope lock).
