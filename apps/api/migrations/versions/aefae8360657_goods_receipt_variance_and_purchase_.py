"""goods receipt variance and purchase backorder

Revision ID: aefae8360657
Revises: a55254931268
Create Date: 2026-08-19 20:30:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "aefae8360657"
down_revision: str | None = "a55254931268"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE purchase_order_lines
          ADD COLUMN accepted_quantity_base NUMERIC(18, 6) NOT NULL DEFAULT 0,
          ADD COLUMN backorder_quantity_base NUMERIC(18, 6) NOT NULL DEFAULT 0,
          ADD CONSTRAINT ck_purchase_order_lines_accepted_quantity_base_nonnegative
            CHECK (accepted_quantity_base >= 0),
          ADD CONSTRAINT ck_purchase_order_lines_backorder_quantity_base_not_null
            CHECK (backorder_quantity_base IS NOT NULL)
        """
    )

    op.execute(
        """
        UPDATE purchase_order_lines
           SET accepted_quantity_base = received_quantity_base,
               backorder_quantity_base = base_quantity - received_quantity_base
        """
    )

    op.execute(
        """
        ALTER TABLE goods_receipt_lines
          ADD COLUMN accepted_quantity_base NUMERIC(18, 6) NOT NULL DEFAULT 0,
          ADD COLUMN rejected_quantity_base NUMERIC(18, 6) NOT NULL DEFAULT 0,
          ADD COLUMN damaged_quantity_base NUMERIC(18, 6) NOT NULL DEFAULT 0,
          ADD COLUMN quarantine_quantity_base NUMERIC(18, 6) NOT NULL DEFAULT 0,
          ADD COLUMN variance_reason VARCHAR(200),
          ADD COLUMN approval_authority_id UUID
            REFERENCES approval_authorities(approval_authority_id),
          ADD CONSTRAINT ck_goods_receipt_lines_component_quantity_balance
            CHECK (
              accepted_quantity_base + rejected_quantity_base
              + damaged_quantity_base + quarantine_quantity_base
              = received_quantity_base
            ),
          ADD CONSTRAINT ck_goods_receipt_lines_accepted_quantity_base_nonnegative
            CHECK (accepted_quantity_base >= 0),
          ADD CONSTRAINT ck_goods_receipt_lines_rejected_quantity_base_nonnegative
            CHECK (rejected_quantity_base >= 0),
          ADD CONSTRAINT ck_goods_receipt_lines_damaged_quantity_base_nonnegative
            CHECK (damaged_quantity_base >= 0),
          ADD CONSTRAINT ck_goods_receipt_lines_quarantine_quantity_base_nonnegative
            CHECK (quarantine_quantity_base >= 0)
        """
    )

    op.execute(
        """
        UPDATE goods_receipt_lines
           SET accepted_quantity_base = received_quantity_base
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE goods_receipt_lines
          DROP CONSTRAINT ck_goods_receipt_lines_quarantine_quantity_base_nonnegative,
          DROP CONSTRAINT ck_goods_receipt_lines_damaged_quantity_base_nonnegative,
          DROP CONSTRAINT ck_goods_receipt_lines_rejected_quantity_base_nonnegative,
          DROP CONSTRAINT ck_goods_receipt_lines_accepted_quantity_base_nonnegative,
          DROP CONSTRAINT ck_goods_receipt_lines_component_quantity_balance,
          DROP COLUMN approval_authority_id,
          DROP COLUMN variance_reason,
          DROP COLUMN quarantine_quantity_base,
          DROP COLUMN damaged_quantity_base,
          DROP COLUMN rejected_quantity_base,
          DROP COLUMN accepted_quantity_base
        """
    )

    op.execute(
        """
        ALTER TABLE purchase_order_lines
          DROP CONSTRAINT ck_purchase_order_lines_backorder_quantity_base_not_null,
          DROP CONSTRAINT ck_purchase_order_lines_accepted_quantity_base_nonnegative,
          DROP COLUMN backorder_quantity_base,
          DROP COLUMN accepted_quantity_base
        """
    )
