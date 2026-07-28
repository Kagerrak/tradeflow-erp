# TradeFlow ERP context map

TradeFlow uses multiple bounded contexts because customer balances, inventory,
orders, delivery, procurement, and commissions have different invariants and
lifecycles. A term defined in one context should not be reused with a different
meaning elsewhere.

| Context               | Responsibility                                                                 | Domain language                                                    |
| --------------------- | ------------------------------------------------------------------------------ | ------------------------------------------------------------------ |
| Organization & Access | Company, branches, users, roles, and operational scope                         | [Organization & Access](./contexts/organization-access/CONTEXT.md) |
| Customers             | Customer identity, contacts, addresses, terms, and credit policy               | [Customers](./contexts/customers/CONTEXT.md)                       |
| Catalog & Inventory   | Products, warehouses, stock movements, reservations, and availability          | [Catalog & Inventory](./contexts/catalog-inventory/CONTEXT.md)     |
| Sales                 | Quotes, sales orders, pricing, and commercial approval                         | [Sales](./contexts/sales/CONTEXT.md)                               |
| Fulfillment           | Pick, release, delivery, proof, and delivery receipts                          | [Fulfillment](./contexts/fulfillment/CONTEXT.md)                   |
| Returns               | Return authorization, inspection, disposition, replacement, and credit request | [Returns](./contexts/returns/CONTEXT.md)                           |
| Procurement           | Suppliers, purchase orders, inbound receipts, imports, and landed cost         | [Procurement](./contexts/procurement/CONTEXT.md)                   |
| Finance               | Invoices, payments, allocations, credits, expenses, and customer statements    | [Finance](./contexts/finance/CONTEXT.md)                           |
| Commissions           | Commission plans, accruals, eligibility, reversals, and payout                 | [Commissions](./contexts/commissions/CONTEXT.md)                   |

## Principal flow

```text
Customer
  -> Sales Order
  -> Inventory Reservation
  -> Delivery
  -> Delivery Receipt
  -> Invoice
  -> Payment Allocation
  -> Customer Statement projection

Supplier
  -> Purchase Order
  -> Inbound Receipt
  -> Stock Movement

Delivered Item
  -> Return Authorization
  -> Return Inspection
  -> Restock / Quarantine / Write-off
  -> Credit Note or Replacement
```

The Customer Statement and inventory availability are projections derived from
posted financial and stock movements. They are not manually editable balances.

## Organizational relationships

- TradeFlow supports one Company in the first release.
- A Company contains multiple Branches.
- A Branch may operate multiple Warehouses.
- A Sales Order belongs to one Branch.
- A Fulfillment Order is assigned to one Warehouse.
- A Sales Order fulfilled from multiple Warehouses produces a separate
  Fulfillment Order for each Warehouse.
- Commercial Approval requests Warehouse-specific Inventory Reservations.
- Reservation may be partial; unreserved Open Quantity becomes Backorder
  Demand and cannot be fulfilled until reserved.
- An approved Sales Order snapshots line-level tax treatment under one
  Price Inclusion Mode.
- Withholding is recognized by Finance at payment time and does not reduce the
  Sales Order total.
- Inventory quantities and reservations use each SKU's Base Stocking Unit while
  business documents retain the entered unit and Unit Conversion Snapshot.
- Inventory Reservations commit quantity without assigning Lot or Serial
  Identity. Picking assigns required identities and uses FEFO for
  expiration-controlled stock.
- Inventory is valued using perpetual Moving Average Unit Cost per SKU and
  Warehouse in the Company Base Currency.
- Sales and customer receivables use Company Base Currency in the first
  release. International Procurement may use one foreign Transaction Currency
  per document with an Exchange Rate Snapshot at posting.
- Delivery Confirmation creates one Draft Invoice for that Delivery's accepted
  quantity. Finance approval posts it to the customer ledger.
- A Customer Account defaults to Prepaid, Cash on Delivery, or On Account.
  Each Sales Order snapshots its Payment Timing Policy independently of Payment
  Method.
- Prepaid orders reserve eligible quantity before collection. Cleared Customer
  Prepayment covering a Fulfillment Order is required before Pick Release and
  is allocated after the related Invoice posts.
- Cash on Delivery permits dispatch before collection. Delivery Confirmation
  records accepted quantity, proof, and sufficient COD Payment Receipt
  atomically unless an authorized conversion moves unpaid value to On Account.
- On Account Commercial Approval checks serialized Credit Exposure against the
  Customer Account's Credit Limit. A posted Invoice atomically replaces its
  matching uninvoiced exposure.
- Commercial Approval freezes line-level Pricing Snapshots. Material commercial
  changes invalidate approval and rerun pricing, credit, and reservation checks.
- On Account and Cash on Delivery reservations persist until fulfillment,
  cancellation, material change, or authorized release. Prepaid reservation
  expires at its Payment Deadline and moves to Payment Hold and Backorder
  Demand.
- Approved order totals use line-level decimal Calculation Snapshots.
  Deterministic allocation and final-delivery residuals make partial delivery
  invoices reconcile exactly to the order.
- Picked stock moves through Dispatch Staging and In Transit custody. Delivery
  Confirmation posts only accepted quantity outbound; refused or damaged stock
  requires Return-to-Warehouse Receipt into Quarantine, while short or missing
  stock remains under Investigation.
- Each server-accepted Delivery Confirmation issues one immutable,
  Branch-numbered Delivery Receipt for accepted quantity. Corrections use linked
  reversal and replacement records.
- Authorization combines Capability, Operational Scope, and Approval Authority.
  Role Templates are configurable, and administrator status alone grants no
  business approval authority.
- Authorized cash collection clears immediately subject to reconciliation.
  Non-cash Payment Receipts require maker-checker Payment Verification unless
  an approved provider confirms settlement.
