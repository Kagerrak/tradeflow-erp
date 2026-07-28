# Platform foundation and order-to-delivery PRD

## Problem Statement

Wholesale and distribution teams currently perform customer, order, stock,
warehouse, delivery, payment-timing, and document work across fragmented or
duplicated systems. Sales users cannot safely promise limited inventory,
warehouse users cannot trace every dispatched unit through delivery exceptions,
and field users need to capture proof through unreliable connectivity. The
business needs one auditable workflow that remains correct under partial stock,
partial delivery, retries, concurrent users, payment gates, and scoped
permissions.

## Solution

Build the production-oriented platform foundation and the first thin vertical
slice from Customer through Sales Order, Commercial Approval, Inventory
Reservation, partial Fulfillment, Dispatch, Delivery Confirmation, Proof of
Delivery, and immutable Delivery Receipt. The slice will run through one
FastAPI modular monolith and PostgreSQL source of truth, serve generated clients
to a Next.js operations console and Expo mobile application, use immutable
stock movements and an outbox for cross-context reactions, and keep all posting
and authorization rules server-authoritative.

The slice supports Prepaid, Cash on Delivery, and On Account timing policies;
untracked, lot-tracked, and serial-tracked stock; offline draft and evidence
capture; exact delivery exception custody; and idempotent Draft Invoice creation
without implementing the later invoice-posting and customer-ledger slice.

## User Stories

1. As an Operations Administrator, I want to configure the single Company and
   its Base Currency, so that every Branch and Warehouse uses one accounting
   basis.
2. As an Operations Administrator, I want to create multiple Branches, so that
   commercial documents and user access reflect operational ownership.
3. As an Operations Administrator, I want to create multiple Warehouses per
   Branch, so that stock can be controlled at its real operating location.
4. As an Operations Administrator, I want to assign Capabilities, Operational
   Scopes, and Approval Authority, so that role names alone cannot grant access.
5. As an Operations Administrator, I want configurable Role Templates, so that
   common sales, warehouse, delivery, finance, and administrative access is easy
   to assign without hard-coding authority.
6. As an auditor, I want administrator status separated from business approval
   authority, so that system administration cannot bypass commercial controls.
7. As a Sales Representative, I want to create and maintain Customer Accounts,
   Contacts, and versioned addresses, so that orders retain accurate customer
   identity and historical delivery destinations.
8. As a Sales Representative, I want to assign a default Payment Timing Policy
   and Payment Terms to a Customer Account, so that new orders begin with the
   correct collection expectations.
9. As a Sales Representative, I want to create a Sales Order draft for an
   authorized Branch and Customer, so that I can capture demand without
   consuming stock or credit.
10. As a mobile Sales Representative, I want to capture a Sales Order draft
    offline, so that poor connectivity does not lose my work.
11. As a mobile Sales Representative, I want offline work clearly marked
    Pending Sync, so that I never mistake a local draft for an approved order.
12. As a Sales Representative, I want Customer-specific Price Lists to take
    precedence over Branch defaults, so that agreed customer pricing is applied.
13. As a Sales Representative, I want order lines to support product-specific
    selling units and fixed conversions, so that I can sell cases while stock is
    controlled in pieces.
14. As a Sales Representative, I want each line to show list price, discounts,
    tax treatment, and final totals, so that I can explain the commercial offer.
15. As a Sales Representative, I want to choose Prepaid, Cash on Delivery, or
    On Account with authorized override evidence, so that the order records when
    payment is expected.
16. As a Sales Manager, I want excessive discounts and below-floor prices routed
    for maker-checker approval, so that margin exceptions are controlled.
17. As a Sales Manager, I want On Account orders checked against Credit Hold,
    Credit Limit, and serialized Credit Exposure, so that concurrent approvals
    cannot overcommit a Customer Account.
18. As a Sales Manager, I want an order-specific Credit Override with a reason
    and exposure snapshot, so that approved exceptions remain auditable.
19. As a Sales Manager, I want Commercial Approval to freeze Pricing and
    Calculation Snapshots, so that approved totals remain reproducible.
20. As a Sales Manager, I want material commercial changes to invalidate prior
    approval, so that pricing, credit, and stock commitments cannot become stale.
21. As a Sales Representative, I want approved demand reserved against a
    selected Warehouse, so that promised inventory is explicit.
22. As a Sales Representative, I want partial reservation with Backorder Demand,
    so that available quantity can proceed without pretending the remainder is
    in stock.
23. As an inventory controller, I want concurrent reservations to prevent the
    final available unit from being promised twice, so that availability remains
    correct.
24. As an inventory controller, I want reservations to commit base-unit
    quantity without assigning lots or serials prematurely, so that physical
    identities can be chosen during picking.
25. As a Finance Staff user, I want to record and clear Customer Prepayment, so
    that a Prepaid Fulfillment Order can be released only after sufficient funds
    are available.
26. As a Finance Staff user, I want non-cash receipts to require a different
    verifier and unique external reference, so that duplicate or unverified
    funds cannot satisfy a payment gate.
27. As a Warehouse Clerk, I want Prepaid demand blocked from Pick Release until
    cleared funds cover the reserved fulfillment quantity, so that warehouse
    effort is not spent on unpaid demand.
28. As a Sales Representative, I want only reserved Prepaid quantity collected,
    so that customers do not pay prematurely for Backorder Demand.
29. As an inventory controller, I want unpaid Prepaid reservations released at
    their Payment Deadline, so that scarce stock is not held indefinitely.
30. As a Sales Representative, I want On Account and Cash on Delivery
    reservations to remain firm until an explicit lifecycle event, so that B2B
    commitments do not disappear automatically.
31. As a Warehouse Clerk, I want a Fulfillment Order assigned to one Warehouse,
    so that picking has one accountable stock source.
32. As a Warehouse Clerk, I want one Sales Order split into multiple Fulfillment
    Orders when Warehouses differ, so that cross-warehouse supply remains
    explicit.
33. As a Warehouse Clerk, I want to pick only reserved quantity, so that
    uncommitted or backordered stock cannot be shipped.
34. As a Warehouse Clerk, I want to scan unique barcode mappings, so that the
    correct SKU, selling unit, Lot Identity, or Serial Identity is selected.
35. As a Warehouse Clerk, I want lot and serial requirements enforced from the
    SKU Tracking Policy, so that outbound traceability is complete.
36. As a Warehouse Clerk, I want expiration-controlled lots suggested by FEFO,
    so that eligible stock with the earliest expiration is used first.
37. As a Warehouse Supervisor, I want expired stock prohibited and FEFO
    overrides reasoned and authorized, so that unsafe or unexplained selection
    cannot be posted.
38. As an inventory controller, I want picking to move stock from Available to
    Dispatch Staging, so that it remains warehouse on-hand but cannot be
    reserved again.
39. As a Warehouse Supervisor, I want Dispatch to move staged stock to In
    Transit custody, so that warehouse on-hand and company-custodied stock are
    distinguishable.
40. As Delivery Staff, I want to access only assigned Deliveries, so that route
    and customer information is scoped to my work.
41. As Delivery Staff, I want assigned delivery data cached on my device, so
    that I can work through unreliable connectivity.
42. As Delivery Staff, I want to capture recipient, signature, photos,
    timestamp, accepted quantity, and Delivery Exceptions, so that the delivery
    outcome is evidenced.
43. As Delivery Staff, I want Proof of Delivery evidence stored durably offline,
    so that process termination or upload interruption does not lose it.
44. As Delivery Staff, I want resumed evidence uploads and idempotent command
    replay, so that retrying sync cannot duplicate a Delivery Confirmation.
45. As Delivery Staff, I want a Cash on Delivery amount calculated from accepted
    quantity, so that collection matches what the customer actually receives.
46. As Delivery Staff, I want authorized cash collection cleared immediately,
    so that COD Delivery Confirmation can proceed while remaining subject to
    Cash Reconciliation.
47. As Delivery Staff, I want unpaid accepted COD value blocked unless an
    authorized On Account conversion and Credit Override exist, so that a
    delivery cannot silently create unintended credit.
48. As an operations user, I want every dispatched line partitioned exactly into
    accepted, refused, damaged, short or missing, and still-undelivered
    quantity, so that no stock disappears.
49. As an inventory controller, I want accepted quantity posted outbound from
    In Transit, so that Delivered Quantity and inventory value are immutable and
    traceable.
50. As an inventory controller, I want refused and damaged stock to remain In
    Transit until physically returned to Quarantine, so that it never silently
    re-enters Available stock.
51. As an inventory controller, I want short or missing stock held in
    Investigation until an approved resolution, so that custody discrepancies
    remain visible.
52. As a delivery coordinator, I want offline confirmation conflicts routed for
    explicit review, so that stale device state is never auto-merged into a
    posting.
53. As a customer, I want one Delivery Receipt showing accepted quantities and
    their original order references, so that I have an accurate delivery
    document.
54. As an operations user, I want Branch-specific Delivery Receipt numbers that
    are unique and never reused, so that issued documents remain auditable.
55. As an auditor, I want voided or skipped receipt numbers recorded, so that
    numbering gaps can be explained.
56. As an operations user, I want repeated confirmation and document-generation
    retries to return the same receipt identity, so that duplicate documents are
    not issued.
57. As an authorized supervisor, I want Delivery Corrections to use linked
    reversal and replacement records, so that issued receipts and stock history
    are never edited.
58. As Finance Staff, I want Delivery Confirmation to create one Draft Invoice
    for accepted quantity without posting receivables, so that the later
    invoice-to-cash slice has a traceable billing source.
59. As an inventory controller, I want outbound movements to snapshot Moving
    Average Unit Cost, so that delivered inventory value can be rebuilt.
60. As an auditor, I want every approval, posting, reversal, correction, and
    document issuance linked to actor, time, source, and correlation identity,
    so that the entire workflow is explainable.
61. As a web user, I want empty, loading, conflict, forbidden, validation, and
    retry states to be explicit, so that operational failures are actionable.
62. As a mobile user, I want denied permissions, poor connectivity, background
    upload, and process restart handled safely, so that field work remains
    reliable.
63. As an API consumer, I want stable error codes and generated OpenAPI clients,
    so that web and mobile interpret business failures consistently.
64. As an operator, I want structured logs, traces, metrics, health checks, and
    migration status, so that production failures can be detected and
    diagnosed.
65. As a maintainer, I want deterministic local and CI environments, so that the
    complete slice can be reproduced and verified before deployment.

## Implementation Decisions

- Use a monorepo with separate web, mobile, API, worker, generated API-client,
  design-token, telemetry, and infrastructure workspaces. TypeScript workspaces
  use pnpm; Python workspaces use uv.
- Build one FastAPI modular monolith plus Python workers. The first slice spans
  Organization & Access, Customers, Catalog & Inventory, Sales, Fulfillment,
  and a narrow Finance boundary for receipts and Draft Invoices.
- Use PostgreSQL as the source of truth. Tests and development run real
  migrations; SQLite is limited to the Expo offline cache and outbox.
- Expose business behavior through versioned HTTP commands and queries.
  Generate the shared TypeScript client from OpenAPI and compile both clients
  against it.
- Use opaque client-safe identifiers, server timestamps, optimistic document
  versions, correlation IDs, and caller-provided idempotency keys for every
  retryable command.
- Store immutable stock movements and value adjustments. Maintain rebuildable
  projections for Warehouse on-hand, custody quantity, reserved quantity,
  available quantity, and moving-average value.
- Serialize reservation and moving-average updates for one SKU and Warehouse.
  Serialize Credit Exposure checks for one Customer Account.
- Use a transactional outbox for committed cross-context reactions, including
  Delivery Confirmation to Draft Invoice creation and document generation.
  Consumers are idempotent and persist processing identity.
- Model the approved Sales, Inventory, Fulfillment, payment-timing, rounding,
  custody, document, and authorization policies from ADR-0001 through ADR-0016
  as server-side state transitions and database constraints.
- Treat Customer addresses, Price Lists, tax rules, Unit Conversions,
  Calculation Snapshots, and issued Delivery Receipts as effective-dated or
  snapshotted data where historical reproduction requires it.
- Use decimal database and application types with the approved six-place input
  precision, currency-minor-unit round-half-up, line-level totals,
  largest-remainder allocation, and final-delivery residual behavior.
- Keep physical Tracking Policy separate from moving-average valuation. Assign
  Lot Identity and Serial Identity at Pick, enforce FEFO and expiration, and
  prevent concurrent double-pick.
- Represent Available, Dispatch Staging, In Transit, Investigation, Quarantine,
  and final outbound as explicit movement locations or custody states rather
  than mutable quantity fields.
- Implement capability-based authorization with Branch/Warehouse Operational
  Scope, delivery assignment, Approval Authority limits, and maker-checker
  separation. UI visibility is advisory; the API rechecks every command.
- Accept standards-based OIDC access tokens at the API boundary. Keep the
  production identity provider deployment-configurable; use a deterministic
  local test issuer for automated verification.
- Build the web experience for master data, order entry and approval,
  reservation visibility, warehouse execution, exception review, and document
  inspection.
- Build the mobile experience for assigned delivery, cached authorized data,
  offline Sales Order draft, offline Proof of Delivery and COD evidence,
  Pending Sync, resumable uploads, and explicit conflict review.
- Encrypt mobile secrets in platform secure storage and retain operational
  drafts and outbox commands in SQLite. Server acknowledgement is mandatory for
  approval, reservation, stock posting, Delivery Confirmation, receipt
  issuance, and Draft Invoice creation.
- Store evidence in S3-compatible object storage using signed, expiring access.
  Document workers render Delivery Receipts from immutable snapshots and reuse
  stable document identity on retries.
- Propagate structured error codes and correlation identity through clients,
  API, PostgreSQL transactions, outbox handlers, workers, and audit events.
- Provide containerized PostgreSQL, Redis, and S3-compatible local dependencies;
  deterministic seed data; migrations; CI; backup/restore hooks; and environment
  configuration without committing secrets.

## Testing Decisions

- The primary acceptance seam is black-box HTTP behavior through FastAPI against
  a real migrated PostgreSQL database. Tests assert responses and durable
  business outcomes rather than internal method calls.
- API acceptance tests cover capabilities and scopes, maker-checker,
  idempotency, optimistic versions, concurrent reservation, concurrent credit
  approval, moving-average serialization, outbox atomicity, projection rebuild,
  and immutable reversal/correction behavior.
- Calculation tests cover unit conversion, decimal precision, inclusive and
  exclusive tax, discount thresholds, largest-remainder ties, partial-delivery
  allocation, and exact final reconciliation.
- Inventory tests cover untracked, lot, serial, expiration, FEFO override,
  double-pick prevention, staging, transit, outbound, quarantine,
  investigation, and every exact delivery partition.
- Payment tests cover Prepaid deadlines, partial reservation collection,
  immediate cash clearance, Cash Reconciliation, non-cash maker-checker
  verification, duplicate references, COD accepted-quantity collection, and
  unauthorized On Account conversion.
- Document tests render Delivery Receipts and inspect their accepted quantities,
  snapshots, Branch numbering, gap audit, stable retry identity, evidence
  linkage, and correction chain.
- Web Playwright journeys cover Customer creation, Sales Order drafting,
  approval and overrides, partial reservation, Pick Release, Pick, Dispatch,
  delivery review, conflicts, forbidden actions, empty states, and Delivery
  Receipt inspection.
- Expo journeys run on supported Android and iOS targets and cover assigned
  delivery, offline capture, process termination, denied camera/storage
  permission, Pending Sync, resumable evidence upload, server-state conflict,
  COD capture, and successful acknowledgement.
- OpenAPI generation is deterministic. CI regenerates the client, fails on
  uncommitted contract drift, and compiles web and mobile against the result.
- CI runs formatting, lint, Python and TypeScript type checks, unit tests,
  PostgreSQL integration tests, API acceptance tests, generated-client checks,
  web build, Expo validation, worker tests, and migration upgrade/downgrade
  verification.
- Release evidence includes real-device mobile results, critical web journeys,
  backup/restore exercise, authorization matrix results, projection
  reconciliation, and documented residual risk.

## Out of Scope

- Invoice approval/posting UI, customer-ledger posting, payment allocation,
  statements, aging, credits, and general invoice-to-cash behavior beyond
  creating a Draft Invoice and retaining Unapplied Payment.
- Full customer returns, return authorization, inspection, replacement, credit
  note, and supplier-return workflows beyond delivery-exception custody and
  Return-to-Warehouse Receipt.
- Procurement, international purchasing, landed-cost allocation, suppliers, and
  purchase receipts.
- Expense claims, expense posting, commissions, commission earning policy, and
  payouts.
- Full general ledger, financial statements, payroll, statutory HR,
  manufacturing, bills of material, and e-commerce.
- Foreign-currency sales or customer receivables.
- Autonomous pricing, purchasing, or credit approval.
- Offline commercial approval, reservation, inventory posting, Delivery
  Confirmation, financial posting, or automatic conflict merging.
- Payment gateway integration; provider-confirmed payment is an interface only.
- Production identity-provider procurement or tenant administration beyond the
  OIDC-compatible application boundary.
- Construction projects, BOQs, WBS, construction contracts, progress billing,
  retainage, RFIs, submittals, and all other construction-specific concepts.
- Microservice extraction without measured scaling, ownership, or
  failure-isolation evidence.

## Further Notes

- ADR-0001 through ADR-0016 are accepted constraints for this PRD.
- Commission basis and earning trigger are intentionally deferred until the
  commission slice.
- International landed-cost allocation is intentionally deferred until the
  procurement slice.
- Production OIDC provider selection, Base Currency value, tax-code seed values,
  Branch payment-deadline durations, approval limits, and document-series
  formats are deployment configuration decisions. Their schemas and validation
  are in scope; organization-specific values are not guessed.
- Construction expansion remains explicitly prohibited.
