# ADR-0004: Limit foreign currency to procurement in the first release

- Status: Accepted
- Date: 2026-07-28

## Context

TradeFlow needs foreign-currency international purchasing, while multi-currency
sales and customer receivables would add exchange-rate, settlement, credit, and
statement invariants that the first release does not require.

## Decision

The Company has one immutable Base Currency inherited by every Branch and
Warehouse. First-release sales, delivery, invoicing, and customer receivables
use Base Currency. An international Purchase Order may use one foreign
Transaction Currency. Each foreign-currency posting retains its approved
Exchange Rate Snapshot, while inventory value posts in Base Currency.

## Consequences

Foreign-currency supplier settlement differences create explicit adjustments
instead of rewriting historical receipt costs. Changing Base Currency requires
a controlled migration. Supporting foreign-currency customer receivables later
will require a separate decision and vertical slice.
