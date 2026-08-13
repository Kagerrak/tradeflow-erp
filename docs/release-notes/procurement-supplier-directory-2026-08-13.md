# TradeFlow ERP — Procurement Supplier Directory Release Notes

Release: procurement supplier directory vertical  
Date: 2026-08-13  
Release branch: `feat/supplier-directory`  
Release PR: #47 — https://github.com/Kagerrak/tradeflow-erp/pull/47  
Migrations: `79a7b271a628_supplier_directory_for_procurement.py`

## Scope

This release adds the first Procurement vertical slice: a company-scoped
supplier directory. It introduces the `suppliers` table, capability-based read
and write authorization, and a web console page so Procurement operators can
register and search suppliers.

## Integrated pull requests

| Issue | PR  | Title                              |
| ----- | --- | ---------------------------------- |
| #42   | #47 | Supplier directory for procurement |

## Runtime environment

- Python: `>=3.13,<3.14` (managed by `uv`)
- Node.js: `>=22` (package manager `pnpm@9.7.0`)
- PostgreSQL: 17+ (see `infra/compose.yaml` and `infra/postgres/init/`)
- Object storage: S3-compatible (local MinIO in compose, configurable via env)
- Worker: `arq` Redis-backed async worker
- Mobile: Expo SDK 57 (iOS, Android, web)
- Web: Next.js 16 / Turbopack

## Verification evidence

All gates passed on `feat/supplier-directory`:

- `uv run ruff check apps/api/src apps/api/tests apps/worker/src` — passed.
- `uv run ruff format --check .` — passed.
- `uv run mypy apps/api/src apps/worker/src` — passed.
- `pnpm format` — passed.
- `pnpm lint` — passed.
- `pnpm typecheck` — passed.
- `pnpm test` — passed.
  - Full Python pytest suite: **152 passed, 4 skipped**.
  - Playwright web suite: **134 passed, 10 skipped**.
- `pnpm build` / `uv build --all-packages` — passed.
- `git diff --check` — passed.
- Alembic `upgrade head` / `downgrade -1` / `upgrade head` on a clean test
  database — passed.

## What changed

- Added `suppliers` table with `supplier_id`, `company_id`, `code`, `legal_name`,
  `tax_id`, `payment_terms`, `default_currency`, `is_active`, `version`,
  `created_by`, `created_at`, and `updated_at`, plus a unique constraint on
  `(company_id, code)` and indexes on `code` and `legal_name`.
- Added `procurement:supplier-read` and `procurement:supplier-write`
  capabilities and company-scoped authorization guards.
- Added `POST /v1/procurement/suppliers` to register a supplier, rejecting
  duplicate codes with `supplier_code_duplicate`.
- Added `GET /v1/procurement/suppliers` to list and search suppliers by code or
  legal name, with `limit` and `offset` pagination.
- Regenerated `openapi/openapi.json` and `packages/api-client/src/schema.d.ts`.
- Added `@tradeflow/procurement-suppliers` workspace package with `searchSuppliers`
  and `createSupplier` helpers.
- Added `/procurement/suppliers` web console page:
  - `apps/web/app/procurement/suppliers/page.tsx`
  - `apps/web/components/procurement-suppliers-workspace.tsx`
  - `apps/web/app/api/procurement/suppliers/route.ts`
- Added Suppliers navigation item in `apps/web/components/tradeflow-shell.tsx`
  and supporting CSS in `apps/web/app/procurement/procurement.css`.
- Added contract tests (`apps/api/tests/test_supplier_directory_contract.py`)
  covering supplier creation, duplicate-code rejection, capability gating,
  missing-field validation, listing, search by code and legal name, pagination,
  and unauthenticated requests.
- Updated `apps/api/tests/test_delivery_correction_migration.py` to reflect the
  new Alembic head after the supplier migration.

## Known limitations / next slices

- Supplier details are read-only in this slice; updates and lifecycle changes are
  planned in subsequent Procurement issues (#43–#46).
- The web console uses the existing test-access-token pattern; production
  authentication will be wired when the identity layer is finalized.
