# Fulfillment domain language

**Reservation Generation**: The exact Warehouse-specific set of sales-order
line quantities created by one successful reservation or re-reservation. A
release closes that generation; any later reservation creates a new generation.

**Fulfillment Order**: The instruction assigning one active Reservation
Generation to one Warehouse. Fulfilling a Sales Order from multiple Warehouses
requires a separate Fulfillment Order for each Warehouse, and Backorder Demand
is never included.

**Reserved Value**: The approved order payable value allocated to a Fulfillment
Order's reserved quantities under the Sales Order's immutable Calculation
Snapshot. It excludes Backorder Demand and is not recalculated from current
prices.

**Released Quantity**: Reserved quantity authorized by Pick Release for one
Fulfillment Order line. It never exceeds that line's active Reserved Quantity.

**Pick Release**: Authorization for warehouse staff to begin picking a
Fulfillment Order's Released Quantity. It does not assign physical identities.
A Prepaid Fulfillment Order requires active Prepayment Coverage Designations
whose total equals or exceeds that exact Reservation Generation's Reserved
Value; partial coverage does not authorize partial Pick Release.

**Pick**: The act of identifying and moving reserved goods for dispatch.
Picking assigns required Lot Identities or Serial Identities.
Expiration-controlled stock defaults to FEFO; selecting another eligible lot
requires an authorized reason.

**Pick Assignment**: Association of exact Base Stocking Unit quantity with
every Lot Identity or Serial Identity required by Tracking Policy before a Pick
posts. Each Serial Identity satisfies exactly one Base Stocking Unit.

**Partial Pick**: A posted Pick smaller than Released Quantity. Posted quantity
is in Dispatch Staging while the remainder stays released and reserved; it is
not silently backordered or completed.

**Pick Conflict**: Server rejection because released or reserved quantity,
identity eligibility, or authoritative Pick state changed after selection. It
requires refresh and explicit reconciliation, never automatic merging.

**Pick Reversal**: An authorized, reasoned immutable transfer linked to an
undispatched Pick that returns its exact quantity and identities from Dispatch
Staging. It does not edit the original Pick.

**Dispatch**: The release of picked goods to a delivery run or carrier,
transferring quantity from Dispatch Staging to In Transit.

**Delivery**: A physical shipment to one customer address. One sales order may
have multiple deliveries.

**Delivery Line**: The quantity of one sales-order line included in a delivery.

**Delivery Receipt**: The issued business document describing delivered goods.
One server-accepted Delivery Confirmation issues one immutable Delivery Receipt
for accepted quantity. It is not itself proof that the customer received the
goods.

**Document Series**: A Branch-specific numbering sequence that assigns a unique,
never-reused Delivery Receipt number and audits voided or skipped numbers.

**Delivery Correction**: An authorized, reasoned reversal and replacement
workflow linked to the original Delivery Confirmation, stock movements, and
Delivery Receipt. A requester proposes one complete corrected quantity and
tracked-identity partition, and a different eligible approver posts it against
the current receipt-chain head. It does not edit an issued receipt, Proof of
Delivery, stock movement, or Draft Invoice source.

**Replacement Delivery Receipt**: A new Branch-numbered immutable receipt
issued when a posted Delivery Correction retains accepted quantity. It links
bidirectionally to the prior receipt; the prior number and customer-readable
document remain valid historical records marked as corrected.

**Proof of Delivery**: Evidence of receipt such as recipient name, signature,
photo, timestamp, or delivery exception.

**Delivery Confirmation**: The server-accepted outcome of a Delivery identifying
the quantities accepted as delivered and any Delivery Exceptions. It triggers
outbound stock posting and Draft Invoice creation for accepted quantity.
For Cash on Delivery, it also requires a sufficient COD Payment Receipt or an
authorized conversion of the unpaid amount to On Account.
Each dispatched line is partitioned exactly into accepted, refused, damaged,
short or missing, and still-undelivered quantity.

**Delivery Exception**: A failure or variance such as refused, short, damaged,
or unreachable delivery.

**Return-to-Warehouse Receipt**: Confirmation that refused, damaged, or other
undelivered In Transit quantity has physically returned. It moves quantity to
Quarantine pending inspection, never directly to Available.

**Delivered Quantity**: Quantity confirmed through the delivery workflow and
posted as an outbound stock movement.
