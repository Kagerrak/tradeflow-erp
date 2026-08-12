# Sales domain language

**Quotation**: A non-binding commercial proposal that may become a sales order.

**Sales Order**: The approved customer commitment, owned by one Branch, defining
products, quantities, prices, taxes, discounts, addresses, and requested
fulfillment. It snapshots the Customer Account's Payment Timing Policy, with
authorized overrides retaining a reason.

**Sales Order Draft**: A mutable, server-authoritative commercial proposal
captured before Commercial Approval. It may be repriced or edited and consumes
neither Credit Exposure nor Inventory Reservation.

**Sales Order Line**: One priced product and quantity commitment within a sales
order.

**Pending Sync**: A locally retained Sales Order Draft command that has not
received server acknowledgement. It is captured work, not an approved customer
commitment, and consumes neither Credit Exposure nor Inventory Reservation.

**Sync Conflict**: A rejected draft command whose expected server version no
longer matches the authoritative Sales Order Draft. It requires explicit user
review and is not automatically merged.

**Commercial Approval**: Authorization that credit, price, discount, and terms
allow an order to proceed and request inventory reservation.
For On Account orders, approval serializes the Customer Account's Credit
Exposure check and requires a Credit Override when the Credit Limit is absent
or exceeded.

**Commercial Approval Request**: The maker's request to approve one immutable
Sales Order revision against a selected fulfillment Warehouse. It cannot be
silently carried to a later revision.

**Commercial Review Evidence**: The read model presented to a checker before
approval. It combines the exact priced revision and maker, current Customer
status, terms, hold and Credit Exposure, required exception measurements, and
Warehouse-specific reservable and Backorder quantities. It is advisory until
the approval command repeats all controls under serialized database locks.

**Commercial Exception Approval**: Immutable evidence that a different
eligible User approved a specific below-floor, excess-discount, or credit
exception within their Approval Authority. It records the measured exception,
reason, approved revision, and authority limits used.

**Approved Uninvoiced Exposure**: The approved On Account value for an
uncancelled Sales Order that has not yet been replaced by its posted Invoice.

**Order Hold**: A reversible block preventing reservation or fulfillment.

**Payment Deadline**: The Branch-policy deadline by which a Prepaid Sales
Order's active Fulfillment Order Reservation Generation must have sufficient
Prepayment Coverage Designations for its full Reserved Value. If it does not,
the entire generation is released atomically and idempotently to Backorder
Demand; partial payment does not retain a partial reservation.

**Payment Hold**: An Order Hold applied after a Prepaid Payment Deadline expires
without sufficient Prepayment Coverage. It blocks Pick Release but not the
controlled re-reservation attempt; later payment alone neither removes the hold
nor restores the released reservation, and Pick Release requires a new
successful Reservation Generation with sufficient coverage.

**Open Quantity**: Ordered quantity not cancelled or fulfilled.

**Partial Fulfillment**: Delivery of less than the full open order quantity.

**Order Cancellation**: Removal of unfulfilled order quantity. Delivered
quantity is handled through a return, not cancellation.

**Price List**: An effective-dated source of default selling prices.
Customer-specific Price Lists take precedence over the Branch default.

**Price List Version**: One immutable effective-dated definition of a Price
List whose prices and inclusion mode can be snapshotted by a Sales Order Line.

**Price Inclusion Mode**: A Price List policy declaring whether its prices
include or exclude sales tax. A Sales Order uses one mode across all lines.

**Pricing Snapshot**: The Price List version, list price, manual override,
allocated line discount, tax treatment, and final unit price retained by an
approved Sales Order Line.

**Calculation Snapshot**: The decimal quantity, unit price, rates, rounding
inputs, rounded line amounts, and deterministic allocation results retained so
approved totals can be reproduced.

**Price Override**: An authorized replacement of the applicable list price. A
price below the SKU's configured floor requires maker-checker approval.

**Payment Timing Override**: An authorized deviation from the Customer
Account's default Payment Timing Policy that retains the requesting User and
reason on the Sales Order.

**Tax Code**: An effective-dated classification that determines the sales-tax
rate and treatment for a Sales Order Line.

**Tax Snapshot**: The Tax Code, rate, inclusion mode, taxable basis, and
calculated tax retained by an approved Sales Order Line so its totals remain
reproducible after tax policy changes.

**Customer Tax Exemption**: An explicit, validity-dated authorization for
applying exempt tax treatment to a Customer.

**Discount Approval**: Maker-checker authorization from a different eligible
User when an allocated line discount exceeds the requesting actor's permitted
threshold.

**Approval-invalidating Change**: A change to Customer, Branch, fulfillment
Warehouse, quantity, price, discount, Tax Code, or Payment Timing Policy that
requires Commercial Approval and its credit and reservation checks to run
again.

**Non-material Order Change**: A note or delivery instruction change that does
not alter pricing, credit, tax, quantity, Warehouse, or payment timing and
therefore does not invalidate Commercial Approval.
