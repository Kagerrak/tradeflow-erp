# Inventory domain language

**Stock Movement**: An immutable record of a change in custody, ownership, or
value of stock. Every movement has a `movement_group_id`, a `movement_type`, and
a `leg` describing the affected warehouse and location.

**Custody**: The warehouse and location that physically hold stock. `available`
custody is sellable and transferable; `in_transit` custody is goods authorized
for movement but not yet available at the destination.

**Warehouse Stock Location**: A named location inside one warehouse with a
`location_kind` of `available` or `in_transit`. The `in_transit` location for a
warehouse is created lazily when a transfer or dispatch needs it.

**Inventory Transfer**: An authorized warehouse-to-warehouse movement of stock at
source cost. A transfer starts in `released` status and becomes `received` when
the destination warehouse accepts the goods. The `inventory_transfers` table is
immutable except for the allowed `released` → `received` transition.

**Transfer Release**: The first movement group of a transfer. It reduces source
`available` quantity and increases source `in_transit` quantity. Source valuation
is reduced by `quantity × source_moving_average_unit_cost`.

**Transfer Receive**: The second movement group of a transfer. It reduces source
`in_transit` quantity and increases destination `available` quantity. Destination
valuation is increased by the source cost captured at release time. Total company
inventory value does not change.

**Source Cost Preservation**: A transfer does not reprice goods. The destination
warehouse receives the same moving-average unit cost that the source warehouse
had at release time, so inter-warehouse movement cannot create or destroy value.

**Lot Identity**: A tracked stock identifier required for lot-controlled SKUs. A
transfer must specify the exact `lot_code` when the SKU's tracking policy is
`lot`.

**Serial Identity**: A unique tracked stock identifier required for
serial-controlled SKUs. Transfers of serial-tracked SKUs are not supported in the
initial transfer slice.

**Inventory Adjustment**: A controlled counted-variance posting that increases or
decreases stock on hand with a reason and approval lifecycle. Adjustments are out
of scope for the transfer slice and will be delivered separately.

**Availability Projection**: The authoritative computed view of on-hand,
reserved, and available quantity per SKU, warehouse, location, and lot. Transfer
movements are applied through `inventory_projection_service`, not by direct edits
to `inventory_availability`.

**Valuation Projection**: The authoritative computed view of inventory value per
SKU and warehouse. Transfer movements adjust source and destination valuation
through the shared projection service using the source cost.

**Scope**: An actor may request or receive a transfer only when both the source
and destination warehouses are in the actor's `warehouse_ids`.

**Command Receipt**: A stored response keyed by `Idempotency-Key` that makes
transfer commands replay-safe. Replays return the original response with
`X-Idempotency-Replayed: true`.
