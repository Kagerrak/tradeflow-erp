"""sales_order_cancellation

Revision ID: d62caac1e324
Revises: e8b78e1dfcfc
Create Date: 2026-08-20 10:56:13.773793
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d62caac1e324"
down_revision: str | None = "e8b78e1dfcfc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    statements = """
      ALTER TABLE sales_orders
        DROP CONSTRAINT ck_sales_orders_status;
      ALTER TABLE sales_orders
        ADD CONSTRAINT ck_sales_orders_status CHECK (
          status IN ('draft', 'approved', 'held', 'partially_cancelled', 'cancelled')
        );

      ALTER TABLE sales_order_line_commitments
        ADD COLUMN cancelled_quantity_base numeric(18,6) NOT NULL DEFAULT 0;
      ALTER TABLE sales_order_line_commitments
        DROP CONSTRAINT ck_sales_order_line_commitments_quantities;
      ALTER TABLE sales_order_line_commitments
        ADD CONSTRAINT ck_sales_order_line_commitments_quantities CHECK (
          ordered_quantity_base > 0
          AND reserved_quantity_base >= 0
          AND picked_quantity_base >= 0
          AND backorder_quantity_base >= 0
          AND cancelled_quantity_base >= 0
          AND reserved_quantity_base + picked_quantity_base
              + backorder_quantity_base + cancelled_quantity_base
              = ordered_quantity_base
        );
      ALTER TABLE sales_order_line_commitments
        ALTER COLUMN cancelled_quantity_base DROP DEFAULT;

      CREATE TABLE sales_order_cancellations (
        cancellation_id uuid PRIMARY KEY,
        sales_order_id uuid NOT NULL REFERENCES sales_orders(sales_order_id),
        reason varchar(500) NOT NULL,
        cancelled_by varchar(200) NOT NULL REFERENCES users(subject),
        correlation_id varchar(100) NOT NULL,
        idempotency_key varchar(200) NOT NULL,
        cancelled_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT ck_sales_order_cancellation_reason CHECK (btrim(reason) <> ''),
        CONSTRAINT uq_sales_order_cancellation_key UNIQUE(idempotency_key)
      );
      CREATE INDEX ix_sales_order_cancellations_order
        ON sales_order_cancellations(sales_order_id, cancelled_at);

      CREATE TABLE sales_order_cancellation_lines (
        cancellation_line_id uuid PRIMARY KEY,
        cancellation_id uuid NOT NULL
          REFERENCES sales_order_cancellations(cancellation_id),
        line_id uuid NOT NULL,
        sku_id uuid NOT NULL REFERENCES skus(sku_id),
        cancelled_quantity_base numeric(18,6) NOT NULL,
        reserved_released_quantity_base numeric(18,6) NOT NULL,
        backorder_reduced_quantity_base numeric(18,6) NOT NULL,
        line_total_delta numeric(24,6) NOT NULL,
        CONSTRAINT ck_sales_order_cancellation_line_quantity CHECK (
          cancelled_quantity_base > 0
          AND reserved_released_quantity_base >= 0
          AND backorder_reduced_quantity_base >= 0
          AND reserved_released_quantity_base + backorder_reduced_quantity_base
              = cancelled_quantity_base
        ),
        CONSTRAINT ck_sales_order_cancellation_line_value CHECK (line_total_delta >= 0)
      );
      CREATE INDEX ix_sales_order_cancellation_lines_order
        ON sales_order_cancellation_lines(cancellation_id, line_id);
    """
    op.execute(statements)


def downgrade() -> None:
    statements = """
      DO $$
      BEGIN
        IF EXISTS (SELECT 1 FROM sales_order_cancellations) THEN
          RAISE EXCEPTION
            'Cannot downgrade d62caac1e324 while cancellation history exists';
        END IF;
      END $$;

      DROP TABLE IF EXISTS sales_order_cancellation_lines;
      DROP TABLE IF EXISTS sales_order_cancellations;

      ALTER TABLE sales_order_line_commitments
        DROP CONSTRAINT ck_sales_order_line_commitments_quantities;
      ALTER TABLE sales_order_line_commitments
        DROP COLUMN cancelled_quantity_base;
      ALTER TABLE sales_order_line_commitments
        ADD CONSTRAINT ck_sales_order_line_commitments_quantities CHECK (
          ordered_quantity_base > 0
          AND reserved_quantity_base >= 0
          AND picked_quantity_base >= 0
          AND backorder_quantity_base >= 0
          AND reserved_quantity_base + picked_quantity_base + backorder_quantity_base
              = ordered_quantity_base
        );

      ALTER TABLE sales_orders
        DROP CONSTRAINT ck_sales_orders_status;
      ALTER TABLE sales_orders
        ADD CONSTRAINT ck_sales_orders_status CHECK (
          status IN ('draft', 'approved', 'held')
        );
    """
    op.execute(statements)
