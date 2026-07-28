# Fulfillment domain language

**Fulfillment Order**: The instruction assigning eligible sales-order demand to
one Warehouse. Fulfilling a Sales Order from multiple Warehouses requires a
separate Fulfillment Order for each Warehouse.

Only reserved sales-order quantity is eligible for a Fulfillment Order.

**Pick Release**: Authorization for warehouse staff to begin picking a
Fulfillment Order. A Prepaid order requires sufficient cleared Customer
Prepayment before Pick Release.

**Pick**: The act of identifying and moving reserved goods for dispatch.
Picking assigns required Lot Identities or Serial Identities.
Expiration-controlled stock defaults to FEFO; selecting another eligible lot
requires an authorized reason.

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
Delivery Receipt. It does not edit an issued receipt.

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
