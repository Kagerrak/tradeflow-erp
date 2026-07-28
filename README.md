# TradeFlow ERP

TradeFlow ERP is a cross-platform business operations system for wholesale and
distribution companies. It replaces fragmented customer, inventory, sales,
delivery, purchasing, receivables, expense, return, and commission workflows
with one auditable source of truth.

The product has three delivery surfaces:

- a responsive web operations console;
- an Expo mobile application for Android and iOS;
- a shared Python API and background-worker platform.

## Proposed stack

- Next.js, React, and TypeScript for the web console
- Expo, React Native, Expo Router, and TypeScript for Android/iOS
- FastAPI, Pydantic, SQLAlchemy, Alembic, and PostgreSQL
- Redis-backed workers for documents, reports, imports, and notifications
- S3-compatible storage for receipts, delivery evidence, and supplier documents
- OpenAPI-generated web/mobile clients
- Docker, CI, structured logs, metrics, traces, and error monitoring

## Documentation

- [Context map](./CONTEXT-MAP.md)
- [Product requirements](./docs/product/product-requirements.md)
- [System architecture](./docs/architecture.md)
- [Cross-platform strategy](./docs/platform-strategy.md)
- [Delivery roadmap](./docs/delivery/implementation-plan.md)
- [Testing strategy](./docs/testing-strategy.md)
- [ADR-0001: immutable operational ledgers](./docs/adr/0001-immutable-operational-ledgers.md)
- [ADR-0002: assign tracked stock identities at pick](./docs/adr/0002-assign-tracked-stock-identities-at-pick.md)
- [ADR-0003: moving-average inventory valuation by warehouse](./docs/adr/0003-moving-average-inventory-valuation-by-warehouse.md)
- [ADR-0004: base and foreign-currency scope](./docs/adr/0004-base-and-foreign-currency-scope.md)
- [ADR-0005: invoice after delivery confirmation](./docs/adr/0005-invoice-after-delivery-confirmation.md)
- [ADR-0006: offline capture with server-authoritative posting](./docs/adr/0006-offline-capture-server-authoritative-posting.md)
- [ADR-0007: reserve before prepayment and collect before pick](./docs/adr/0007-reserve-before-prepayment-collect-before-pick.md)
- [ADR-0008: confirm COD delivery with collection](./docs/adr/0008-confirm-cod-delivery-with-collection.md)
- [ADR-0009: calculate and serialize on-account credit exposure](./docs/adr/0009-on-account-credit-exposure.md)
- [ADR-0010: freeze line pricing and invalidate material changes](./docs/adr/0010-freeze-line-pricing-and-invalidate-material-changes.md)
- [ADR-0011: expire only unpaid prepaid reservations](./docs/adr/0011-expire-only-unpaid-prepaid-reservations.md)
- [ADR-0012: deterministic monetary rounding and allocation](./docs/adr/0012-deterministic-monetary-rounding-and-allocation.md)
- [ADR-0013: preserve stock custody through delivery exceptions](./docs/adr/0013-stock-custody-through-delivery-exceptions.md)
- [ADR-0014: issue immutable branch-numbered delivery receipts](./docs/adr/0014-immutable-branch-numbered-delivery-receipts.md)
- [ADR-0015: authorize by capability, scope, and limits](./docs/adr/0015-capability-scope-and-limit-authorization.md)
- [ADR-0016: clear payments by method-specific evidence](./docs/adr/0016-method-specific-payment-clearance.md)

## Portfolio-ready outcome

A reviewer should be able to:

1. create a customer and sales order;
2. reserve inventory and partially fulfill the order;
3. issue a delivery receipt and invoice;
4. record and allocate a partial payment;
5. see the statement of account update immediately;
6. process damaged-item returns and a credit;
7. receive a local or international purchase order;
8. inspect expense and sales-commission calculations;
9. complete delivery/warehouse work on a real Android or iOS device;
10. trace every stock and financial balance to immutable source movements.
