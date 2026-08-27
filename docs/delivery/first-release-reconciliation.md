# First-release scope reconciliation

- Date: August 28, 2026
- Baseline: `origin/main` after PR #112 (Return Authorization) and PR #114
  (Inventory Transfer / Adjustment) merged
- Tracker reconciliation: GitHub issue #55 (closed)
- Release decision: Issue #107 / PR #114 have landed and are no longer blocking
  the first release. The two PR #114 / ADR-0019 business-policy decisions are
  resolved, issue #107 part 2 (counted-variance Inventory Adjustments) is
  implemented, the full release gate has passed, and PR #114 received explicit
  approval from the repo owner (recorded in PR comment
  https://github.com/Kagerrak/tradeflow-erp/pull/114#issuecomment-5359718827;
  GitHub self-approval is blocked in this single-collaborator repository).
  PR #112 (Return Authorization) was approved by the repo owner and merged,
  adding immutable Return Requests with maker-checker authorization.
- Pending evidence: Issue #72 closed by PR #116; Issue #110 current-system
  baseline closed by PR #117; Issue #77 in progress via PR #118.

## Why the tracker was reopened

The previously closed tracker represented the order-to-delivery, initial
invoice/payment/statement, and initial procurement slices that had been
implemented. It did not represent the complete first-release scope in
`docs/product/product-requirements.md` and
`docs/delivery/implementation-plan.md`.

Repository inspection found no Returns API or persistence module, no Expenses
or Commissions implementation, and no consolidated customer-history/reporting
module. Finance and Procurement have useful foundations but do not satisfy all
of their first-release requirements. The absence of open implementation issues
therefore was not evidence that TradeFlow's first release was complete.

## Replacement classification contract

This matrix reconciles the declared product requirements. It does **not** prove
that every behavior in the current business system has been discovered. Issue
#110 owns the current-system inventory of workflows, reports, roles,
integrations, exports, and data sources. Each discovered item must receive
exactly one approved day-one classification:

1. implemented and verified in TradeFlow;
2. intentionally retired with a named business-owner approval; or
3. covered by a temporary bridge with an owner, risks, controls, removal date,
   and migration path.

No approved retirements or temporary bridges are currently recorded. Until
#110 supplies the source inventory and approvals, absence from this product
matrix is **unknown scope**, not evidence that a legacy behavior is unnecessary.

## Readiness snapshot

At this baseline, the coarse planning matrix below contains 8 shipped-
foundation rows, 15 partial rows, and 13 missing rows. These rows are not atomic
requirements and therefore are not assigned a completion percentage. Issue
#110 must establish stable legacy-inventory identifiers and the classification
rubric before quantitative replacement coverage can be reported. Legacy
discovery, migration, security/device/performance/recovery qualification,
parallel run, cutover rehearsal, UAT, audit sign-off, and the explicit go-live
decision remain incomplete.

## Requirement-level evidence matrix

Each row maps a product requirement to concrete repository evidence and a
remaining issue. “Partial” means the cited foundation exists but the complete
PRD behavior or release evidence does not.

| Product requirement                                                                                                     | Repository evidence on `origin/main`                                                                                                                                                                                                                                                                    | Status and remaining tracker                                                                          |
| ----------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| Customer account, status, terms, credit, tax identity, contacts and versioned addresses                                 | `customers.py`, migration `0003_customers.py`, and `test_organization_customer_contract.py`                                                                                                                                                                                                             | Shipped foundation                                                                                    |
| Assigned salesperson and customer-specific pricing                                                                      | `customers.py`, `sales.py`, `commercial_approval.py`, and `test_sales_order_draft_contract.py`                                                                                                                                                                                                          | Shipped foundation                                                                                    |
| Consolidated customer sales, delivery, return, payment and balance timeline                                             | Only the Customer directory and `customer_statement.py` exist; there is no cross-context reporting module or export worker                                                                                                                                                                              | Missing: #61, #94–#96                                                                                 |
| Products, SKUs, units, Warehouses, Stock Locations and tracked identities                                               | `catalog_inventory.py`, migrations `0006_catalog_inventory.py` and `0010_tracked_stock_picking.py`, and their contract tests                                                                                                                                                                            | Shipped foundation                                                                                    |
| Receipts, reservations, deliveries, custody and immutable inventory projections                                         | `catalog_inventory.py`, `inventory_projection_service.py`, `goods_receipts.py`, delivery modules, `inventory_movements.py` (source-cost transfers with receipt authorization), `inventory_adjustments.py` (counted-variance adjustments), migrations `0018`/`0022`, and their contract/Playwright tests | Partial: customer returns/damage #56, #65–#70; Inventory Transfer and Adjustment shipped via PR #114  |
| On-hand, Reserved, Available, Quarantine and Damaged quantities                                                         | Availability/custody projections cover Available, Quarantine, Dispatch Staging, In Transit and Investigation; no complete customer-return Damaged Item lifecycle exists                                                                                                                                 | Partial: #56, #65–#70                                                                                 |
| Reorder and stock-aging views                                                                                           | No API, projection, web route or test exists                                                                                                                                                                                                                                                            | Missing: #58, #82                                                                                     |
| Quotation-to-Order                                                                                                      | No Quotation model, migration, API, web/mobile workflow or test exists                                                                                                                                                                                                                                  | Missing: #104, #105                                                                                   |
| Price, discount, tax, terms, credit and Commercial Approval                                                             | `sales.py`, `commercial_approval.py`, migrations `0007`–`0008`, and sales/approval tests                                                                                                                                                                                                                | Shipped foundation                                                                                    |
| Partial reservation, fulfillment, cancellation and Backorder Demand                                                     | Reservation, partial fulfillment and Backorder Demand are tested; no authorized Order Cancellation command exists                                                                                                                                                                                       | Partial: #104, #106                                                                                   |
| Fulfillment preparation and Delivery assignment                                                                         | `picking.py`, `dispatch.py`, migrations `0010`–`0011`, web/mobile workbenches and contract tests                                                                                                                                                                                                        | Shipped foundation                                                                                    |
| Multiple Deliveries/Delivery Receipts from eligible order lines                                                         | `dispatch.py`, `delivery_confirmation.py`, `delivery_corrections.py` and their contract tests preserve line/source links                                                                                                                                                                                | Shipped foundation                                                                                    |
| Recipient/signature/photo/timestamp/exception evidence and printable/shareable Delivery Receipt                         | `delivery_confirmation.py`, `delivery_confirmation_outbox.py`, `object_storage.py`, mobile offline evidence tests, and `test_delivery_confirmation_contract.py`                                                                                                                                         | Shipped foundation; real-device/recovery evidence remains #98–#99                                     |
| Invoices, receipts, allocations, credits, adjustments and aging                                                         | `invoice_posting.py`, `payment_allocation.py`, `customer_statement.py`, and merged PR #113 implement Invoice, Allocation, Statement, and immutable Credit Note foundations                                                                                                                              | Partial: #71 tracker/gate reconciliation; overpayment, reversals, complete states and rebuild #72–#76 |
| Partial and Unapplied Payment with immediate Statement refresh                                                          | Partial allocation exists, but the user-facing Unapplied/overpaid workflow and complete receipt reconciliation do not                                                                                                                                                                                   | Partial: #72, #76                                                                                     |
| Paid, partially paid, unpaid, overdue and credited document states                                                      | `customer_statement.py` derives a subset from ledger rows; reversal-complete, due-date-snapshotted behavior is absent                                                                                                                                                                                   | Partial: #75–#76                                                                                      |
| Return request/approval against Delivered Quantity                                                                      | `returns.py`, migration `e93736a741bd`, `test_return_authorization_contract.py`, `test_return_authorization_migration.py`, web workspace `return-authorization-workspace.tsx` and Playwright tests implement immutable Return Requests with maker-checker authorization against delivered receipts | Shipped foundation via PR #112; return disposition outcomes (#68–#70) remain open                                                |
| Offline return evidence, Return Receipt/Inspection and controlled damaged custody                                       | Delivery-exception Return-to-Warehouse Receipt exists, but not the customer Returns lifecycle                                                                                                                                                                                                           | Missing: #66–#67                                                                                      |
| Restock, Replacement, repair, Supplier Return, write-off and finance credit outcomes                                    | No Return Disposition or outcome model exists                                                                                                                                                                                                                                                           | Missing: #68–#70, with Finance #71 and Supplier Return #79                                            |
| Supplier, Purchase Request/approval, Purchase Order and Goods Receipt                                                   | `suppliers.py`, `purchase_orders.py`, `goods_receipts.py`, and `purchase_requests.py` with contract tests and web workspace via PR #118                                                                                                                                                                 | Partial: #58 supplier config; #77 implemented, pending merge in PR #118                               |
| Partial receipt, Receipt Variance, Purchase Backorder and Supplier Return                                               | Basic partial Goods Receipt exists; explicit variance/quality/backorder and Supplier Return do not                                                                                                                                                                                                      | Partial: #78–#79                                                                                      |
| Foreign currency, Inbound Shipment, customs evidence and Landed Cost                                                    | `landed_costs.py` implements line-value allocation; approved Exchange Rate Snapshot and shipment/customs workflow are absent                                                                                                                                                                            | Partial and policy-gated: #63, #80–#81                                                                |
| Expense categories, claims, evidence, duplicate checks, attribution, approval, posting and payment                      | Finance context defines Expense/Expense Claim; no runtime code, migration, client or UI exists                                                                                                                                                                                                          | Missing: #59, #83–#87                                                                                 |
| Effective-dated Commission plans, attribution, accrual, reversal and payout                                             | Only `contexts/commissions/CONTEXT.md` exists                                                                                                                                                                                                                                                           | Missing and policy-gated: #60, #64, #88–#93                                                           |
| Customer Product/Category history, drill-down, filters and role-aware export                                            | No reporting/export runtime module or worker exists                                                                                                                                                                                                                                                     | Missing: #61, #94–#96                                                                                 |
| Responsive web approvals, purchasing, finance, statements, reports and configuration                                    | Existing web routes cover shipped order/delivery/initial finance/procurement slices                                                                                                                                                                                                                     | Partial: each functional issue plus configuration #108 and reporting #94–#96                          |
| Mobile customer lookup, sales, receiving/picking, delivery proof, returns, expense capture, approvals and notifications | Customer/sales/pick/delivery/payment mobile workflows exist; Returns, Expense, broader receiving/approval and notification journeys do not                                                                                                                                                              | Partial: #66–#69, #78–#81, #84–#86, notifications #109, device evidence #98                           |
| One server authorization model, immutable audit, idempotency and database constraints                                   | Existing commands use shared auth, command receipts, scopes and PostgreSQL constraints                                                                                                                                                                                                                  | Partial by completeness contract: every new command must extend these controls; security matrix #97   |
| Generated OpenAPI clients shared by web/mobile                                                                          | `openapi/openapi.json`, `packages/api-client/src/schema.d.ts`, generation scripts and CI drift checks exist                                                                                                                                                                                             | Shipped foundation; every contract-changing slice retains the gate                                    |
| Logs, metrics, traces, backup, migration and rollback                                                                   | Correlation/tracing and migration checks exist; production-like recovery and operational evidence do not                                                                                                                                                                                                | Partial: #62, #97, #99–#102                                                                           |
| Accessibility, responsive behavior and real-device testing                                                              | Playwright covers desktop/mobile-web and native component tests exist; physical-device and full accessibility/performance evidence is open                                                                                                                                                              | Partial: #98                                                                                          |
| Configurable timezone, currency, tax, numbering and document templates                                                  | Base Currency, Tax snapshots and Delivery Receipt series foundations exist; no complete configuration/template workflow exists                                                                                                                                                                          | Partial: #104, #108                                                                                   |
| Import/export tools for legacy migration                                                                                | OpenAPI export is not a business migration/export tool; no legacy import pipeline or role-aware history export exists                                                                                                                                                                                   | Missing: trial migration #100, final production migration #115, and customer export #96               |
| Discovery inventory and current workflow time/error baseline                                                            | No approved inventory of current screens/reports/roles/integrations/exports or observed task-time/error baseline exists                                                                                                                                                                                 | Missing: #110                                                                                         |
| PRD success measures and release-candidate comparison                                                                   | Telemetry foundations exist, but sources, formulas, baselines, targets and owners for the PRD measures are not documented                                                                                                                                                                               | Missing: #110; final comparison feeds #103                                                            |
| Parallel reconciliation, cutover/rollback and business UAT                                                              | No exercised release evidence exists in the repository                                                                                                                                                                                                                                                  | Missing: #101–#103                                                                                    |

## Policy gates

- #63 must confirm whether the line-value Landed Cost allocation implemented by
  #45 is the approved first-release policy, including rounding, late charges,
  and reversals. Policy-sensitive international valuation work remains blocked.
- #64 must select the Commission Basis and earning trigger before commission
  posting rules can be implemented.
- PR #114 / ADR-0019 business-policy decisions are resolved and implemented:
  1. **Transfer valuation timing**: The source Warehouse owns both quantity and
     value until destination receipt. Value moves only at receipt.
  2. **Transfer authorization control**: Request and receipt must be performed
     by different actors; receipt additionally requires an `approval_authorities`
     row for `inventory:transfer-receive` scoped to the source warehouse with a
     `maximum_amount` covering the transfer value.
  3. **Counted-variance adjustments**: Implemented as request → post → reverse
     with maker-checker and Approval Authority enforcement via
     `inventory:adjustment-approve`.

Only the affected dependency chains pause. Independent, low-risk work remains
available in Returns, invoice-to-cash, Procurement, Expenses, customer history,
and staging/security.

## In-flight reconciliation

- PR #111 merged the scope-lock tracker reconciliation to `main` at `5832ba6`.
- PR #112 is open, green, and mergeable against current `main` at `8e649cf`, but
  has no GitHub review decision. It must not be merged without explicit
  PR-specific approval.
- PR #113 merged immutable Credit Notes to `main`. Issue #71 remains open pending
  final tracker closure.
- PR #114 merged issue #107 (immutable source-cost warehouse transfers and
  counted-variance Inventory Adjustments) to `main` at `baf1d48` after passing
  the full release gate (pytest, Playwright, native tests, typecheck, lint,
  format, build, OpenAPI client generation, and downgrade-base/upgrade-head
  migration cycle). Issue #107 is closed.
- PR #116 merged issue #72 (unapplied/overpaid Payment Receipts) to `main` at
  `771c061`.
- PR #117 merged issue #110 (current-system baseline and success measures) to
  `main` at `f195560`.
- PR #118 implements issue #77 (create and approve Purchase Requests before
  Purchase Orders) and is the active slice.

## Dependency-ready work

Issue #107 / PR #114 are now closed and merged. The next dependency-ready
slices include #78, #83, #94, #97, and #105–#109, subject to the remaining
policy gates and tracker approvals. Delivery still proceeds one vertical slice
at a time.

## Completion rule

The overall first-release goal remains active until every required parent has
passed its functional exit criteria, #103 records business UAT, audit sign-off,
and an explicit Go decision, and #115 records the authorized final production
migration plus reconciled cutover control totals. The #110 baseline and final
success-measure comparison must also be approved. A closed child issue, a
working happy-path UI, or a previously green CI run is not sufficient evidence
by itself.
