"""landed cost allocation to goods receipts

Revision ID: d524a29c32b8
Revises: d53dcaa7ede3
Create Date: 2026-08-13 18:10:07.940475
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d524a29c32b8"
down_revision: str | None = "d53dcaa7ede3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE landed_cost_charges (
          landed_cost_charge_id UUID PRIMARY KEY,
          goods_receipt_id UUID NOT NULL REFERENCES goods_receipts(goods_receipt_id),
          charge_type VARCHAR(50) NOT NULL,
          amount_base NUMERIC(18, 6) NOT NULL,
          base_currency VARCHAR(3) NOT NULL,
          correlation_id VARCHAR(100) NOT NULL,
          idempotency_key VARCHAR(200) NOT NULL UNIQUE,
          created_by VARCHAR(200) NOT NULL REFERENCES users(subject),
          created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
          CONSTRAINT ck_landed_cost_charges_charge_type CHECK (
            charge_type IN ('freight', 'insurance', 'customs', 'brokerage', 'handling')
          ),
          CONSTRAINT ck_landed_cost_charges_amount_positive CHECK (amount_base > 0)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE landed_cost_allocations (
          landed_cost_allocation_id UUID PRIMARY KEY,
          landed_cost_charge_id UUID NOT NULL
            REFERENCES landed_cost_charges(landed_cost_charge_id),
          goods_receipt_line_id UUID NOT NULL
            REFERENCES goods_receipt_lines(goods_receipt_line_id),
          allocated_amount_base NUMERIC(18, 6) NOT NULL,
          CONSTRAINT ck_landed_cost_allocations_amount_positive
            CHECK (allocated_amount_base > 0)
        )
        """
    )

    op.execute(
        """
        CREATE INDEX idx_landed_cost_charges_receipt
          ON landed_cost_charges(goods_receipt_id)
        """
    )

    op.execute(
        """
        CREATE INDEX idx_landed_cost_allocations_charge
          ON landed_cost_allocations(landed_cost_charge_id)
        """
    )

    op.execute(
        """
        CREATE INDEX idx_landed_cost_allocations_line
          ON landed_cost_allocations(goods_receipt_line_id)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_landed_cost_allocations_line")
    op.execute("DROP INDEX IF EXISTS idx_landed_cost_allocations_charge")
    op.execute("DROP INDEX IF EXISTS idx_landed_cost_charges_receipt")
    op.execute("DROP TABLE IF EXISTS landed_cost_allocations")
    op.execute("DROP TABLE IF EXISTS landed_cost_charges")
