"""Create commercial approval, credit exposure, and reservation records.

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_sales_orders_status", "sales_orders", type_="check")
    op.add_column(
        "sales_orders",
        sa.Column(
            "approved_revision_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "sales_orders",
        sa.Column(
            "fulfillment_warehouse_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "sales_orders",
        sa.Column("notes", sa.String(2000), nullable=True),
    )
    op.add_column(
        "sales_orders",
        sa.Column("delivery_instructions", sa.String(2000), nullable=True),
    )
    op.add_column(
        "sales_orders",
        sa.Column("metadata_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_check_constraint(
        "ck_sales_orders_metadata_version_positive",
        "sales_orders",
        "metadata_version > 0",
    )
    op.create_unique_constraint(
        "uq_sales_order_revision_ownership",
        "sales_order_revisions",
        ["sales_order_id", "sales_order_revision_id"],
    )
    op.create_unique_constraint(
        "uq_sales_order_revision_customer_ownership",
        "sales_order_revisions",
        ["sales_order_id", "sales_order_revision_id", "customer_id"],
    )
    op.create_unique_constraint(
        "uq_sales_order_line_revision_sku_ownership",
        "sales_order_line_revisions",
        ["sales_order_revision_id", "line_id", "sku_id"],
    )
    op.create_foreign_key(
        "fk_sales_orders_approved_revision",
        "sales_orders",
        "sales_order_revisions",
        ["sales_order_id", "approved_revision_id"],
        ["sales_order_id", "sales_order_revision_id"],
    )
    op.create_foreign_key(
        "fk_sales_orders_fulfillment_warehouse",
        "sales_orders",
        "warehouses",
        ["fulfillment_warehouse_id"],
        ["warehouse_id"],
    )
    op.create_check_constraint(
        "ck_sales_orders_status",
        "sales_orders",
        "status IN ('draft', 'approved', 'held')",
    )

    op.create_table(
        "commercial_approvals",
        sa.Column(
            "commercial_approval_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "sales_order_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "sales_order_revision_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "warehouse_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("warehouses.warehouse_id"),
            nullable=False,
        ),
        sa.Column(
            "maker_subject",
            sa.String(200),
            sa.ForeignKey("users.subject"),
            nullable=False,
        ),
        sa.Column(
            "approved_by",
            sa.String(200),
            sa.ForeignKey("users.subject"),
            nullable=False,
        ),
        sa.Column("payment_timing_policy", sa.String(30), nullable=False),
        sa.Column("order_total", sa.Numeric(24, 6), nullable=False),
        sa.Column("open_balance_snapshot", sa.Numeric(24, 6), nullable=False),
        sa.Column(
            "approved_uninvoiced_snapshot",
            sa.Numeric(24, 6),
            nullable=False,
        ),
        sa.Column("credit_limit_snapshot", sa.Numeric(24, 6), nullable=True),
        sa.Column("credit_excess_approved", sa.Numeric(24, 6), nullable=False),
        sa.Column("correlation_id", sa.String(100), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "payment_timing_policy IN ('prepaid', 'cash_on_delivery', 'on_account')",
            name="ck_commercial_approvals_payment_timing",
        ),
        sa.CheckConstraint(
            "order_total >= 0 AND open_balance_snapshot >= 0 "
            "AND approved_uninvoiced_snapshot >= 0 "
            "AND credit_excess_approved >= 0",
            name="ck_commercial_approvals_amounts",
        ),
        sa.UniqueConstraint(
            "sales_order_revision_id",
            name="uq_commercial_approvals_revision",
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_commercial_approvals_idempotency",
        ),
        sa.ForeignKeyConstraint(
            ["sales_order_id", "sales_order_revision_id", "customer_id"],
            [
                "sales_order_revisions.sales_order_id",
                "sales_order_revisions.sales_order_revision_id",
                "sales_order_revisions.customer_id",
            ],
            name="fk_commercial_approvals_customer_revision_ownership",
        ),
        sa.UniqueConstraint(
            "commercial_approval_id",
            "sales_order_id",
            "customer_id",
            name="uq_commercial_approval_customer_ownership",
        ),
        sa.UniqueConstraint(
            "commercial_approval_id",
            "sales_order_id",
            "sales_order_revision_id",
            "warehouse_id",
            name="uq_commercial_approval_reservation_ownership",
        ),
    )
    op.create_table(
        "commercial_exception_approvals",
        sa.Column(
            "exception_approval_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "commercial_approval_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("commercial_approvals.commercial_approval_id"),
            nullable=False,
        ),
        sa.Column("exception_type", sa.String(30), nullable=False),
        sa.Column(
            "maker_subject",
            sa.String(200),
            sa.ForeignKey("users.subject"),
            nullable=False,
        ),
        sa.Column(
            "approved_by",
            sa.String(200),
            sa.ForeignKey("users.subject"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("exception_amount", sa.Numeric(24, 6), nullable=False),
        sa.Column("exception_percentage", sa.Numeric(9, 6), nullable=True),
        sa.Column("authority_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "exception_type IN ('discount', 'below_floor', 'credit_override')",
            name="ck_commercial_exception_approvals_type",
        ),
        sa.CheckConstraint(
            "exception_amount >= 0",
            name="ck_commercial_exception_approvals_amount",
        ),
        sa.CheckConstraint(
            "exception_percentage IS NULL "
            "OR (exception_percentage >= 0 AND exception_percentage <= 100)",
            name="ck_commercial_exception_approvals_percentage",
        ),
        sa.CheckConstraint(
            "maker_subject <> approved_by",
            name="ck_commercial_exception_approvals_maker_checker",
        ),
        sa.UniqueConstraint(
            "commercial_approval_id",
            "exception_type",
            name="uq_commercial_exception_approval_type",
        ),
    )
    op.create_table(
        "commercial_approval_invalidations",
        sa.Column(
            "invalidation_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "commercial_approval_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("commercial_approvals.commercial_approval_id"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column(
            "invalidated_by",
            sa.String(200),
            sa.ForeignKey("users.subject"),
            nullable=False,
        ),
        sa.Column("correlation_id", sa.String(100), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "commercial_approval_id",
            name="uq_commercial_approval_invalidation",
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_commercial_approval_invalidation_idempotency",
        ),
    )
    op.create_table(
        "customer_credit_exposure",
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("customer_accounts.customer_id"),
            primary_key=True,
        ),
        sa.Column(
            "open_balance",
            sa.Numeric(24, 6),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "approved_uninvoiced",
            sa.Numeric(24, 6),
            nullable=False,
            server_default="0",
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "open_balance >= 0 AND approved_uninvoiced >= 0",
            name="ck_customer_credit_exposure_amounts",
        ),
        sa.CheckConstraint(
            "version > 0",
            name="ck_customer_credit_exposure_version",
        ),
    )
    op.create_table(
        "credit_exposure_entries",
        sa.Column(
            "entry_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("customer_accounts.customer_id"),
            nullable=False,
        ),
        sa.Column(
            "commercial_approval_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("commercial_approvals.commercial_approval_id"),
            nullable=True,
        ),
        sa.Column(
            "sales_order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sales_orders.sales_order_id"),
            nullable=True,
        ),
        sa.Column("component", sa.String(30), nullable=False),
        sa.Column("amount_delta", sa.Numeric(24, 6), nullable=False),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "actor_subject",
            sa.String(200),
            sa.ForeignKey("users.subject"),
            nullable=False,
        ),
        sa.Column("correlation_id", sa.String(100), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "component IN ('posted_open_balance', 'approved_uninvoiced')",
            name="ck_credit_exposure_entries_component",
        ),
        sa.CheckConstraint(
            "amount_delta <> 0",
            name="ck_credit_exposure_entries_amount_nonzero",
        ),
        sa.CheckConstraint(
            "(component <> 'approved_uninvoiced') "
            "OR (commercial_approval_id IS NOT NULL AND sales_order_id IS NOT NULL)",
            name="ck_credit_exposure_entries_order_reference",
        ),
        sa.UniqueConstraint(
            "source_type",
            "source_id",
            "component",
            name="uq_credit_exposure_entry_source_component",
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            "component",
            name="uq_credit_exposure_entry_command_component",
        ),
        sa.ForeignKeyConstraint(
            ["commercial_approval_id", "sales_order_id", "customer_id"],
            [
                "commercial_approvals.commercial_approval_id",
                "commercial_approvals.sales_order_id",
                "commercial_approvals.customer_id",
            ],
            name="fk_credit_exposure_entries_approval_ownership",
        ),
    )
    op.create_table(
        "inventory_reservation_events",
        sa.Column(
            "reservation_event_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "commercial_approval_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("commercial_approvals.commercial_approval_id"),
            nullable=False,
        ),
        sa.Column(
            "sales_order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sales_orders.sales_order_id"),
            nullable=False,
        ),
        sa.Column(
            "sales_order_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sales_order_revisions.sales_order_revision_id"),
            nullable=False,
        ),
        sa.Column("line_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "sku_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("skus.sku_id"),
            nullable=False,
        ),
        sa.Column(
            "warehouse_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("warehouses.warehouse_id"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(20), nullable=False),
        sa.Column("quantity_base", sa.Numeric(18, 6), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column(
            "actor_subject",
            sa.String(200),
            sa.ForeignKey("users.subject"),
            nullable=False,
        ),
        sa.Column("correlation_id", sa.String(100), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "event_type IN ('reserved', 'released')",
            name="ck_inventory_reservation_events_type",
        ),
        sa.CheckConstraint(
            "quantity_base > 0",
            name="ck_inventory_reservation_events_quantity",
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            "line_id",
            "event_type",
            name="uq_inventory_reservation_event_command_line",
        ),
        sa.ForeignKeyConstraint(
            [
                "commercial_approval_id",
                "sales_order_id",
                "sales_order_revision_id",
                "warehouse_id",
            ],
            [
                "commercial_approvals.commercial_approval_id",
                "commercial_approvals.sales_order_id",
                "commercial_approvals.sales_order_revision_id",
                "commercial_approvals.warehouse_id",
            ],
            name="fk_inventory_reservation_events_approval_ownership",
        ),
        sa.ForeignKeyConstraint(
            ["sales_order_revision_id", "line_id", "sku_id"],
            [
                "sales_order_line_revisions.sales_order_revision_id",
                "sales_order_line_revisions.line_id",
                "sales_order_line_revisions.sku_id",
            ],
            name="fk_inventory_reservation_events_line_ownership",
        ),
    )
    op.create_table(
        "inventory_reserved_by_sku_warehouse",
        sa.Column(
            "sku_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("skus.sku_id"),
            primary_key=True,
        ),
        sa.Column(
            "warehouse_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("warehouses.warehouse_id"),
            primary_key=True,
        ),
        sa.Column(
            "reserved_quantity_base",
            sa.Numeric(18, 6),
            nullable=False,
            server_default="0",
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "reserved_quantity_base >= 0",
            name="ck_inventory_reserved_by_sku_warehouse_quantity",
        ),
        sa.CheckConstraint(
            "version > 0",
            name="ck_inventory_reserved_by_sku_warehouse_version",
        ),
    )
    op.create_table(
        "sales_order_line_commitments",
        sa.Column(
            "sales_order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sales_orders.sales_order_id"),
            primary_key=True,
        ),
        sa.Column("line_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "commercial_approval_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("commercial_approvals.commercial_approval_id"),
            nullable=False,
        ),
        sa.Column(
            "sales_order_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sales_order_revisions.sales_order_revision_id"),
            nullable=False,
        ),
        sa.Column(
            "sku_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("skus.sku_id"),
            nullable=False,
        ),
        sa.Column(
            "warehouse_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("warehouses.warehouse_id"),
            nullable=False,
        ),
        sa.Column("ordered_quantity_base", sa.Numeric(18, 6), nullable=False),
        sa.Column("reserved_quantity_base", sa.Numeric(18, 6), nullable=False),
        sa.Column("backorder_quantity_base", sa.Numeric(18, 6), nullable=False),
        sa.CheckConstraint(
            "ordered_quantity_base > 0 AND reserved_quantity_base >= 0 "
            "AND backorder_quantity_base >= 0 "
            "AND reserved_quantity_base + backorder_quantity_base "
            "= ordered_quantity_base",
            name="ck_sales_order_line_commitments_quantities",
        ),
        sa.ForeignKeyConstraint(
            [
                "commercial_approval_id",
                "sales_order_id",
                "sales_order_revision_id",
                "warehouse_id",
            ],
            [
                "commercial_approvals.commercial_approval_id",
                "commercial_approvals.sales_order_id",
                "commercial_approvals.sales_order_revision_id",
                "commercial_approvals.warehouse_id",
            ],
            name="fk_sales_order_line_commitments_approval_ownership",
        ),
        sa.ForeignKeyConstraint(
            ["sales_order_revision_id", "line_id", "sku_id"],
            [
                "sales_order_line_revisions.sales_order_revision_id",
                "sales_order_line_revisions.line_id",
                "sales_order_line_revisions.sku_id",
            ],
            name="fk_sales_order_line_commitments_line_ownership",
        ),
    )
    _create_immutable_ledger_guards()


def _create_immutable_ledger_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION prevent_commercial_ledger_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'Commercial approval and commitment ledgers are immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table_name in (
        "commercial_approvals",
        "commercial_exception_approvals",
        "commercial_approval_invalidations",
        "credit_exposure_entries",
        "inventory_reservation_events",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_immutable
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION prevent_commercial_ledger_mutation()
            """
        )


def downgrade() -> None:
    for table_name in (
        "inventory_reservation_events",
        "credit_exposure_entries",
        "commercial_approval_invalidations",
        "commercial_exception_approvals",
        "commercial_approvals",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_immutable ON {table_name}")
    op.execute("DROP FUNCTION IF EXISTS prevent_commercial_ledger_mutation")

    op.drop_table("sales_order_line_commitments")
    op.drop_table("inventory_reserved_by_sku_warehouse")
    op.drop_table("inventory_reservation_events")
    op.drop_table("credit_exposure_entries")
    op.drop_table("customer_credit_exposure")
    op.drop_table("commercial_approval_invalidations")
    op.drop_table("commercial_exception_approvals")
    op.drop_table("commercial_approvals")

    op.drop_constraint("ck_sales_orders_status", "sales_orders", type_="check")
    op.drop_constraint(
        "fk_sales_orders_fulfillment_warehouse",
        "sales_orders",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_sales_orders_approved_revision",
        "sales_orders",
        type_="foreignkey",
    )
    op.execute(
        "ALTER TABLE sales_order_revisions "
        "DROP CONSTRAINT IF EXISTS uq_sales_order_revision_ownership"
    )
    op.execute(
        "ALTER TABLE sales_order_revisions "
        "DROP CONSTRAINT IF EXISTS uq_sales_order_revision_customer_ownership"
    )
    op.execute(
        "ALTER TABLE sales_order_line_revisions "
        "DROP CONSTRAINT IF EXISTS uq_sales_order_line_revision_sku_ownership"
    )
    op.execute(
        "ALTER TABLE sales_orders "
        "DROP CONSTRAINT IF EXISTS ck_sales_orders_metadata_version_positive"
    )
    op.execute("UPDATE sales_orders SET status = 'draft' WHERE status <> 'draft'")
    op.drop_column("sales_orders", "delivery_instructions")
    op.drop_column("sales_orders", "notes")
    op.drop_column("sales_orders", "fulfillment_warehouse_id")
    op.drop_column("sales_orders", "approved_revision_id")
    op.execute("ALTER TABLE sales_orders DROP COLUMN IF EXISTS metadata_version")
    op.create_check_constraint(
        "ck_sales_orders_status",
        "sales_orders",
        "status = 'draft'",
    )
