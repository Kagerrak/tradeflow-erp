# Customers domain language

**Customer**: The legal or commercial party buying goods. A Customer is not a
login account or contact person.

**Customer Account**: The commercial relationship containing status, sales
terms, credit policy, and assigned salesperson.

**Contact**: A named person associated with a customer and a communication role.

**Customer Address**: A versioned billing or delivery location. Historical
orders retain their address snapshot.

**Payment Terms**: The rule determining an invoice due date, such as due on
receipt or net 30.

**Payment Timing Policy**: The Customer Account default for when payment is
required: Prepaid, Cash on Delivery, or On Account. A Sales Order snapshots the
applicable policy.

**Prepaid**: A Payment Timing Policy requiring cleared Customer Prepayment for
reserved quantity before its Fulfillment Order is released for picking.

**Cash on Delivery**: A Payment Timing Policy requiring collection as part of
the delivery workflow. Unpaid accepted delivery requires authorized conversion
to On Account.

**On Account**: A Payment Timing Policy allowing delivery before payment under
Payment Terms and customer credit controls.

**Credit Limit**: The approved exposure ceiling used during sales-order checks.
It is not the customer's current balance.

**Credit Exposure**: Posted Open Balance plus approved, uncancelled Sales Order
value not yet represented by a posted Invoice.

**Credit Hold**: A restriction preventing new commercial approval until an
authorized user releases it.

**Credit Override**: Order-specific authorization to exceed or proceed without
a Credit Limit. It retains the approver, reason, exposure snapshot, and approved
excess.

**Salesperson Assignment**: The effective-dated relationship used as one input
to commission attribution.
