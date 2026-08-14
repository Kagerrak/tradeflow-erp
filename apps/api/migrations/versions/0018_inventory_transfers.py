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
        CREATE FUNCTION reject_inventory_transfer_mutation() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'UPDATE' AND OLD.status = 'released' AND NEW.status = 'received'
             AND OLD.received_by IS NULL AND OLD.received_at IS NULL
             AND OLD.receive_movement_group_id IS NULL
             AND NEW.received_by IS NOT NULL AND NEW.received_at IS NOT NULL
             AND NEW.receive_movement_group_id IS NOT NULL
             AND (to_jsonb(OLD) - 'status' - 'received_by' - 'received_at'
                  - 'receive_movement_group_id')
                 = (to_jsonb(NEW) - 'status' - 'received_by' - 'received_at'
                    - 'receive_movement_group_id') THEN
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'Inventory Transfer history is immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """CREATE TRIGGER trg_inventory_transfers_immutable
        BEFORE UPDATE OR DELETE ON inventory_transfers
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
    op.execute("DROP INDEX IF EXISTS ix_inventory_transfers_sku_to")
    op.execute("DROP INDEX IF EXISTS ix_inventory_transfers_sku_from")
    op.execute("DROP TABLE inventory_transfers")
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
