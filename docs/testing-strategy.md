# TradeFlow ERP testing strategy

## Domain tests

- order totals, discounts, taxes, terms, and approval thresholds;
- partial reservation, fulfillment, cancellation, and backorder;
- stock movements, reservations, reversals, and damaged disposition;
- moving-average receipts, outbound cost snapshots, transfers, original-cost
  returns, and immutable value adjustments;
- invoice, payment allocation, credit, aging, and statement reconciliation;
- idempotent draft-invoice creation from accepted delivery quantities and
  Finance-controlled posting;
- prepaid reservation-before-collection, payment-before-pick release, partial
  reservation collection, later invoice allocation, and cancellation handling;
- COD accepted-quantity collection, method-specific clearance, atomic delivery
  confirmation, later invoice allocation, cash reconciliation, and authorized
  conversion of unpaid value to on-account terms;
- serialized on-account credit checks, holds, absent and exceeded limits,
  order-specific overrides, and atomic replacement of uninvoiced exposure by
  posted invoices;
- price-list precedence, line discount allocation, floor-price and role
  thresholds, maker-checker separation, pricing snapshots, and precise
  approval invalidation;
- persistent COD/on-account reservations, prepaid payment deadlines, idempotent
  release to backorder, payment hold, manual release evidence, and simultaneous
  payment/release/fulfillment races;
- decimal precision, currency-minor-unit round-half-up, line-sum totals,
  deterministic largest-remainder allocation, and exact reconciliation across
  partial and final deliveries;
- available-to-staging-to-transit movements, exact delivery partitions,
  accepted outbound posting, quarantine-only physical returns, investigation
  resolution, concurrent confirmation, and idempotent retries;
- one immutable delivery receipt per confirmation, branch-series uniqueness,
  number non-reuse and gap audit, stable retry identity, evidence linkage, and
  maker-checker correction reversal/replacement, tracked-identity preservation,
  original-cost valuation, Draft Invoice source replacement, and receipt-chain
  rendering;
- capability plus branch/warehouse scope checks, approval limits, configurable
  role templates, maker-checker separation, assignment boundaries, and
  administrator non-escalation;
- immediate cash clearance and reconciliation, non-cash maker-checker
  verification, check clearance, active-reference uniqueness, provider
  confirmation, rejection, reversal, and duplicate command replay;
- return eligibility against the current receipt-chain head and previously
  authorized quantity, maker-checker value limits, exact replay, concurrent
  final-quantity authorization, immutable request guards, and exclusion with
  later Delivery Correction;
- purchase receipt variance and landed-cost allocation;
- expense approval and duplicate evidence;
- commission accrual, tier, adjustment, reversal, and payout.

## Integration tests

Use real PostgreSQL migrations for:

- concurrent reservations against limited stock;
- duplicate order, delivery, payment, and mobile commands;
- transaction rollback between domain posting and outbox creation;
- ledger and stock projection rebuilds;
- role/branch/warehouse authorization;
- generated OpenAPI client compatibility;
- document generation and signed-access expiry.

## Cross-platform E2E

### Web

- customer/order entry;
- discount and credit approval;
- purchase order and receipt;
- payment allocation and statement generation;
- return review, expense approval, and commission statement.

### Android/iOS

- customer and inventory lookup;
- order draft;
- pick/receive scan;
- proof of delivery with signature/photo;
- damaged return evidence;
- expense receipt capture;
- notification deep link and approval action.

Test process termination, background upload, denied permissions, poor
connectivity, large text, screen readers, and supported client/API versions.
Verify Pending Sync state, idempotent command replay, resumable evidence upload,
server-state conflict review, and that offline work never appears posted before
server acknowledgement.

## Invariant and failure scenarios

- two users reserve the final available unit concurrently;
- the same payment command is retried after response loss;
- a partial payment is later reversed;
- delivery is confirmed while invoice generation worker is unavailable;
- damaged return is mistakenly requested for more than delivered quantity;
- a credit is approved after the statement was previously generated;
- commission rules change after an earlier accrual;
- international receipt arrives in multiple shipments with later freight cost;
- projection tables are deleted and rebuilt from immutable entries;
- a user attempts access outside assigned branch or warehouse.

## Migration verification

- record counts and key uniqueness;
- customer opening balances;
- invoice aging;
- SKU/warehouse quantities and valuation;
- open sales and purchase quantities;
- historical document links;
- duplicate customer/product detection;
- rejected rows with actionable reasons;
- dry-run, repeatability, and rollback.

## Performance evidence

Publish workload assumptions and measure p50/p95/p99 API latency, critical query
plans, concurrent order posting, reservation contention, statement generation,
report queue age, import throughput, mobile cold start, large-list rendering,
and outbox completion.

## Release gates

- deterministic tests, migrations, and API compatibility pass;
- no stock, finance, or cross-scope invariant failures;
- projection rebuilds reconcile;
- critical web and real-device journeys pass;
- backup/restore and rollback have been exercised;
- migration reconciliation is approved before production cutover.
