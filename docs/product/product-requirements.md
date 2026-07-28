# TradeFlow ERP product requirements

## Product goal

Simplify an existing customized business system while preserving essential
distribution operations. Users should complete common work with fewer screens
and duplicate entries, while finance and inventory retain auditable correctness.

## Primary personas

- Sales representative managing customers and orders.
- Sales manager approving discounts and reviewing performance.
- Warehouse clerk receiving, reserving, picking, and returning goods.
- Delivery staff recording proof and exceptions on mobile.
- Purchasing officer managing local and international suppliers.
- Finance staff issuing invoices, allocating payments, producing statements,
  posting expenses, and reviewing commissions.
- Operations administrator configuring roles, workflows, and master data.

## Required modules

### Customer information management

- customer account, status, terms, credit policy, tax identity, contacts, and
  billing/delivery addresses;
- assigned salesperson and customer-specific pricing;
- consolidated sales, delivery, return, payment, and balance timeline.

### Inventory management

- products, SKUs, units, warehouses, locations, lots/serials where required;
- receipts, transfers, reservations, deliveries, returns, damage, and
  adjustments through immutable movements;
- on-hand, reserved, available, quarantine, and damaged quantities;
- reorder and stock-aging views.

### Sales order processing

- quotation-to-order flow;
- price list, discount, tax, terms, credit, and approval rules;
- partial reservation, fulfillment, cancellation, and backorder;
- order status derived from commercial and fulfillment state.

### Delivery receipt

- fulfillment preparation and delivery assignment;
- one order to multiple delivery receipts and one delivery from eligible order
  lines;
- recipient, signature/photo, timestamp, and exception evidence;
- printable/shareable delivery receipt.

### Statements and receivables

- invoices, payment receipts, allocations, credits, adjustments, and aging;
- partial payment and unapplied-payment support;
- statement of account generated from the customer ledger;
- immediate balance refresh after a committed posting;
- paid, partially paid, unpaid, overdue, and credited document states.

### Sales return processing

- return request and approval against delivered quantities;
- reason, photos, received quantity, inspection, and responsible party;
- damaged goods move to quarantine/damaged stock, never directly to available;
- replacement, repair, supplier return, write-off, or finance credit outcomes.

### Purchase order management

- supplier master, purchase request/approval, purchase order, and goods receipt;
- partial receipts, quality variance, backorder, and supplier return;
- foreign currency, inbound shipment, customs documents, and landed-cost
  allocation for international suppliers.

### Expense management

- expense categories, claims, receipts, approvals, posting, and payment status;
- cost center, branch, project, supplier, and employee attribution;
- duplicate-evidence and policy checks.

### Sales commission management

- effective-dated plans, rates, tiers, attribution, accruals, reversals, and
  payout statements;
- configurable earning basis and trigger;
- returns, credits, and payment reversals update commission through adjustment
  entries rather than history edits.

### Customer sales history

- chronological transaction history;
- product and category purchase history;
- order, delivery, invoice, payment, return, and balance drill-down;
- date, status, branch, salesperson, and product filters;
- export with role-aware masking.

## Cross-platform allocation

### Web console

Owns master data, dense tables, approvals, configuration, purchasing, finance,
statements, reports, and administrative workflows.

### Android/iOS app

Prioritizes customer lookup, sales capture, warehouse receiving/picking,
delivery proof, returns evidence, expense receipt capture, approvals, and
notifications. Mobile does not duplicate every administrative screen.

## Non-functional requirements

- one server-side authorization model for all clients;
- append-only audit history for postings and approvals;
- idempotent commands for orders, deliveries, payments, and mobile sync;
- database constraints protecting stock and financial invariants;
- generated OpenAPI clients shared by web and mobile;
- structured logs, metrics, traces, backups, migration, and rollback procedures;
- accessibility, responsive web behavior, and real-device mobile testing;
- configurable timezone, currency, tax, numbering, and document templates;
- import/export tools for migration from the existing system.

## Confirmed business policies

- The first release supports one company with multiple branches and multiple
  warehouses. A sales order belongs to one branch, and each fulfillment order
  uses one warehouse.
- Commercial approval attempts warehouse-specific reservation. Partial
  reservation is allowed, excess demand becomes backorder demand, and
  fulfillment cannot exceed reserved quantity.
- Each price list declares tax-inclusive or tax-exclusive pricing. An order
  uses one inclusion mode and snapshots effective line tax treatment on
  approval.
- Withholding is recognized during payment processing and does not reduce the
  sales-order total.
- Each SKU has an immutable base stocking unit. Product-specific,
  effective-dated fixed conversions allow selling and purchasing in other
  units, while documents retain the entered unit and conversion snapshot.
- Each SKU is untracked, lot-tracked, or serial-tracked. Required identities
  are assigned during picking, expired stock cannot be shipped, FEFO is the
  default for expiration-controlled stock, and barcode mappings are unique.
- Inventory uses perpetual moving weighted-average valuation per SKU and
  warehouse in the company base currency. Deliveries snapshot current average
  cost, transfers carry source cost, and customer returns restore original
  delivery cost.
- The company has one immutable base currency inherited by all branches and
  warehouses. Sales and customer receivables use it; international procurement
  may use one foreign transaction currency per document with an approved
  exchange-rate snapshot at posting.
- Confirming a delivery creates one draft invoice idempotently for its accepted
  quantities. Partial deliveries produce separate drafts; Finance approval is
  required before posting affects customer receivables.
- Mobile supports cached authorized reference data, offline sales-order drafts,
  and offline proof-of-delivery evidence. Approval, reservation, stock posting,
  delivery confirmation, and invoice creation require server acknowledgement;
  conflicts require explicit review.
- Customer accounts default to prepaid, cash-on-delivery, or on-account payment
  timing. Sales orders snapshot the selected timing policy independently of
  payment method, and authorized overrides retain a reason.
- Prepaid orders reserve stock before collection. Cleared prepayment covering
  the reserved quantity is required before pick release, remains unapplied
  until invoicing, and never collects for backorder demand prematurely.
- Cash-on-delivery orders may dispatch before collection. Confirmation records
  accepted quantity, proof, and sufficient collection atomically; unpaid value
  requires authorized conversion to on-account terms and applicable credit
  approval.
- On-account approval requires payment terms and an approved credit limit.
  Exposure combines posted open receivables with approved uninvoiced order
  value, serializes concurrent checks, and requires an order-specific override
  when absent or exceeded.
- Customer-specific price lists take precedence over branch defaults. Approved
  order lines snapshot price sources, allocated discounts, tax, and final unit
  prices; excessive discount or below-floor pricing requires maker-checker
  approval, and material commercial changes invalidate approval.
- On-account and cash-on-delivery reservations persist until fulfilled,
  cancelled, materially changed, or explicitly released. Prepaid reservations
  expire at a branch-configured payment deadline, release stock idempotently,
  and place remaining demand on payment hold until re-reserved.
- Calculations use decimal arithmetic, currency-minor-unit round-half-up,
  line-level rounding, deterministic largest-remainder allocation, and
  final-delivery residual handling so partial invoices reconcile exactly.
- Picked stock moves through dispatch staging and in-transit custody. Delivery
  outcomes partition dispatched quantity exactly; accepted stock posts
  outbound, refused or damaged stock returns to quarantine, and short or
  missing stock remains under investigation until explicitly resolved.
- Each server-accepted delivery confirmation issues one immutable,
  branch-numbered delivery receipt for accepted quantities. Proof remains
  separate linked evidence, retries reuse document identity, and corrections
  use audited reversal and replacement records.
- Authorization combines server-enforced capabilities with branch/warehouse
  scope and explicit approval limits. Configurable role templates cover sales,
  warehouse, delivery, finance, and administration; administrator status alone
  grants no business approval authority.
- Cash receipts clear immediately for authorized collectors and are later
  reconciled. Bank transfer, check, and manually recorded electronic receipts
  require maker-checker verification, unique active external references, and
  immutable rejection or reversal history.

## Important assumptions requiring confirmation

- commission basis and earning trigger;
- landed-cost allocation rules for international purchasing.

## Explicit non-goals for the first release

- Payroll and statutory HR.
- Full general ledger and financial statements.
- Manufacturing or bill of materials.
- E-commerce storefront.
- Autonomous purchasing, credit approval, or pricing decisions.
- Replacing business policy with an AI agent.

## Success measures

- order-entry time and correction rate;
- stock variance and reservation conflicts;
- delivery completion and exception resolution time;
- unallocated payment age and statement reconciliation;
- return cycle time and damaged-stock visibility;
- purchase lead time and receipt variance;
- expense approval time;
- commission dispute/recalculation rate;
- mobile crash-free sessions and sync success;
- user task completion compared with the current system.
