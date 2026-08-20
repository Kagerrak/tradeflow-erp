# TradeFlow authorization matrix

TradeFlow authorizes every business command through three layers:

1. **Capability** – a server-enforced permission to perform one action (e.g.,
   `sales:commercial-approve`).
2. **Operational Scope** – the Branches and Warehouses where the user may exercise
   a capability.
3. **Approval Authority** – for sensitive approvals, an explicit financial or
   percentage limit and a Maker-Checker requirement.

Role Templates are configurable starting collections of Capabilities. A user's
Role assignments, Branch/Warehouse assignments, and Approval Authorities are
stored in the database and loaded on every authenticated request.

## Domain terms

| Term                     | Meaning                                                                                           |
| ------------------------ | ------------------------------------------------------------------------------------------------- |
| Capability               | Server-enforced permission for one action.                                                        |
| Operational Scope        | Assigned Branches and Warehouses.                                                                 |
| Approval Authority       | Capability + Scope + explicit limit + Maker-Checker flag.                                         |
| Maker-Checker            | The approver must be a different person from the requester.                                       |
| Operations Administrator | Configures users, roles, scopes, and policies, but has no business approval authority by default. |

## Key controls

- **Missing capability is denied.** A bearer token may be valid and the user may
  be active, but without the required Capability the API returns `403`
  `capability_required`.
- **Cross-scope is denied.** A user assigned only to Branch A cannot read or
  approve data for Branch B. A warehouse-scoped authority is rejected when it
  does not match the document's warehouse.
- **Administrator status does not escalate.** An Operations Administrator without
  `sales:commercial-approve`, `finance:payment-verify`, or another business
  approval Capability cannot perform that approval.
- **Self-approval is denied.** Maker-Checker requires `approved_by != maker_subject`.
  Attempting to approve your own request returns `409` `maker_checker_violation`.
- **Over-limit is denied.** An Approval Authority may set `maximum_amount` and/or
  `maximum_percentage`. Exceeding either returns `403` `approval_limit_exceeded`.
- **Revocation is immediate.** Removing a Branch or Warehouse assignment or
  deleting an Approval Authority row takes effect on the next request; cached
  sessions are not trusted.

## Capability categories

### Platform

| Capability               | Scope  | Notes                                |
| ------------------------ | ------ | ------------------------------------ |
| `platform:read`          | Global | Public health and session endpoints. |
| `platform:write`         | Global | Internal platform commands.          |
| `organization:bootstrap` | Global | One-time initial organization setup. |

### Organization administration

| Capability           | Scope              | Notes                                                                                                                  |
| -------------------- | ------------------ | ---------------------------------------------------------------------------------------------------------------------- |
| `organization:admin` | Branch / Warehouse | Configure users, roles, branches, warehouses, document templates, and policies. Does **not** grant business approvals. |

### Master data

| Capability                   | Scope  | Notes                                |
| ---------------------------- | ------ | ------------------------------------ |
| `customer:read`              | Branch | Read customers and addresses.        |
| `customer:write`             | Branch | Create and update customers.         |
| `customer:credit-approve`    | Branch | Approve credit limits and overrides. |
| `catalog:read`               | Global | Read products, SKUs, and units.      |
| `catalog:write`              | Global | Create and update products and SKUs. |
| `procurement:supplier-read`  | Branch | Read supplier directory.             |
| `procurement:supplier-write` | Branch | Maintain suppliers.                  |

### Sales and commercial

| Capability                     | Scope              | Approval Authority                                   | Notes                                           |
| ------------------------------ | ------------------ | ---------------------------------------------------- | ----------------------------------------------- |
| `sales:order-read`             | Branch             | No                                                   | Read sales orders and revisions.                |
| `sales:order-write`            | Branch             | No                                                   | Create and edit drafts.                         |
| `sales:pricing-write`          | Branch             | No                                                   | Maintain price lists.                           |
| `sales:commercial-approve`     | Branch / Warehouse | Yes, with amount/percentage limits and Maker-Checker | Approve commercial exceptions and reservations. |
| `sales:discount-approve`       | Branch             | Yes                                                  | Approve discount exceptions.                    |
| `sales:below-floor-approve`    | Branch             | Yes                                                  | Approve below-floor pricing.                    |
| `sales:credit-override`        | Branch             | Yes                                                  | Override credit exposure checks.                |
| `sales:cod-convert-on-account` | Branch             | Yes                                                  | Convert COD deliveries to on-account terms.     |
| `sales:projection-rebuild`     | Global             | No                                                   | Rebuild sales/credit projections.               |

### Fulfillment

| Capability                                  | Scope                  | Approval Authority | Notes                                   |
| ------------------------------------------- | ---------------------- | ------------------ | --------------------------------------- |
| `fulfillment:pick-release`                  | Warehouse              | No                 | Release reservations to picking.        |
| `fulfillment:pick`                          | Warehouse              | No                 | Record picks.                           |
| `fulfillment:pick-reverse`                  | Warehouse              | Yes                | Reverse posted picks.                   |
| `fulfillment:dispatch`                      | Warehouse              | No                 | Create dispatches.                      |
| `fulfillment:delivery-confirm`              | Warehouse / Assignment | No                 | Confirm deliveries and collect COD.     |
| `fulfillment:delivery-correction-authorize` | Warehouse              | Yes                | Authorize delivery receipt corrections. |
| `fulfillment:return-receive`                | Warehouse              | No                 | Receive returns into quarantine.        |

### Finance

| Capability                    | Scope  | Approval Authority | Notes                                                   |
| ----------------------------- | ------ | ------------------ | ------------------------------------------------------- |
| `finance:payment-read`        | Branch | No                 | Read payment receipts.                                  |
| `finance:payment-record`      | Branch | No                 | Record cash receipts.                                   |
| `finance:payment-verify`      | Branch | Yes, Maker-Checker | Verify bank transfers, checks, and electronic receipts. |
| `finance:payment-reverse`     | Branch | Yes, Maker-Checker | Reverse posted receipts.                                |
| `finance:payment-refund`      | Branch | Yes, Maker-Checker | Issue refunds.                                          |
| `finance:payment-allocate`    | Branch | No                 | Allocate payments to invoices.                          |
| `finance:invoice-post`        | Branch | Yes                | Post draft invoices to receivables.                     |
| `finance:invoice-void`        | Branch | Yes                | Void posted invoices.                                   |
| `finance:credit-note-approve` | Branch | Yes, Maker-Checker | Approve credit notes.                                   |
| `finance:cash-reconcile`      | Branch | Yes                | Reconcile cash collections.                             |
| `finance:statement-read`      | Branch | No                 | Read customer statements.                               |

### Inventory

| Capability                        | Scope              | Approval Authority | Notes                                   |
| --------------------------------- | ------------------ | ------------------ | --------------------------------------- |
| `inventory:read`                  | Branch / Warehouse | No                 | Read availability and valuation.        |
| `inventory:post`                  | Warehouse          | Yes                | Post goods receipts and adjustments.    |
| `inventory:rebuild`               | Global             | No                 | Rebuild inventory projections.          |
| `inventory:investigation-resolve` | Warehouse          | Yes                | Resolve damaged/missing investigations. |

## Error codes

| Code                          | HTTP | Meaning                                                                    |
| ----------------------------- | ---- | -------------------------------------------------------------------------- |
| `authentication_required`     | 401  | Missing or malformed bearer token.                                         |
| `invalid_token`               | 401  | Token signature, expiry, issuer, audience, or capability claim is invalid. |
| `capability_required`         | 403  | User lacks the required Capability.                                        |
| `operational_access_required` | 403  | User is not active in TradeFlow operational data.                          |
| `operational_scope_required`  | 403  | User's scope does not cover the requested Branch or Warehouse.             |
| `approval_authority_required` | 403  | No Approval Authority row matches Capability + Scope.                      |
| `approval_limit_exceeded`     | 403  | The exception exceeds the approver's amount or percentage limit.           |
| `maker_checker_violation`     | 409  | Approver is the same person as the requester.                              |

## Tests

Authorization matrix behavior is covered by contract tests in
`apps/api/tests/test_authorization_matrix_contract.py` and by the existing
organization, sales, and finance contract suites. These tests verify:

- Missing capability is denied.
- Cross-branch and cross-warehouse scope is denied.
- Administrator status does not auto-approve business actions.
- Approval without an Authority row is denied.
- Over-limit approvals are denied.
- Self-approval is denied.
- Valid capability + scope + limit + different approver succeeds.
