# Domain docs

TradeFlow uses a multi-context domain model.

## Before working

1. Read `CONTEXT-MAP.md`.
2. Read each relevant `contexts/<context>/CONTEXT.md`.
3. Read applicable system-wide decisions under `docs/adr/`.
4. If context-specific ADR directories are introduced later, read the applicable decisions there too.

## Current bounded contexts

- Customers
- Catalog and Inventory
- Sales
- Fulfillment
- Returns
- Procurement
- Finance
- Commissions

Use terminology exactly as defined by the context documents. If a required concept is missing or ambiguous, resolve it through the domain-modeling workflow rather than silently inventing a synonym.

Surface any proposed change that conflicts with an existing ADR instead of silently overriding it.
