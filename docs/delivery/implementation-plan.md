# TradeFlow ERP delivery roadmap

Deliver thin end-to-end business slices before attempting every module.

The evidence-based status of this roadmap and the dependency-aware tracker for
remaining first-release work are recorded in
[`first-release-reconciliation.md`](./first-release-reconciliation.md). A
closed tracker item is not completion evidence unless the corresponding code,
tests, migration, cross-platform workflow, reconciliation, and release gates
also pass.

The product matrix is not a substitute for legacy discovery. Every behavior
found in the current system must be classified as implemented and verified,
retired with named owner approval, or covered by a controlled temporary bridge
as defined in `first-release-reconciliation.md`.

## Phase 0: Discovery and migration baseline

- Inventory existing screens, workflows, reports, roles, integrations, data
  exports, and source data stores.
- Observe high-frequency user workflows and measure current task time/errors.
- Confirm company, branch, warehouse, currency, tax, valuation, numbering,
  approval, invoicing, payment, and commission policies.
- Produce legacy-data mapping and migration-quality report.

Exit: unresolved accounting and inventory policies are visible, not hidden in
implementation assumptions.

## Phase 1: Platform and master data

- Identity, roles, branches, warehouses, approval limits, and audit.
- Customer, supplier, product, SKU, unit, price-list, and opening-data imports.
- Next.js and Expo shells using the generated API client.
- PostgreSQL migrations, CI, telemetry, backup, and environment strategy.

Exit: master data is searchable and permission-scoped on web and mobile.

## Phase 2: Order-to-delivery slice

- Sales order, pricing, discount/credit approval, and reservations.
- Warehouse pick/dispatch and partial delivery.
- Mobile proof of delivery and delivery exceptions.
- Delivery receipt generation.

Exit: one order can be partially delivered with auditable stock movements.

## Phase 3: Invoice-to-cash slice

- Invoice posting policy.
- Payment receipt and allocation.
- Credit notes and adjustments.
- Customer ledger, aging, and statement of account.
- Reconciliation and projection-rebuild commands.

Exit: partial payments and credits update the statement immediately and
reconcile to immutable entries.

## Phase 4: Returns and damaged items

- Return authorization against delivered quantity.
- Mobile evidence, receipt, inspection, and disposition.
- Quarantine/damaged stock locations.
- Replacement, write-off, supplier return, and credit outcomes.

Exit: returned goods never silently re-enter available stock.

## Phase 5: Procurement

- Local purchase order, approval, partial receipt, variance, and supplier return.
- International currency, inbound shipment, document, and landed-cost workflow.
- Reorder suggestions as a reviewable recommendation, not automatic purchasing.

Exit: received goods and acquisition costs are traceable to source documents.

## Phase 6: Expenses and commissions

- Expense capture, evidence, approval, posting, and payment state.
- Commission plans, effective dates, attribution, accruals, reversals, and
  participant statements.
- Controlled recalculation with versioned rules.

Exit: an invoice payment and later return produce explainable commission entries.

## Phase 7: Hardening and migration

- Parallel-run reconciliation against the existing system.
- Permission, security, accessibility, load, restore, and failure testing.
- EAS store builds, staged rollout, support runbooks, and training.
- Cutover rehearsal, rollback criteria, and audit sign-off.
- After an explicit Go decision, execute the final production migration and
  reconcile frozen-source and imported cutover control totals before day-one
  activation.

Exit: business owners approve the reconciled opening and closing balances, the
final production migration evidence, and the explicit activation decision.
