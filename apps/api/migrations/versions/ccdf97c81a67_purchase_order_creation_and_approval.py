"""purchase order creation and approval

Revision ID: ccdf97c81a67
Revises: 79a7b271a628
Create Date: 2026-08-13 12:23:55.089435
"""

from collections.abc import Sequence

from alembic import op

revision: str = "ccdf97c81a67"
down_revision: str | None = "79a7b271a628"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE purchase_orders (
          purchase_order_id UUID PRIMARY KEY,
          company_id UUID NOT NULL REFERENCES companies(company_id),
          supplier_id UUID NOT NULL REFERENCES suppliers(supplier_id),
          branch_id UUID NOT NULL REFERENCES branches(branch_id),
          code VARCHAR(50) NOT NULL,
          currency VARCHAR(3) NOT NULL,
          exchange_rate NUMERIC(18, 6) NOT NULL DEFAULT 1,
          status VARCHAR(30) NOT NULL DEFAULT 'draft',
          version INTEGER NOT NULL DEFAULT 1,
          created_by VARCHAR(200) NOT NULL REFERENCES users(subject),
          created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
          updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
          CONSTRAINT ck_purchase_orders_status CHECK (
            status IN ('draft', 'approved', 'partially_received', 'received', 'closed')
          ),
          CONSTRAINT ck_purchase_orders_version_positive CHECK (version > 0),
          CONSTRAINT ck_purchase_orders_exchange_rate_positive CHECK (exchange_rate > 0),
          CONSTRAINT uq_purchase_orders_company_code UNIQUE (company_id, code)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX idx_purchase_orders_status ON purchase_orders(status)
        """
    )
    op.execute(
        """
        CREATE INDEX idx_purchase_orders_supplier ON purchase_orders(supplier_id)
        """
    )
    op.execute(
        """
        CREATE TABLE purchase_order_lines (
          purchase_order_line_id UUID PRIMARY KEY,
          purchase_order_id UUID NOT NULL REFERENCES purchase_orders(purchase_order_id),
          sku_id UUID NOT NULL REFERENCES skus(sku_id),
          line_number INTEGER NOT NULL,
          requested_quantity NUMERIC(18, 6) NOT NULL,
          unit_code VARCHAR(30) NOT NULL,
          base_quantity NUMERIC(18, 6) NOT NULL,
          unit_cost NUMERIC(18, 6) NOT NULL,
          version INTEGER NOT NULL DEFAULT 1,
          CONSTRAINT ck_purchase_order_lines_line_number_positive
            CHECK (line_number > 0),
          CONSTRAINT ck_purchase_order_lines_requested_quantity_positive
            CHECK (requested_quantity > 0),
          CONSTRAINT ck_purchase_order_lines_base_quantity_positive
            CHECK (base_quantity > 0),
          CONSTRAINT ck_purchase_order_lines_unit_cost_positive
            CHECK (unit_cost >= 0),
          CONSTRAINT ck_purchase_order_lines_version_positive CHECK (version > 0),
          CONSTRAINT uq_purchase_order_lines_order_line UNIQUE (purchase_order_id, line_number)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX idx_purchase_order_lines_sku ON purchase_order_lines(sku_id)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_purchase_order_lines_sku")
    op.execute("DROP TABLE IF EXISTS purchase_order_lines")
    op.execute("DROP INDEX IF EXISTS idx_purchase_orders_supplier")
    op.execute("DROP INDEX IF EXISTS idx_purchase_orders_status")
    op.execute("DROP TABLE IF EXISTS purchase_orders")
