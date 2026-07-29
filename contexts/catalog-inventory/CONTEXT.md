# Catalog and inventory domain language

**Product**: A sellable or purchasable item definition.

**SKU**: The unique stock-keeping identifier for one inventory item variant.

**Unit of Measure**: The quantity basis in which an item is ordered, stocked,
sold, or converted.

**Base Stocking Unit**: The immutable Unit of Measure in which a SKU's stock
movements, availability, and reservations are expressed.

**Unit Conversion**: A product-specific, effective-dated fixed ratio between an
entered Unit of Measure and the Base Stocking Unit.

**Unit Conversion Snapshot**: The entered unit, entered quantity, conversion
ratio, and resulting base quantity retained by a business document. A
conversion already used by a posted transaction is not edited.

**Warehouse**: A Branch-operated controlled location that owns stock balances.

**Stock Location**: A warehouse subdivision such as receiving, available,
quarantine, damaged, or dispatch.

**Custody Location**: A controlled non-warehouse stock state such as In Transit
or Investigation that retains responsibility for quantity outside warehouse
on-hand.

**Stock Movement**: An immutable increase, decrease, or transfer of quantity
between locations with a source document and posting time.

**On-hand Quantity**: Posted physical quantity in eligible warehouse locations.

**Reserved Quantity**: On-hand quantity committed to open fulfillment demand.

**Available Quantity**: On-hand quantity minus effective reservations.

**Dispatch Staging**: A Stock Location holding picked quantity that remains
warehouse on-hand but is no longer available.

**In Transit**: A Custody Location holding dispatched quantity until accepted
delivery or physical return.

**Investigation**: A Custody Location holding short or missing dispatched
quantity until approved recovery, claim, or Inventory Adjustment.

**Inventory Reservation**: A Warehouse-specific commitment of eligible
available quantity to commercially approved sales-order demand. It does not
remove stock and cannot cause Reserved Quantity to exceed eligible On-hand
Quantity.

**Reservation Event**: An immutable reservation or release entry in Base
Stocking Unit quantity for one Sales Order Line, SKU, and Warehouse. Current
Reserved Quantity is a rebuildable projection of these events.

**Reservation Release**: An idempotent return of reserved quantity to
availability caused by fulfillment, cancellation, an approval-invalidating
change, an expired Payment Deadline, or an authorized manual action with a
reason.

**Backorder Demand**: Open sales-order quantity that could not be reserved. It
remains awaiting later reservation and is not eligible for fulfillment.

**Inventory Adjustment**: An authorized stock movement correcting a verified
physical difference. It is not a direct balance edit.

**Tracking Policy**: A SKU rule requiring no tracked identity, a Lot Identity,
or a Serial Identity for received and outbound stock.

**Lot Identity**: A shared traceability identity for a batch of a lot-tracked
SKU.

**Serial Identity**: A unique traceability identity for one unit of a
serial-tracked SKU.

**Expiration Control**: A SKU policy requiring an expiration date for each Lot
Identity and prohibiting outbound posting after expiration.

**FEFO**: First-expiring-first-out selection of eligible, unexpired lots during
picking.

**Barcode Mapping**: A unique active association from a barcode to a SKU and
Unit of Measure, or to a specific Lot Identity or Serial Identity.

**Moving Average Unit Cost**: The perpetual weighted-average Base Currency cost
of one Base Stocking Unit for a SKU in one Warehouse.

**Inventory Value Adjustment**: An immutable cost-only correction or
revaluation linked to its source and reason. It does not rewrite a prior Stock
Movement.
