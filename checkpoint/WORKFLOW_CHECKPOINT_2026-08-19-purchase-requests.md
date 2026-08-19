# TradeFlow workflow checkpoint

- Date: August 19, 2026
- Phase: Issue #77 — Create and approve Purchase Requests before Purchase Orders
- Branch: `feature/purchase-requests`
- Base: `origin/main` at `f195560143a93fb2f95341bb89e9111eaf203b57`

## Implementation card

- Outcome: Procurement can create, revise, approve, and partially convert Purchase Requests to linked Purchase Order drafts, with branch scope, maker-checker, and quantity limits.
- Persona: Procurement buyer (writer), procurement manager/approver.
- Start: Purchase Orders exist but have no upstream request workflow. End: approved requests feed partial PO conversions; open request quantity is explicit; concurrent conversions cannot exceed the approved remainder.
- Invariants: request lines track open quantity via linked PO lines; status derives from conversion progress; conversions are immutable and linked to both request and PO; maker cannot approve their own request; approver must have branch-scoped approval authority; branch scope enforced on every command.
- Authorization/scope: capabilities `procurement:purchase-request-read/write/approve`; branch scope on all commands; `procurement:purchase-order-write` for conversion; approval authority table enforces optional value limit.
- Financial/stock effects: no stock/finance effect until the resulting PO is approved and received. Conversion creates a draft PO only.
- Reliability: idempotent create and conversion via stored idempotency keys; optimistic version on revise/approve; advisory lock on request_id for conversion; rollback leaves no partial state.
- Dependencies: supplier directory (#42), purchase order creation (#43), goods receipt foundation, branch/scope/auth.
- Replacement requirement: closes the missing purchase-request gate in first-release procurement.
- Non-goals: receipt variance, supplier return, landed-cost rules, general ledger, autonomous approvals.

## Planned changes

- Alembic migration for `purchase_requests`, `purchase_request_lines`, and
  `purchase_order_lines.purchase_request_line_id`.
- Auth helpers for `procurement:purchase-request-*` capabilities.
- `apps/api/src/tradeflow_api/purchase_requests.py` with create, revise, get,
  list, approve, reject, and convert endpoints.
- Web workspace for purchase requests under `/procurement/purchase-requests`.
- Contract tests for lifecycle, partial conversion, concurrency, idempotency,
  scope, and maker-checker.
- Regenerate OpenAPI if contract changes.

## Verification

- `uv run pytest -q apps/api/tests/test_purchase_request_contract.py` passes.
- `pnpm format`, `pnpm lint`, `pnpm typecheck` pass.
- Full CI gate green before requesting merge approval.
