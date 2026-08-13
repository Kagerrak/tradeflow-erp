"""goods receipt posting against purchase orders

Revision ID: d53dcaa7ede3
Revises: ccdf97c81a67
Create Date: 2026-08-13 14:37:32.736571
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d53dcaa7ede3"
down_revision: str | None = "ccdf97c81a67"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE purchase_order_lines
          ADD COLUMN received_quantity_base NUMERIC(18, 6) NOT NULL DEFAULT 0,
          ADD CONSTRAINT ck_purchase_order_lines_received_quantity_base_nonnegative
            CHECK (received_quantity_base >= 0)
        """
    )

    op.execute(
        """
        CREATE TABLE goods_receipts (
          goods_receipt_id UUID PRIMARY KEY,
          purchase_order_id UUID NOT NULL REFERENCES purchase_orders(purchase_order_id),
          warehouse_id UUID NOT NULL REFERENCES warehouses(warehouse_id),
          location_id UUID NOT NULL REFERENCES warehouse_stock_locations(location_id),
          receipt_number VARCHAR(50) NOT NULL,
          status VARCHAR(30) NOT NULL DEFAULT 'posted',
          correlation_id VARCHAR(100) NOT NULL,
          idempotency_key VARCHAR(200) NOT NULL UNIQUE,
          created_by VARCHAR(200) NOT NULL REFERENCES users(subject),
          created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
          updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
          CONSTRAINT ck_goods_receipts_status CHECK (status IN ('posted', 'reversed')),
          CONSTRAINT uq_goods_receipts_purchase_order_receipt_number
            UNIQUE (purchase_order_id, receipt_number)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE goods_receipt_lines (
          goods_receipt_line_id UUID PRIMARY KEY,
          goods_receipt_id UUID NOT NULL REFERENCES goods_receipts(goods_receipt_id),
          purchase_order_line_id UUID NOT NULL
            REFERENCES purchase_order_lines(purchase_order_line_id),
          received_quantity_base NUMERIC(18, 6) NOT NULL,
          lot_code VARCHAR(100),
          serial_numbers JSONB NOT NULL DEFAULT '[]',
          CONSTRAINT ck_goods_receipt_lines_received_quantity_positive
            CHECK (received_quantity_base > 0)
        )
        """
    )

    op.execute(
        """
        CREATE INDEX idx_goods_receipts_purchase_order ON goods_receipts(purchase_order_id)
        """
    )

    op.execute(
        """
        ALTER TABLE stock_movements
          DROP CONSTRAINT ck_stock_movements_type,
          ADD CONSTRAINT ck_stock_movements_type CHECK (
            movement_type IN (
              'opening_stock', 'pick', 'pick_reversal', 'dispatch',
              'delivery_confirmation', 'delivery_exception', 'return_to_warehouse',
              'investigation_resolution', 'delivery_correction', 'goods_receipt'
            )
          )
        """
    )

    op.execute(
        """
        ALTER TABLE stock_movements
          DROP CONSTRAINT ck_stock_movements_leg,
          ADD CONSTRAINT ck_stock_movements_leg CHECK (
            (movement_type = 'opening_stock' AND movement_leg = 'opening_in')
            OR (movement_type = 'pick'
                AND movement_leg IN ('pick_available_out', 'pick_staging_in'))
            OR (movement_type = 'pick_reversal'
                AND movement_leg IN (
                  'pick_reversal_staging_out', 'pick_reversal_available_in'))
            OR (movement_type = 'dispatch'
                AND movement_leg IN ('dispatch_staging_out', 'dispatch_transit_in'))
            OR (movement_type = 'delivery_confirmation'
                AND movement_leg = 'delivery_outbound')
            OR (movement_type = 'delivery_exception'
                AND movement_leg IN ('exception_transit_out', 'exception_investigation_in'))
            OR (movement_type = 'return_to_warehouse'
                AND movement_leg IN ('return_transit_out', 'return_quarantine_in'))
            OR (movement_type = 'investigation_resolution'
                AND movement_leg IN (
                  'recovery_investigation_out', 'recovery_quarantine_in',
                  'carrier_claim_investigation_out', 'inventory_adjustment_investigation_out'))
            OR (movement_type = 'delivery_correction'
                AND movement_leg IN (
                  'correction_accepted_reversal_in',
                  'correction_exception_reversal_transit_in',
                  'correction_exception_reversal_investigation_out',
                  'correction_accepted_replacement_out',
                  'correction_exception_replacement_transit_out',
                  'correction_exception_replacement_investigation_in'))
            OR (movement_type = 'goods_receipt'
                AND movement_leg = 'goods_receipt_in')
          )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE stock_movements
          DROP CONSTRAINT ck_stock_movements_leg,
          ADD CONSTRAINT ck_stock_movements_leg CHECK (
            (movement_type = 'opening_stock' AND movement_leg = 'opening_in')
            OR (movement_type = 'pick'
                AND movement_leg IN ('pick_available_out', 'pick_staging_in'))
            OR (movement_type = 'pick_reversal'
                AND movement_leg IN (
                  'pick_reversal_staging_out', 'pick_reversal_available_in'))
            OR (movement_type = 'dispatch'
                AND movement_leg IN ('dispatch_staging_out', 'dispatch_transit_in'))
            OR (movement_type = 'delivery_confirmation'
                AND movement_leg = 'delivery_outbound')
            OR (movement_type = 'delivery_exception'
                AND movement_leg IN ('exception_transit_out', 'exception_investigation_in'))
            OR (movement_type = 'return_to_warehouse'
                AND movement_leg IN ('return_transit_out', 'return_quarantine_in'))
            OR (movement_type = 'investigation_resolution'
                AND movement_leg IN (
                  'recovery_investigation_out', 'recovery_quarantine_in',
                  'carrier_claim_investigation_out', 'inventory_adjustment_investigation_out'))
            OR (movement_type = 'delivery_correction'
                AND movement_leg IN (
                  'correction_accepted_reversal_in',
                  'correction_exception_reversal_transit_in',
                  'correction_exception_reversal_investigation_out',
                  'correction_accepted_replacement_out',
                  'correction_exception_replacement_transit_out',
                  'correction_exception_replacement_investigation_in'))
          )
        """
    )

    op.execute(
        """
        ALTER TABLE stock_movements
          DROP CONSTRAINT ck_stock_movements_type,
          ADD CONSTRAINT ck_stock_movements_type CHECK (
            movement_type IN (
              'opening_stock', 'pick', 'pick_reversal', 'dispatch',
              'delivery_confirmation', 'delivery_exception', 'return_to_warehouse',
              'investigation_resolution', 'delivery_correction'
            )
          )
        """
    )

    op.execute("DROP INDEX IF EXISTS idx_goods_receipts_purchase_order")
    op.execute("DROP TABLE IF EXISTS goods_receipt_lines")
    op.execute("DROP TABLE IF EXISTS goods_receipts")

    op.execute(
        """
        ALTER TABLE purchase_order_lines
          DROP CONSTRAINT ck_purchase_order_lines_received_quantity_base_nonnegative,
          DROP COLUMN received_quantity_base
        """
    )
