# Returns domain language

**Return Request**: A customer's request to return previously delivered goods.

**Return Authorization**: Approval identifying eligible delivery lines,
quantities, reason, and expected resolution.

**Return Receipt**: Confirmation that returned goods physically arrived at a
controlled location.

**Return Inspection**: Assessment of received condition, quantity, and cause.

**Disposition**: The approved result for returned quantity: restock,
quarantine, repair, supplier return, or write-off.

**Damaged Item**: Returned or discovered stock that cannot enter available
inventory without an approved later disposition.

**Replacement**: New fulfillment created to substitute approved returned goods.

**Credit Request**: A return outcome requesting a finance credit. It does not
change the customer balance until Finance posts a Credit Note.

**Return Reason**: A controlled classification such as transit damage, product
defect, wrong item, excess delivery, or customer error.

**Return Eligibility**: The current Delivery Receipt correction-chain head's
Delivered Quantity less quantity already authorized for customer return. A
pending Return Request does not reserve quantity; maker-checker Return
Authorization reserves it atomically. Once a return is authorized, the source
Delivery Receipt is no longer eligible for Delivery Correction.

**Responsible Party**: The controlled attribution captured with a Return
Request, such as customer, carrier, supplier, warehouse, or company. The code
and display label are snapshotted on the immutable request so later master-data
changes do not rewrite history.
