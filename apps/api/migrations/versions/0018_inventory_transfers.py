"""Inventory transfers with source cost and in-transit custody.

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0018"
down_revision: str | Sequence[str] | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    statements = """
      ALTER TABLE warehouse_stock_locations
        DROP CONSTRAINT ck_warehouse_stock_locations_custody;
      ALTER TABLE warehouse_stock_locations
        ADD CONSTRAINT ck_warehouse_stock_locations_custody CHECK (
          custody IN ('available','quarantine','dispatch_staging','in_transit',
            'transfer_in_transit','investigation')
        );
      CREATE UNIQUE INDEX uq_warehouse_active_transfer_in_transit
        ON warehouse_stock_locations(warehouse_id)
        WHERE custody = 'transfer_in_transit' AND is_active;
      ALTER TABLE stock_movements DROP CONSTRAINT ck_stock_movements_type;
      ALTER TABLE stock_movements DROP CONSTRAINT ck_stock_movements_leg;
      ALTER TABLE stock_movements ADD CONSTRAINT ck_stock_movements_type CHECK (
        movement_type IN ('opening_stock','pick','pick_reversal','dispatch',
          'delivery_confirmation','delivery_exception','return_to_warehouse',
          'investigation_resolution','delivery_correction','goods_receipt','transfer')
      );
      ALTER TABLE stock_movements ADD CONSTRAINT ck_stock_movements_leg CHECK (
        (movement_type = 'opening_stock' AND movement_leg = 'opening_in')
        OR (movement_type = 'pick' AND movement_leg IN
          ('pick_available_out','pick_staging_in'))
        OR (movement_type = 'pick_reversal' AND movement_leg IN
          ('pick_reversal_staging_out','pick_reversal_available_in'))
        OR (movement_type = 'dispatch' AND movement_leg IN
          ('dispatch_staging_out','dispatch_transit_in'))
        OR (movement_type = 'delivery_confirmation' AND movement_leg = 'delivery_outbound')
        OR (movement_type = 'delivery_exception' AND movement_leg IN
          ('exception_transit_out','exception_investigation_in'))
        OR (movement_type = 'return_to_warehouse' AND movement_leg IN
          ('return_transit_out','return_quarantine_in'))
        OR (movement_type = 'investigation_resolution' AND movement_leg IN
          ('recovery_investigation_out','recovery_quarantine_in',
           'carrier_claim_investigation_out','inventory_adjustment_investigation_out'))
        OR (movement_type = 'delivery_correction' AND movement_leg IN
          ('correction_accepted_reversal_in',
           'correction_exception_reversal_transit_in',
           'correction_exception_reversal_investigation_out',
           'correction_accepted_replacement_out',
           'correction_exception_replacement_transit_out',
           'correction_exception_replacement_investigation_in'))
        OR (movement_type = 'goods_receipt' AND movement_leg = 'goods_receipt_in')
        OR (movement_type = 'transfer' AND movement_leg IN
          ('transfer_source_out','transfer_in_transit_in',
           'transfer_in_transit_out','transfer_destination_in'))
      );

      CREATE TABLE inventory_transfers (
        transfer_id uuid PRIMARY KEY,
        sku_id uuid NOT NULL REFERENCES skus(sku_id),
        from_warehouse_id uuid NOT NULL REFERENCES warehouses(warehouse_id),
        to_warehouse_id uuid NOT NULL REFERENCES warehouses(warehouse_id),
        from_location_id uuid NOT NULL REFERENCES warehouse_stock_locations(location_id),
        to_location_id uuid NOT NULL REFERENCES warehouse_stock_locations(location_id),
        quantity_base numeric(18,6) NOT NULL,
        unit_cost numeric(18,6) NOT NULL,
        base_currency varchar(3) NOT NULL,
        status varchar(20) NOT NULL DEFAULT 'released',
        version integer NOT NULL DEFAULT 1,
        reason varchar(500) NOT NULL,
        source_reference varchar(100) NOT NULL,
        lot_code varchar(100) NULL,
        requested_by varchar(200) NOT NULL REFERENCES users(subject),
        requested_at timestamptz NOT NULL DEFAULT now(),
        received_by varchar(200) NULL REFERENCES users(subject),
        received_at timestamptz NULL,
        release_movement_group_id uuid NOT NULL,
        receive_movement_group_id uuid NULL,
        correlation_id varchar(100) NOT NULL,
        idempotency_key varchar(200) NOT NULL UNIQUE,
        CONSTRAINT ck_inventory_transfers_status CHECK (status IN ('released','received')),
        CONSTRAINT ck_inventory_transfers_version CHECK (version > 0),
        CONSTRAINT ck_inventory_transfers_quantity CHECK (quantity_base > 0),
        CONSTRAINT ck_inventory_transfers_reason CHECK (btrim(reason) <> ''),
        CONSTRAINT ck_inventory_transfers_unit_cost CHECK (unit_cost >= 0),
        CONSTRAINT ck_inventory_transfers_received_shape CHECK (
          (status = 'released' AND received_by IS NULL AND received_at IS NULL
            AND receive_movement_group_id IS NULL)
          OR (status = 'received' AND received_by IS NOT NULL AND received_at IS NOT NULL
            AND receive_movement_group_id IS NOT NULL))
      );
      CREATE INDEX ix_inventory_transfers_sku_from
        ON inventory_transfers(sku_id, from_warehouse_id, status);
      CREATE INDEX ix_inventory_transfers_sku_to
        ON inventory_transfers(sku_id, to_warehouse_id, status);
    """
    for statement in statements.split(";\n"):
        if statement.strip():
            op.execute(statement)

    op.execute(
        """
        CREATE FUNCTION inventory_transfer_group_is_valid(
          target_group_id uuid,
          target_transfer_id uuid,
          target_phase varchar,
          target_sku_id uuid,
          target_from_warehouse_id uuid,
          target_to_warehouse_id uuid,
          target_from_location_id uuid,
          target_to_location_id uuid,
          target_quantity numeric,
          target_unit_cost numeric,
          target_currency varchar
        ) RETURNS boolean LANGUAGE sql STABLE AS $$
          WITH expected AS (
            SELECT round(
              target_quantity * target_unit_cost,
              CASE
                WHEN target_currency IN (
                  'BIF','CLP','DJF','GNF','ISK','JPY','KMF','KRW','PYG','RWF','UGX',
                  'VND','VUV','XAF','XOF','XPF'
                ) THEN 0
                WHEN target_currency IN ('BHD','IQD','JOD','KWD','LYD','OMR','TND') THEN 3
                WHEN target_currency IN ('CLF','UYW') THEN 4
                ELSE 2
              END
            ) AS inventory_value
          ), group_rows AS (
            SELECT movement.*, location.custody
            FROM stock_movements movement
            JOIN warehouse_stock_locations location
              ON location.location_id = movement.location_id
            WHERE movement.movement_group_id = target_group_id
              AND movement.movement_type = 'transfer'
              AND movement.sku_id = target_sku_id
              AND movement.quantity_base = target_quantity
              AND movement.unit_cost = target_unit_cost
              AND movement.base_currency = target_currency
              AND movement.source_reference = 'TRANSFER:' || target_transfer_id
          )
          SELECT (SELECT count(*) FROM stock_movements
                  WHERE movement_group_id = target_group_id) = 2
             AND (SELECT count(*) FROM group_rows) = 2
             AND CASE target_phase
               WHEN 'release' THEN
                 (SELECT count(*) FROM group_rows, expected
                  WHERE movement_leg = 'transfer_source_out'
                    AND warehouse_id = target_from_warehouse_id
                    AND location_id = target_from_location_id
                    AND custody = 'available'
                    AND value_delta = -expected.inventory_value) = 1
                 AND
                 (SELECT count(*) FROM group_rows, expected
                  WHERE movement_leg = 'transfer_in_transit_in'
                    AND warehouse_id = target_from_warehouse_id
                    AND custody = 'transfer_in_transit'
                    AND value_delta = expected.inventory_value) = 1
               WHEN 'receive' THEN
                 (SELECT count(*) FROM group_rows, expected
                  WHERE movement_leg = 'transfer_in_transit_out'
                    AND warehouse_id = target_from_warehouse_id
                    AND custody = 'transfer_in_transit'
                    AND value_delta = -expected.inventory_value) = 1
                 AND
                 (SELECT count(*) FROM group_rows, expected
                  WHERE movement_leg = 'transfer_destination_in'
                    AND warehouse_id = target_to_warehouse_id
                    AND location_id = target_to_location_id
                    AND custody = 'available'
                    AND value_delta = expected.inventory_value) = 1
               ELSE false
             END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_inventory_transfer_mutation() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'INSERT'
             AND NEW.status = 'released'
             AND inventory_transfer_group_is_valid(
               NEW.release_movement_group_id, NEW.transfer_id, 'release', NEW.sku_id,
               NEW.from_warehouse_id, NEW.to_warehouse_id,
               NEW.from_location_id, NEW.to_location_id,
               NEW.quantity_base, NEW.unit_cost, NEW.base_currency
             ) THEN
            RETURN NEW;
          END IF;
          IF TG_OP = 'UPDATE' AND OLD.status = 'released' AND NEW.status = 'received'
             AND OLD.received_by IS NULL AND OLD.received_at IS NULL
             AND OLD.receive_movement_group_id IS NULL
             AND NEW.received_by IS NOT NULL AND NEW.received_at IS NOT NULL
             AND NEW.receive_movement_group_id IS NOT NULL
             AND NEW.version = OLD.version + 1
             AND (to_jsonb(OLD) - 'status' - 'version' - 'received_by' - 'received_at'
                  - 'receive_movement_group_id')
                 = (to_jsonb(NEW) - 'status' - 'version' - 'received_by' - 'received_at'
                    - 'receive_movement_group_id')
             AND inventory_transfer_group_is_valid(
               NEW.release_movement_group_id, NEW.transfer_id, 'release', NEW.sku_id,
               NEW.from_warehouse_id, NEW.to_warehouse_id,
               NEW.from_location_id, NEW.to_location_id,
               NEW.quantity_base, NEW.unit_cost, NEW.base_currency
             )
             AND inventory_transfer_group_is_valid(
               NEW.receive_movement_group_id, NEW.transfer_id, 'receive', NEW.sku_id,
               NEW.from_warehouse_id, NEW.to_warehouse_id,
               NEW.from_location_id, NEW.to_location_id,
               NEW.quantity_base, NEW.unit_cost, NEW.base_currency
             ) THEN
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'Inventory Transfer history is immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """CREATE TRIGGER trg_inventory_transfers_immutable
        BEFORE INSERT OR UPDATE OR DELETE ON inventory_transfers
        FOR EACH ROW EXECUTE FUNCTION reject_inventory_transfer_mutation()"""
    )


def downgrade() -> None:
    op.execute(
        """DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM inventory_transfers) THEN
          RAISE EXCEPTION 'Cannot downgrade 0018 while immutable Inventory Transfer history exists';
        END IF;
        IF EXISTS (SELECT 1 FROM stock_movements WHERE movement_type = 'transfer') THEN
          RAISE EXCEPTION 'Cannot downgrade 0018 while transfer stock_movements rows exist';
        END IF;
        END $$"""
    )
    op.execute("DROP TRIGGER IF EXISTS trg_inventory_transfers_immutable ON inventory_transfers")
    op.execute("DROP FUNCTION IF EXISTS reject_inventory_transfer_mutation()")
    op.execute(
        "DROP FUNCTION IF EXISTS inventory_transfer_group_is_valid("
        "uuid,uuid,varchar,uuid,uuid,uuid,uuid,uuid,numeric,numeric,varchar)"
    )
    op.execute("DROP INDEX IF EXISTS ix_inventory_transfers_sku_to")
    op.execute("DROP INDEX IF EXISTS ix_inventory_transfers_sku_from")
    op.execute("DROP TABLE inventory_transfers")
    op.execute("DROP INDEX IF EXISTS uq_warehouse_active_transfer_in_transit")
    op.execute(
        "ALTER TABLE warehouse_stock_locations DROP CONSTRAINT ck_warehouse_stock_locations_custody"
    )
    op.execute(
        "ALTER TABLE warehouse_stock_locations ADD CONSTRAINT "
        "ck_warehouse_stock_locations_custody CHECK (custody IN "
        "('available','quarantine','dispatch_staging','in_transit','investigation'))"
    )
    op.execute("ALTER TABLE stock_movements DROP CONSTRAINT ck_stock_movements_type")
    op.execute("ALTER TABLE stock_movements DROP CONSTRAINT ck_stock_movements_leg")
    op.execute(
        """ALTER TABLE stock_movements ADD CONSTRAINT ck_stock_movements_type CHECK (
        movement_type IN ('opening_stock','pick','pick_reversal','dispatch',
          'delivery_confirmation','delivery_exception','return_to_warehouse',
          'investigation_resolution','delivery_correction','goods_receipt'))"""
    )
    op.execute(
        """ALTER TABLE stock_movements ADD CONSTRAINT ck_stock_movements_leg CHECK (
        (movement_type = 'opening_stock' AND movement_leg = 'opening_in')
        OR (movement_type = 'pick' AND movement_leg IN
          ('pick_available_out','pick_staging_in'))
        OR (movement_type = 'pick_reversal' AND movement_leg IN
          ('pick_reversal_staging_out','pick_reversal_available_in'))
        OR (movement_type = 'dispatch' AND movement_leg IN
          ('dispatch_staging_out','dispatch_transit_in'))
        OR (movement_type = 'delivery_confirmation' AND movement_leg = 'delivery_outbound')
        OR (movement_type = 'delivery_exception' AND movement_leg IN
          ('exception_transit_out','exception_investigation_in'))
        OR (movement_type = 'return_to_warehouse' AND movement_leg IN
          ('return_transit_out','return_quarantine_in'))
        OR (movement_type = 'investigation_resolution' AND movement_leg IN
          ('recovery_investigation_out','recovery_quarantine_in',
           'carrier_claim_investigation_out','inventory_adjustment_investigation_out'))
        OR (movement_type = 'delivery_correction' AND movement_leg IN
          ('correction_accepted_reversal_in',
           'correction_exception_reversal_transit_in',
           'correction_exception_reversal_investigation_out',
           'correction_accepted_replacement_out',
           'correction_exception_replacement_transit_out',
           'correction_exception_replacement_investigation_in'))
        OR (movement_type = 'goods_receipt' AND movement_leg = 'goods_receipt_in'))"""
    )
