# Return Request and Authorization

TradeFlow now records immutable customer Return Requests against the current
Delivery Receipt and lets a different authorized operator approve only the
remaining Delivered Quantity. The responsive web review queue shows reason,
responsibility, requested quantity, delivered quantity, remaining eligibility,
and affected Base Currency value before approval.

Authorization is retry-safe, versioned, capability- and Branch/Warehouse-scoped,
value-limited, and serialized with Delivery Correction. It creates no inventory
or financial posting. Offline evidence, physical Return Receipt, inspection,
Disposition, credit, and replacement remain assigned to later Returns slices.
