"""Create tracked-stock picking and Dispatch Staging ledgers.

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    statements = """
        ALTER TABLE warehouse_stock_locations
          DROP CONSTRAINT ck_warehouse_stock_locations_custody;
        ALTER TABLE warehouse_stock_locations
          ADD CONSTRAINT ck_warehouse_stock_locations_custody
          CHECK (custody IN ('available','quarantine','dispatch_staging'));
        CREATE UNIQUE INDEX uq_warehouse_active_dispatch_staging
          ON warehouse_stock_locations (warehouse_id)
          WHERE custody = 'dispatch_staging' AND is_active;

        ALTER TABLE inventory_reservation_events
          DROP CONSTRAINT ck_inventory_reservation_events_type;
        ALTER TABLE inventory_reservation_events
          ADD CONSTRAINT ck_inventory_reservation_events_type
          CHECK (event_type IN ('reserved','released','consumed','restored'));

        ALTER TABLE sales_order_line_commitments
          ADD COLUMN picked_quantity_base numeric(18,6) NOT NULL DEFAULT 0;
        ALTER TABLE sales_order_line_commitments
          DROP CONSTRAINT ck_sales_order_line_commitments_quantities;
        ALTER TABLE sales_order_line_commitments
          ADD CONSTRAINT ck_sales_order_line_commitments_quantities CHECK (
            ordered_quantity_base > 0
            AND reserved_quantity_base >= 0
            AND picked_quantity_base >= 0
            AND backorder_quantity_base >= 0
            AND reserved_quantity_base + picked_quantity_base + backorder_quantity_base
              = ordered_quantity_base
          );

        ALTER TABLE fulfillment_order_state
          ADD COLUMN picked_quantity_base numeric(18,6) NOT NULL DEFAULT 0;
        ALTER TABLE fulfillment_order_state
          DROP CONSTRAINT ck_fulfillment_order_state_status;
        ALTER TABLE fulfillment_order_state
          ADD CONSTRAINT ck_fulfillment_order_state_status CHECK (
            status IN ('reserved','payment_ready','pick_released','partially_picked',
                       'picked','payment_hold','cancelled')
          );
        ALTER TABLE fulfillment_order_state
          DROP CONSTRAINT ck_fulfillment_order_state_amounts;
        ALTER TABLE fulfillment_order_state
          ADD CONSTRAINT ck_fulfillment_order_state_amounts CHECK (
            reserved_quantity_base >= 0 AND backorder_quantity_base >= 0
            AND covered_amount >= 0 AND picked_quantity_base >= 0
          );

        ALTER TABLE fulfillment_orders
          DROP CONSTRAINT uq_fulfillment_order_generation;
        ALTER TABLE fulfillment_orders
          ADD CONSTRAINT uq_fulfillment_order_generation
          UNIQUE (sales_order_id,warehouse_id,reservation_generation);

        ALTER TABLE stock_movements
          DROP CONSTRAINT ck_stock_movements_type;
        ALTER TABLE stock_movements
          ADD COLUMN movement_group_id uuid;
        ALTER TABLE stock_movements
          ADD COLUMN movement_leg varchar(40);
        ALTER TABLE stock_movements
          ADD COLUMN reversal_of_movement_id uuid REFERENCES stock_movements(movement_id);
        ALTER TABLE stock_movements DISABLE TRIGGER trg_stock_movements_immutable;
        UPDATE stock_movements
          SET movement_group_id = movement_id, movement_leg = 'opening_in';
        ALTER TABLE stock_movements ENABLE TRIGGER trg_stock_movements_immutable;
        ALTER TABLE stock_movements ALTER COLUMN movement_group_id SET NOT NULL;
        ALTER TABLE stock_movements ALTER COLUMN movement_leg SET NOT NULL;
        ALTER TABLE stock_movements
          ADD CONSTRAINT ck_stock_movements_type CHECK (
            movement_type IN ('opening_stock','pick','pick_reversal')
          );
        ALTER TABLE stock_movements
          ADD CONSTRAINT ck_stock_movements_leg CHECK (
            (movement_type = 'opening_stock' AND movement_leg = 'opening_in')
            OR (movement_type = 'pick'
                AND movement_leg IN ('pick_available_out','pick_staging_in'))
            OR (movement_type = 'pick_reversal'
                AND movement_leg IN ('pick_reversal_staging_out',
                                     'pick_reversal_available_in'))
          );
        ALTER TABLE stock_movements
          ADD CONSTRAINT uq_stock_movement_group_leg
          UNIQUE (movement_group_id,movement_leg);
        CREATE UNIQUE INDEX uq_stock_movement_reversal
          ON stock_movements (reversal_of_movement_id)
          WHERE reversal_of_movement_id IS NOT NULL;

        CREATE TABLE pick_postings (
          pick_id uuid PRIMARY KEY,
          fulfillment_order_id uuid NOT NULL REFERENCES fulfillment_orders(fulfillment_order_id),
          pick_release_id uuid NOT NULL REFERENCES pick_releases(pick_release_id),
          warehouse_id uuid NOT NULL REFERENCES warehouses(warehouse_id),
          event_type varchar(20) NOT NULL,
          reversal_of_pick_id uuid REFERENCES pick_postings(pick_id),
          reason varchar(500),
          actor_subject varchar(200) NOT NULL REFERENCES users(subject),
          correlation_id varchar(100) NOT NULL,
          idempotency_key varchar(200) NOT NULL UNIQUE,
          posted_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_pick_postings_event_type
            CHECK (event_type IN ('posted','reversed')),
          CONSTRAINT ck_pick_postings_reversal_shape CHECK (
            (event_type = 'posted' AND reversal_of_pick_id IS NULL)
            OR (event_type = 'reversed' AND reversal_of_pick_id IS NOT NULL)
          )
        );
        CREATE UNIQUE INDEX uq_pick_posting_reversal
          ON pick_postings (reversal_of_pick_id)
          WHERE reversal_of_pick_id IS NOT NULL;

        CREATE TABLE pick_lines (
          pick_line_id uuid PRIMARY KEY,
          pick_id uuid NOT NULL REFERENCES pick_postings(pick_id),
          fulfillment_order_id uuid NOT NULL,
          line_id uuid NOT NULL,
          sku_id uuid NOT NULL REFERENCES skus(sku_id),
          warehouse_id uuid NOT NULL REFERENCES warehouses(warehouse_id),
          source_location_id uuid NOT NULL REFERENCES warehouse_stock_locations(location_id),
          staging_location_id uuid NOT NULL REFERENCES warehouse_stock_locations(location_id),
          quantity_base numeric(18,6) NOT NULL,
          entered_quantity numeric(18,6) NOT NULL,
          entered_unit varchar(30) NOT NULL,
          conversion_snapshot jsonb NOT NULL,
          capture_mode varchar(20) NOT NULL,
          barcode_mapping_id uuid REFERENCES barcode_mappings(barcode_mapping_id),
          manual_reason varchar(500),
          fefo_override_reason varchar(500),
          movement_group_id uuid NOT NULL UNIQUE,
          source_movement_id uuid NOT NULL UNIQUE REFERENCES stock_movements(movement_id),
          staging_movement_id uuid NOT NULL UNIQUE REFERENCES stock_movements(movement_id),
          CONSTRAINT ck_pick_lines_quantity CHECK (
            quantity_base > 0 AND entered_quantity > 0
          ),
          CONSTRAINT ck_pick_lines_capture_mode
            CHECK (capture_mode IN ('automatic','barcode','manual')),
          CONSTRAINT ck_pick_lines_locations
            CHECK (source_location_id <> staging_location_id),
          CONSTRAINT fk_pick_lines_fulfillment_line FOREIGN KEY
            (fulfillment_order_id,line_id)
            REFERENCES fulfillment_order_lines (fulfillment_order_id,line_id)
        );

        CREATE TABLE pick_identity_assignments (
          pick_identity_assignment_id uuid PRIMARY KEY,
          pick_line_id uuid NOT NULL REFERENCES pick_lines(pick_line_id),
          tracking_policy varchar(20) NOT NULL,
          lot_identity_id uuid REFERENCES lot_identities(lot_identity_id),
          serial_allocation_id uuid REFERENCES stock_serial_allocations(serial_allocation_id),
          captured_barcode varchar(100),
          quantity_base numeric(18,6) NOT NULL,
          CONSTRAINT ck_pick_identity_assignments_policy
            CHECK (tracking_policy IN ('lot','serial')),
          CONSTRAINT ck_pick_identity_assignments_quantity CHECK (quantity_base > 0),
          CONSTRAINT ck_pick_identity_assignments_shape CHECK (
            (tracking_policy = 'lot' AND lot_identity_id IS NOT NULL
             AND serial_allocation_id IS NULL)
            OR (tracking_policy = 'serial' AND lot_identity_id IS NULL
                AND serial_allocation_id IS NOT NULL AND quantity_base = 1)
          ),
          CONSTRAINT uq_pick_identity_assignment_lot
            UNIQUE (pick_line_id,lot_identity_id),
          CONSTRAINT uq_pick_identity_assignment_serial
            UNIQUE (pick_line_id,serial_allocation_id)
        );

        CREATE TABLE fulfillment_line_pick_state (
          fulfillment_order_id uuid NOT NULL,
          line_id uuid NOT NULL,
          released_quantity_base numeric(18,6) NOT NULL,
          picked_quantity_base numeric(18,6) NOT NULL DEFAULT 0,
          reversed_quantity_base numeric(18,6) NOT NULL DEFAULT 0,
          version integer NOT NULL DEFAULT 1,
          PRIMARY KEY (fulfillment_order_id,line_id),
          CONSTRAINT fk_fulfillment_line_pick_state_line FOREIGN KEY
            (fulfillment_order_id,line_id)
            REFERENCES fulfillment_order_lines (fulfillment_order_id,line_id),
          CONSTRAINT ck_fulfillment_line_pick_state_quantities CHECK (
            released_quantity_base > 0
            AND picked_quantity_base >= 0
            AND reversed_quantity_base >= 0
            AND picked_quantity_base >= reversed_quantity_base
            AND picked_quantity_base - reversed_quantity_base <= released_quantity_base
          ),
          CONSTRAINT ck_fulfillment_line_pick_state_version CHECK (version > 0)
        );
        INSERT INTO fulfillment_line_pick_state
          (fulfillment_order_id,line_id,released_quantity_base)
          SELECT lines.fulfillment_order_id,lines.line_id,lines.reserved_quantity_base
          FROM fulfillment_order_lines AS lines
          JOIN fulfillment_order_state AS state
            ON state.fulfillment_order_id = lines.fulfillment_order_id
          WHERE lines.reserved_quantity_base > 0
            AND state.status = 'pick_released';
        """
    for statement in statements.split(";\n"):
        if statement.strip():
            op.execute(statement)
    _create_immutable_guards()


def _create_immutable_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION prevent_pick_ledger_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'Pick ledgers are immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table_name in ("pick_postings", "pick_lines", "pick_identity_assignments"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_immutable
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION prevent_pick_ledger_mutation()
            """
        )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pick_postings) THEN
            RAISE EXCEPTION
              'Cannot downgrade 0010 while immutable Pick ledger data exists';
          END IF;
          IF EXISTS (
            SELECT 1
            FROM fulfillment_orders
            GROUP BY sales_order_id,reservation_generation
            HAVING count(*) > 1
          ) THEN
            RAISE EXCEPTION
              'Cannot downgrade 0010 while multi-Warehouse Fulfillment generations exist';
          END IF;
        END
        $$
        """
    )
    for table_name in ("pick_identity_assignments", "pick_lines", "pick_postings"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_immutable ON {table_name}")
    op.execute("DROP FUNCTION IF EXISTS prevent_pick_ledger_mutation")
    op.execute("DROP TABLE fulfillment_line_pick_state")
    op.execute("DROP TABLE pick_identity_assignments")
    op.execute("DROP TABLE pick_lines")
    op.execute("DROP INDEX uq_pick_posting_reversal")
    op.execute("DROP TABLE pick_postings")
    op.execute("DROP INDEX uq_stock_movement_reversal")
    op.execute("ALTER TABLE stock_movements DROP CONSTRAINT uq_stock_movement_group_leg")
    op.execute("ALTER TABLE stock_movements DROP CONSTRAINT ck_stock_movements_leg")
    op.execute("ALTER TABLE stock_movements DROP CONSTRAINT ck_stock_movements_type")
    op.execute("ALTER TABLE stock_movements DROP COLUMN reversal_of_movement_id")
    op.execute("ALTER TABLE stock_movements DROP COLUMN movement_leg")
    op.execute("ALTER TABLE stock_movements DROP COLUMN movement_group_id")
    op.execute(
        "ALTER TABLE stock_movements ADD CONSTRAINT ck_stock_movements_type "
        "CHECK (movement_type = 'opening_stock')"
    )
    op.execute(
        "ALTER TABLE fulfillment_order_state DROP CONSTRAINT ck_fulfillment_order_state_amounts"
    )
    op.execute(
        "ALTER TABLE fulfillment_order_state DROP CONSTRAINT ck_fulfillment_order_state_status"
    )
    op.execute("ALTER TABLE fulfillment_order_state DROP COLUMN picked_quantity_base")
    op.execute(
        "ALTER TABLE fulfillment_order_state "
        "ADD CONSTRAINT ck_fulfillment_order_state_status "
        "CHECK (status IN ('reserved','payment_ready','pick_released',"
        "'payment_hold','cancelled'))"
    )
    op.execute(
        "ALTER TABLE fulfillment_order_state "
        "ADD CONSTRAINT ck_fulfillment_order_state_amounts "
        "CHECK (reserved_quantity_base >= 0 AND backorder_quantity_base >= 0 "
        "AND covered_amount >= 0)"
    )
    op.execute("ALTER TABLE fulfillment_orders DROP CONSTRAINT uq_fulfillment_order_generation")
    op.execute(
        "ALTER TABLE fulfillment_orders ADD CONSTRAINT uq_fulfillment_order_generation "
        "UNIQUE (sales_order_id,reservation_generation)"
    )
    op.execute(
        "ALTER TABLE sales_order_line_commitments "
        "DROP CONSTRAINT ck_sales_order_line_commitments_quantities"
    )
    op.execute("ALTER TABLE sales_order_line_commitments DROP COLUMN picked_quantity_base")
    op.execute(
        "ALTER TABLE sales_order_line_commitments "
        "ADD CONSTRAINT ck_sales_order_line_commitments_quantities CHECK ("
        "ordered_quantity_base > 0 AND reserved_quantity_base >= 0 "
        "AND backorder_quantity_base >= 0 "
        "AND reserved_quantity_base + backorder_quantity_base = ordered_quantity_base)"
    )
    op.execute(
        "ALTER TABLE inventory_reservation_events "
        "DROP CONSTRAINT ck_inventory_reservation_events_type"
    )
    op.execute(
        "ALTER TABLE inventory_reservation_events "
        "ADD CONSTRAINT ck_inventory_reservation_events_type "
        "CHECK (event_type IN ('reserved','released'))"
    )
    op.execute("DROP INDEX uq_warehouse_active_dispatch_staging")
    op.execute(
        "ALTER TABLE warehouse_stock_locations DROP CONSTRAINT ck_warehouse_stock_locations_custody"
    )
    op.execute(
        "ALTER TABLE warehouse_stock_locations "
        "ADD CONSTRAINT ck_warehouse_stock_locations_custody "
        "CHECK (custody IN ('available','quarantine'))"
    )
