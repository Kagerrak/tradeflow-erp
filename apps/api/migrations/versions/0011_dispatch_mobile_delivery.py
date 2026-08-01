"""Create Delivery dispatch and In Transit custody ledgers.

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    statements = """
        ALTER TABLE warehouse_stock_locations
          DROP CONSTRAINT ck_warehouse_stock_locations_custody;
        ALTER TABLE warehouse_stock_locations
          ADD CONSTRAINT ck_warehouse_stock_locations_custody
          CHECK (custody IN ('available','quarantine','dispatch_staging','in_transit'));
        CREATE UNIQUE INDEX uq_warehouse_active_in_transit
          ON warehouse_stock_locations (warehouse_id)
          WHERE custody = 'in_transit' AND is_active;

        ALTER TABLE stock_movements
          DROP CONSTRAINT ck_stock_movements_type;
        ALTER TABLE stock_movements
          DROP CONSTRAINT ck_stock_movements_leg;
        ALTER TABLE stock_movements
          ADD CONSTRAINT ck_stock_movements_type CHECK (
            movement_type IN ('opening_stock','pick','pick_reversal','dispatch')
          );
        ALTER TABLE stock_movements
          ADD CONSTRAINT ck_stock_movements_leg CHECK (
            (movement_type = 'opening_stock' AND movement_leg = 'opening_in')
            OR (movement_type = 'pick'
                AND movement_leg IN ('pick_available_out','pick_staging_in'))
            OR (movement_type = 'pick_reversal'
                AND movement_leg IN ('pick_reversal_staging_out',
                                     'pick_reversal_available_in'))
            OR (movement_type = 'dispatch'
                AND movement_leg IN ('dispatch_staging_out','dispatch_transit_in'))
          );

        ALTER TABLE fulfillment_order_state
          ADD COLUMN dispatched_quantity_base numeric(18,6) NOT NULL DEFAULT 0;
        ALTER TABLE fulfillment_order_state
          DROP CONSTRAINT ck_fulfillment_order_state_status;
        ALTER TABLE fulfillment_order_state
          ADD CONSTRAINT ck_fulfillment_order_state_status CHECK (
            status IN ('reserved','payment_ready','pick_released','partially_picked',
                       'picked','partially_dispatched','dispatched','payment_hold','cancelled')
          );
        ALTER TABLE fulfillment_order_state
          DROP CONSTRAINT ck_fulfillment_order_state_amounts;
        ALTER TABLE fulfillment_order_state
          ADD CONSTRAINT ck_fulfillment_order_state_amounts CHECK (
            reserved_quantity_base >= 0 AND backorder_quantity_base >= 0
            AND covered_amount >= 0 AND picked_quantity_base >= 0
            AND dispatched_quantity_base >= 0
            AND dispatched_quantity_base <= picked_quantity_base
          );

        CREATE TABLE delivery_dispatches (
          delivery_id uuid PRIMARY KEY,
          fulfillment_order_id uuid NOT NULL
            REFERENCES fulfillment_orders(fulfillment_order_id),
          sales_order_id uuid NOT NULL,
          sales_order_revision_id uuid NOT NULL,
          customer_id uuid NOT NULL REFERENCES customer_accounts(customer_id),
          branch_id uuid NOT NULL REFERENCES branches(branch_id),
          warehouse_id uuid NOT NULL REFERENCES warehouses(warehouse_id),
          delivery_address_version_id uuid NOT NULL
            REFERENCES customer_address_versions(address_version_id),
          delivery_address_snapshot jsonb NOT NULL,
          recipient_name_snapshot varchar(300) NOT NULL,
          payment_timing_policy varchar(30) NOT NULL,
          evidence_requirements jsonb NOT NULL,
          initial_assignee_subject varchar(200) NOT NULL REFERENCES users(subject),
          dispatched_by varchar(200) NOT NULL REFERENCES users(subject),
          correlation_id varchar(100) NOT NULL,
          idempotency_key varchar(200) NOT NULL,
          dispatched_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_delivery_dispatches_payment_timing CHECK (
            payment_timing_policy IN ('prepaid','cash_on_delivery','on_account')
          ),
          CONSTRAINT uq_delivery_dispatch_actor_idempotency
            UNIQUE (dispatched_by,idempotency_key)
        );

        CREATE TABLE delivery_state (
          delivery_id uuid PRIMARY KEY REFERENCES delivery_dispatches(delivery_id),
          status varchar(30) NOT NULL,
          assigned_to varchar(200) NOT NULL REFERENCES users(subject),
          version integer NOT NULL DEFAULT 1,
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_delivery_state_status CHECK (status IN ('dispatched')),
          CONSTRAINT ck_delivery_state_version CHECK (version > 0)
        );

        CREATE TABLE delivery_assignment_events (
          delivery_assignment_event_id uuid PRIMARY KEY,
          delivery_id uuid NOT NULL REFERENCES delivery_dispatches(delivery_id),
          previous_assignee_subject varchar(200) NOT NULL REFERENCES users(subject),
          assigned_to varchar(200) NOT NULL REFERENCES users(subject),
          delivery_version integer NOT NULL,
          reason varchar(500) NOT NULL,
          assigned_by varchar(200) NOT NULL REFERENCES users(subject),
          correlation_id varchar(100) NOT NULL,
          idempotency_key varchar(200) NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_delivery_assignment_events_change
            CHECK (previous_assignee_subject <> assigned_to),
          CONSTRAINT ck_delivery_assignment_events_version CHECK (delivery_version > 1),
          CONSTRAINT uq_delivery_assignment_actor_idempotency
            UNIQUE (assigned_by,idempotency_key)
        );

        CREATE TABLE delivery_lines (
          delivery_line_id uuid PRIMARY KEY,
          delivery_id uuid NOT NULL REFERENCES delivery_dispatches(delivery_id),
          pick_line_id uuid NOT NULL UNIQUE REFERENCES pick_lines(pick_line_id),
          line_id uuid NOT NULL,
          sku_id uuid NOT NULL REFERENCES skus(sku_id),
          quantity_base numeric(18,6) NOT NULL,
          movement_group_id uuid NOT NULL UNIQUE,
          staging_movement_id uuid NOT NULL UNIQUE REFERENCES stock_movements(movement_id),
          transit_movement_id uuid NOT NULL UNIQUE REFERENCES stock_movements(movement_id),
          CONSTRAINT ck_delivery_lines_quantity CHECK (quantity_base > 0),
          CONSTRAINT uq_delivery_line_pick UNIQUE (delivery_id,pick_line_id)
        );
        """
    for statement in statements.split(";\n"):
        if statement.strip():
            op.execute(statement)
    op.execute(
        """
        CREATE FUNCTION prevent_delivery_dispatch_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'Delivery dispatch ledgers are immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table_name in (
        "delivery_dispatches",
        "delivery_assignment_events",
        "delivery_lines",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_immutable
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION prevent_delivery_dispatch_mutation()
            """
        )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM delivery_dispatches) THEN
            RAISE EXCEPTION
              'Cannot downgrade 0011 while immutable Delivery dispatch data exists';
          END IF;
        END
        $$
        """
    )
    for table_name in (
        "delivery_lines",
        "delivery_assignment_events",
        "delivery_dispatches",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_immutable ON {table_name}")
    op.execute("DROP FUNCTION IF EXISTS prevent_delivery_dispatch_mutation")
    op.execute("DROP TABLE delivery_lines")
    op.execute("DROP TABLE IF EXISTS delivery_assignment_events")
    op.execute("DROP TABLE delivery_state")
    op.execute("DROP TABLE delivery_dispatches")
    op.execute(
        "ALTER TABLE fulfillment_order_state DROP CONSTRAINT ck_fulfillment_order_state_amounts"
    )
    op.execute(
        "ALTER TABLE fulfillment_order_state DROP CONSTRAINT ck_fulfillment_order_state_status"
    )
    op.execute("ALTER TABLE fulfillment_order_state DROP COLUMN dispatched_quantity_base")
    op.execute(
        "ALTER TABLE fulfillment_order_state ADD CONSTRAINT "
        "ck_fulfillment_order_state_status CHECK ("
        "status IN ('reserved','payment_ready','pick_released','partially_picked',"
        "'picked','payment_hold','cancelled'))"
    )
    op.execute(
        "ALTER TABLE fulfillment_order_state ADD CONSTRAINT "
        "ck_fulfillment_order_state_amounts CHECK ("
        "reserved_quantity_base >= 0 AND backorder_quantity_base >= 0 "
        "AND covered_amount >= 0 AND picked_quantity_base >= 0)"
    )
    op.execute("ALTER TABLE stock_movements DROP CONSTRAINT ck_stock_movements_leg")
    op.execute("ALTER TABLE stock_movements DROP CONSTRAINT ck_stock_movements_type")
    op.execute(
        "ALTER TABLE stock_movements ADD CONSTRAINT ck_stock_movements_type "
        "CHECK (movement_type IN ('opening_stock','pick','pick_reversal'))"
    )
    op.execute(
        "ALTER TABLE stock_movements ADD CONSTRAINT ck_stock_movements_leg CHECK ("
        "(movement_type = 'opening_stock' AND movement_leg = 'opening_in') "
        "OR (movement_type = 'pick' "
        "AND movement_leg IN ('pick_available_out','pick_staging_in')) "
        "OR (movement_type = 'pick_reversal' "
        "AND movement_leg IN ('pick_reversal_staging_out',"
        "'pick_reversal_available_in')))"
    )
    op.execute("DROP INDEX uq_warehouse_active_in_transit")
    op.execute(
        "ALTER TABLE warehouse_stock_locations DROP CONSTRAINT ck_warehouse_stock_locations_custody"
    )
    op.execute(
        "ALTER TABLE warehouse_stock_locations ADD CONSTRAINT "
        "ck_warehouse_stock_locations_custody "
        "CHECK (custody IN ('available','quarantine','dispatch_staging'))"
    )
