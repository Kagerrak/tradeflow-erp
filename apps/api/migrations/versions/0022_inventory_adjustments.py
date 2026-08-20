"""Counted-variance inventory adjustments and transfer receipt authorization.

Revision ID: 0022
Revises: 0018, 0021
Create Date: 2026-08-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0022"
down_revision: str | Sequence[str] | None = ("0018", "0021")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    statements = """
      ALTER TABLE stock_movements DROP CONSTRAINT ck_stock_movements_type;
      ALTER TABLE stock_movements DROP CONSTRAINT ck_stock_movements_leg;
      ALTER TABLE stock_movements ADD CONSTRAINT ck_stock_movements_type CHECK (
        movement_type IN ('opening_stock','pick','pick_reversal','dispatch',
          'delivery_confirmation','delivery_exception','return_to_warehouse',
          'investigation_resolution','delivery_correction','goods_receipt','transfer',
          'inventory_adjustment')
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
        OR (movement_type = 'inventory_adjustment' AND movement_leg IN
          ('adjustment_surplus_in','adjustment_shortage_out',
           'adjustment_surplus_reversal_out','adjustment_shortage_reversal_in'))
      );

      CREATE TABLE inventory_transfer_authorizations (
        authorization_id uuid PRIMARY KEY,
        transfer_id uuid NOT NULL UNIQUE REFERENCES inventory_transfers(transfer_id),
        approval_authority_id uuid NOT NULL REFERENCES approval_authorities(approval_authority_id),
        authorized_by varchar(200) NOT NULL REFERENCES users(subject),
        authorized_at timestamptz NOT NULL DEFAULT now(),
        correlation_id varchar(100) NOT NULL,
        idempotency_key varchar(200) NOT NULL UNIQUE
      );

      CREATE TABLE inventory_adjustments (
        adjustment_id uuid PRIMARY KEY,
        sku_id uuid NOT NULL REFERENCES skus(sku_id),
        warehouse_id uuid NOT NULL REFERENCES warehouses(warehouse_id),
        location_id uuid NOT NULL REFERENCES warehouse_stock_locations(location_id),
        kind varchar(20) NOT NULL,
        quantity_base numeric(18,6) NOT NULL,
        unit_cost numeric(18,6) NOT NULL,
        value_delta numeric(24,6) NOT NULL,
        base_currency varchar(3) NOT NULL,
        reason varchar(500) NOT NULL,
        source_reference varchar(100) NOT NULL,
        lot_code varchar(100) NULL,
        status varchar(30) NOT NULL DEFAULT 'pending_authorization',
        version integer NOT NULL DEFAULT 1,
        requested_by varchar(200) NOT NULL REFERENCES users(subject),
        requested_at timestamptz NOT NULL DEFAULT now(),
        posted_by varchar(200) NULL REFERENCES users(subject),
        posted_at timestamptz NULL,
        posted_movement_group_id uuid NULL,
        reversed_by varchar(200) NULL REFERENCES users(subject),
        reversed_at timestamptz NULL,
        reversal_reason varchar(500) NULL,
        reversal_movement_group_id uuid NULL,
        correlation_id varchar(100) NOT NULL,
        idempotency_key varchar(200) NOT NULL UNIQUE,
        CONSTRAINT ck_inventory_adjustments_status CHECK (
          status IN ('pending_authorization','posted','reversed')),
        CONSTRAINT ck_inventory_adjustments_version CHECK (version > 0),
        CONSTRAINT ck_inventory_adjustments_quantity CHECK (quantity_base > 0),
        CONSTRAINT ck_inventory_adjustments_kind CHECK (kind IN ('surplus','shortage')),
        CONSTRAINT ck_inventory_adjustments_value_sign CHECK (
          (kind = 'surplus' AND value_delta > 0)
          OR (kind = 'shortage' AND value_delta < 0)),
        CONSTRAINT ck_inventory_adjustments_reason CHECK (btrim(reason) <> ''),
        CONSTRAINT ck_inventory_adjustments_unit_cost CHECK (unit_cost >= 0),
        CONSTRAINT ck_inventory_adjustments_status_shape CHECK (
          (status = 'pending_authorization'
            AND posted_by IS NULL AND posted_at IS NULL
            AND posted_movement_group_id IS NULL
            AND reversed_by IS NULL AND reversed_at IS NULL
            AND reversal_reason IS NULL AND reversal_movement_group_id IS NULL)
          OR (status IN ('posted','reversed')
            AND posted_by IS NOT NULL AND posted_at IS NOT NULL
            AND posted_movement_group_id IS NOT NULL)
          OR (status = 'reversed'
            AND reversed_by IS NOT NULL AND reversed_at IS NOT NULL
            AND reversal_movement_group_id IS NOT NULL))
      );
      CREATE INDEX ix_inventory_adjustments_sku_warehouse
        ON inventory_adjustments(sku_id, warehouse_id, status);
      CREATE INDEX ix_inventory_adjustments_location
        ON inventory_adjustments(warehouse_id, location_id, status);

      CREATE TABLE inventory_adjustment_authorizations (
        authorization_id uuid PRIMARY KEY,
        adjustment_id uuid NOT NULL REFERENCES inventory_adjustments(adjustment_id),
        approval_authority_id uuid NOT NULL REFERENCES approval_authorities(approval_authority_id),
        authorized_by varchar(200) NOT NULL REFERENCES users(subject),
        authorized_at timestamptz NOT NULL DEFAULT now(),
        correlation_id varchar(100) NOT NULL,
        idempotency_key varchar(200) NOT NULL UNIQUE
      );
      CREATE INDEX ix_inventory_adjustment_authorizations_adjustment
        ON inventory_adjustment_authorizations(adjustment_id);
    """
    for statement in statements.split(";\n"):
        if statement.strip():
            op.execute(statement)

    op.execute(
        """
        CREATE FUNCTION inventory_adjustment_group_is_valid(
          target_group_id uuid,
          target_adjustment_id uuid,
          target_phase varchar,
          target_sku_id uuid,
          target_warehouse_id uuid,
          target_location_id uuid,
          target_kind varchar,
          target_quantity numeric,
          target_unit_cost numeric,
          target_currency varchar,
          target_value numeric
        ) RETURNS boolean LANGUAGE sql STABLE AS $$
          SELECT count(*) = 1
          FROM stock_movements
          WHERE movement_group_id = target_group_id
            AND movement_type = 'inventory_adjustment'
            AND sku_id = target_sku_id
            AND warehouse_id = target_warehouse_id
            AND location_id = target_location_id
            AND quantity_base = target_quantity
            AND unit_cost = target_unit_cost
            AND base_currency = target_currency
            AND value_delta = target_value
            AND source_reference = 'ADJUSTMENT:' || target_adjustment_id
            AND movement_leg = CASE target_phase
              WHEN 'post' THEN
                CASE target_kind
                  WHEN 'surplus' THEN 'adjustment_surplus_in'
                  WHEN 'shortage' THEN 'adjustment_shortage_out'
                END
              WHEN 'reverse' THEN
                CASE target_kind
                  WHEN 'surplus' THEN 'adjustment_surplus_reversal_out'
                  WHEN 'shortage' THEN 'adjustment_shortage_reversal_in'
                END
            END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_inventory_adjustment_mutation() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'INSERT'
             AND NEW.status = 'pending_authorization'
             AND NEW.posted_by IS NULL AND NEW.posted_at IS NULL
             AND NEW.posted_movement_group_id IS NULL
             AND NEW.reversed_by IS NULL AND NEW.reversed_at IS NULL
             AND NEW.reversal_reason IS NULL AND NEW.reversal_movement_group_id IS NULL THEN
            RETURN NEW;
          END IF;
          IF TG_OP = 'UPDATE' AND OLD.status = 'pending_authorization' AND NEW.status = 'posted'
             AND NEW.version = OLD.version + 1
             AND NEW.posted_by IS NOT NULL AND NEW.posted_at IS NOT NULL
             AND NEW.posted_movement_group_id IS NOT NULL
             AND (to_jsonb(OLD) - 'status' - 'version' - 'posted_by' - 'posted_at'
                  - 'posted_movement_group_id')
                 = (to_jsonb(NEW) - 'status' - 'version' - 'posted_by' - 'posted_at'
                    - 'posted_movement_group_id')
             AND inventory_adjustment_group_is_valid(
               NEW.posted_movement_group_id, NEW.adjustment_id, 'post',
               NEW.sku_id, NEW.warehouse_id, NEW.location_id,
               NEW.kind, NEW.quantity_base, NEW.unit_cost, NEW.base_currency, NEW.value_delta
             ) THEN
            RETURN NEW;
          END IF;
          IF TG_OP = 'UPDATE' AND OLD.status = 'posted' AND NEW.status = 'reversed'
             AND NEW.version = OLD.version + 1
             AND NEW.reversed_by IS NOT NULL AND NEW.reversed_at IS NOT NULL
             AND NEW.reversal_movement_group_id IS NOT NULL
             AND (to_jsonb(OLD) - 'status' - 'version' - 'reversed_by' - 'reversed_at'
                  - 'reversal_reason' - 'reversal_movement_group_id')
                 = (to_jsonb(NEW) - 'status' - 'version' - 'reversed_by' - 'reversed_at'
                    - 'reversal_reason' - 'reversal_movement_group_id')
             AND inventory_adjustment_group_is_valid(
               NEW.reversal_movement_group_id, NEW.adjustment_id, 'reverse',
               NEW.sku_id, NEW.warehouse_id, NEW.location_id,
               NEW.kind, NEW.quantity_base, NEW.unit_cost, NEW.base_currency, -NEW.value_delta
             ) THEN
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'Inventory Adjustment history is immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """CREATE TRIGGER trg_inventory_adjustments_immutable
        BEFORE INSERT OR UPDATE OR DELETE ON inventory_adjustments
        FOR EACH ROW EXECUTE FUNCTION reject_inventory_adjustment_mutation()"""
    )


def downgrade() -> None:
    op.execute(
        """DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM inventory_adjustments) THEN
          RAISE EXCEPTION 'Cannot downgrade 0022 while immutable '
                          'Inventory Adjustment history exists';
        END IF;
        IF EXISTS (SELECT 1 FROM stock_movements WHERE movement_type = 'inventory_adjustment') THEN
          RAISE EXCEPTION 'Cannot downgrade 0022 while inventory_adjustment '
                          'stock_movements rows exist';
        END IF;
        END $$"""
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_inventory_adjustments_immutable ON inventory_adjustments"
    )
    op.execute("DROP FUNCTION IF EXISTS reject_inventory_adjustment_mutation()")
    op.execute(
        "DROP FUNCTION IF EXISTS inventory_adjustment_group_is_valid("
        "uuid,uuid,varchar,uuid,uuid,uuid,varchar,numeric,numeric,varchar,numeric)"
    )
    op.execute("DROP INDEX IF EXISTS ix_inventory_adjustment_authorizations_adjustment")
    op.execute("DROP TABLE inventory_adjustment_authorizations")
    op.execute("DROP INDEX IF EXISTS ix_inventory_adjustments_location")
    op.execute("DROP INDEX IF EXISTS ix_inventory_adjustments_sku_warehouse")
    op.execute("DROP TABLE inventory_adjustments")
    op.execute("DROP TABLE inventory_transfer_authorizations")
    op.execute("ALTER TABLE stock_movements DROP CONSTRAINT ck_stock_movements_type")
    op.execute("ALTER TABLE stock_movements DROP CONSTRAINT ck_stock_movements_leg")
    op.execute(
        """ALTER TABLE stock_movements ADD CONSTRAINT ck_stock_movements_type CHECK (
        movement_type IN ('opening_stock','pick','pick_reversal','dispatch',
          'delivery_confirmation','delivery_exception','return_to_warehouse',
          'investigation_resolution','delivery_correction','goods_receipt','transfer'))"""
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
        OR (movement_type = 'goods_receipt' AND movement_leg = 'goods_receipt_in')
        OR (movement_type = 'transfer' AND movement_leg IN
          ('transfer_source_out','transfer_in_transit_in',
           'transfer_in_transit_out','transfer_destination_in')))"""
    )
