"""Create pricing masters and immutable Sales Order draft revisions.

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tax_codes",
        sa.Column("tax_code_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(30), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_by", sa.String(200), sa.ForeignKey("users.subject"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "tax_code_versions",
        sa.Column("tax_code_version_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tax_code_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tax_codes.tax_code_id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("rate", sa.Numeric(9, 6), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("created_by", sa.String(200), sa.ForeignKey("users.subject"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("version > 0", name="ck_tax_code_versions_version_positive"),
        sa.CheckConstraint("rate >= 0 AND rate <= 1", name="ck_tax_code_versions_rate"),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_tax_code_versions_effective_range",
        ),
        sa.UniqueConstraint("tax_code_id", "version", name="uq_tax_code_version"),
        sa.UniqueConstraint(
            "tax_code_id",
            "effective_from",
            name="uq_tax_code_version_effective_date",
        ),
    )
    op.create_table(
        "price_lists",
        sa.Column("price_list_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "branch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("branches.branch_id"),
            nullable=False,
        ),
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("customer_accounts.customer_id"),
            nullable=True,
        ),
        sa.Column("code", sa.String(50), nullable=False, unique=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_by", sa.String(200), sa.ForeignKey("users.subject"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "price_list_versions",
        sa.Column("price_list_version_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "price_list_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("price_lists.price_list_id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("inclusion_mode", sa.String(20), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("created_by", sa.String(200), sa.ForeignKey("users.subject"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("version > 0", name="ck_price_list_versions_version_positive"),
        sa.CheckConstraint(
            "inclusion_mode IN ('inclusive', 'exclusive')",
            name="ck_price_list_versions_inclusion_mode",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_price_list_versions_effective_range",
        ),
        sa.UniqueConstraint("price_list_id", "version", name="uq_price_list_version"),
        sa.UniqueConstraint(
            "price_list_id",
            "effective_from",
            name="uq_price_list_version_effective_date",
        ),
    )
    op.create_table(
        "price_list_lines",
        sa.Column("price_list_line_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "price_list_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("price_list_versions.price_list_version_id"),
            nullable=False,
        ),
        sa.Column(
            "sku_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("skus.sku_id"), nullable=False
        ),
        sa.Column("unit_code", sa.String(30), nullable=False),
        sa.Column("list_unit_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("floor_unit_price", sa.Numeric(18, 6), nullable=True),
        sa.Column(
            "tax_code_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tax_code_versions.tax_code_version_id"),
            nullable=False,
        ),
        sa.Column("line_position", sa.Integer(), nullable=False),
        sa.CheckConstraint("list_unit_price >= 0", name="ck_price_list_lines_price_nonnegative"),
        sa.CheckConstraint(
            "floor_unit_price IS NULL OR floor_unit_price >= 0",
            name="ck_price_list_lines_floor_nonnegative",
        ),
        sa.CheckConstraint("line_position > 0", name="ck_price_list_lines_position_positive"),
        sa.UniqueConstraint(
            "price_list_version_id",
            "sku_id",
            "unit_code",
            name="uq_price_list_line_sku_unit",
        ),
    )
    op.create_table(
        "sales_orders",
        sa.Column("sales_order_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "branch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("branches.branch_id"),
            nullable=False,
        ),
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("customer_accounts.customer_id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(200), sa.ForeignKey("users.subject"), nullable=False),
        sa.Column("updated_by", sa.String(200), sa.ForeignKey("users.subject"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("status = 'draft'", name="ck_sales_orders_status"),
        sa.CheckConstraint("version > 0", name="ck_sales_orders_version_positive"),
    )
    op.create_table(
        "sales_order_revisions",
        sa.Column("sales_order_revision_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "sales_order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sales_orders.sales_order_id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "branch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("branches.branch_id"),
            nullable=False,
        ),
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("customer_accounts.customer_id"),
            nullable=False,
        ),
        sa.Column("customer_version", sa.Integer(), nullable=False),
        sa.Column(
            "delivery_address_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("customer_address_versions.address_version_id"),
            nullable=False,
        ),
        sa.Column("delivery_address_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("price_inclusion_mode", sa.String(20), nullable=False),
        sa.Column(
            "price_list_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("price_list_versions.price_list_version_id"),
            nullable=False,
        ),
        sa.Column("price_list_code", sa.String(50), nullable=False),
        sa.Column("price_list_version", sa.Integer(), nullable=False),
        sa.Column("pricing_date", sa.Date(), nullable=False),
        sa.Column("payment_timing_default", sa.String(30), nullable=False),
        sa.Column("payment_timing_policy", sa.String(30), nullable=False),
        sa.Column("payment_timing_override_reason", sa.String(500), nullable=True),
        sa.Column(
            "payment_timing_overridden_by",
            sa.String(200),
            sa.ForeignKey("users.subject"),
            nullable=True,
        ),
        sa.Column("order_discount_amount", sa.Numeric(18, 6), nullable=False),
        sa.Column("subtotal", sa.Numeric(24, 6), nullable=False),
        sa.Column("discount_total", sa.Numeric(24, 6), nullable=False),
        sa.Column("taxable_total", sa.Numeric(24, 6), nullable=False),
        sa.Column("tax_total", sa.Numeric(24, 6), nullable=False),
        sa.Column("grand_total", sa.Numeric(24, 6), nullable=False),
        sa.Column("calculation_contract_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("actor_subject", sa.String(200), sa.ForeignKey("users.subject"), nullable=False),
        sa.Column("correlation_id", sa.String(100), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False, unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("version > 0", name="ck_sales_order_revisions_version_positive"),
        sa.CheckConstraint(
            "price_inclusion_mode IN ('inclusive', 'exclusive')",
            name="ck_sales_order_revisions_inclusion_mode",
        ),
        sa.CheckConstraint(
            "payment_timing_default IN ('prepaid', 'cash_on_delivery', 'on_account')",
            name="ck_sales_order_revisions_payment_default",
        ),
        sa.CheckConstraint(
            "payment_timing_policy IN ('prepaid', 'cash_on_delivery', 'on_account')",
            name="ck_sales_order_revisions_payment_policy",
        ),
        sa.CheckConstraint(
            "order_discount_amount >= 0 AND discount_total >= 0",
            name="ck_sales_order_revisions_discount_nonnegative",
        ),
        sa.CheckConstraint(
            "subtotal >= 0 AND taxable_total >= 0 AND tax_total >= 0 AND grand_total >= 0",
            name="ck_sales_order_revisions_totals_nonnegative",
        ),
        sa.UniqueConstraint("sales_order_id", "version", name="uq_sales_order_revision"),
    )
    op.create_table(
        "sales_order_line_revisions",
        sa.Column("sales_order_line_revision_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "sales_order_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sales_order_revisions.sales_order_revision_id"),
            nullable=False,
        ),
        sa.Column("line_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("line_position", sa.Integer(), nullable=False),
        sa.Column(
            "sku_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("skus.sku_id"), nullable=False
        ),
        sa.Column("sku_code", sa.String(50), nullable=False),
        sa.Column("sku_name", sa.String(200), nullable=False),
        sa.Column("entered_quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("entered_unit", sa.String(30), nullable=False),
        sa.Column("quantity_base", sa.Numeric(18, 6), nullable=False),
        sa.Column("conversion_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column(
            "price_list_line_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("price_list_lines.price_list_line_id"),
            nullable=False,
        ),
        sa.Column("list_unit_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("floor_unit_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("manual_override_unit_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("price_override_reason", sa.String(500), nullable=True),
        sa.Column("effective_unit_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("price_source", sa.String(20), nullable=False),
        sa.Column("below_floor", sa.Boolean(), nullable=False),
        sa.Column("allocated_discount", sa.Numeric(24, 6), nullable=False),
        sa.Column("tax_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("calculation_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("taxable_amount", sa.Numeric(24, 6), nullable=False),
        sa.Column("tax_amount", sa.Numeric(24, 6), nullable=False),
        sa.Column("line_total", sa.Numeric(24, 6), nullable=False),
        sa.CheckConstraint("line_position > 0", name="ck_sales_order_line_revisions_position"),
        sa.CheckConstraint(
            "entered_quantity > 0 AND quantity_base > 0",
            name="ck_sales_order_line_revisions_quantity",
        ),
        sa.CheckConstraint(
            "list_unit_price >= 0 AND effective_unit_price >= 0",
            name="ck_sales_order_line_revisions_price",
        ),
        sa.CheckConstraint(
            "price_source IN ('customer', 'branch')",
            name="ck_sales_order_line_revisions_price_source",
        ),
        sa.CheckConstraint(
            "allocated_discount >= 0 AND taxable_amount >= 0 AND tax_amount >= 0"
            " AND line_total >= 0",
            name="ck_sales_order_line_revisions_amounts",
        ),
        sa.UniqueConstraint(
            "sales_order_revision_id",
            "line_id",
            name="uq_sales_order_line_revision_identity",
        ),
        sa.UniqueConstraint(
            "sales_order_revision_id",
            "line_position",
            name="uq_sales_order_line_revision_position",
        ),
    )
    _create_effective_date_guards()
    _create_immutable_revision_guards()


def _create_effective_date_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION prevent_overlapping_tax_code_versions() RETURNS trigger AS $$
        BEGIN
          PERFORM pg_advisory_xact_lock(hashtext('tax:' || NEW.tax_code_id::text));
          IF EXISTS (
            SELECT 1 FROM tax_code_versions existing
            WHERE existing.tax_code_id = NEW.tax_code_id
              AND existing.tax_code_version_id <> NEW.tax_code_version_id
              AND daterange(
                existing.effective_from,
                COALESCE(existing.effective_to, 'infinity'::date),
                '[]'
              ) && daterange(
                NEW.effective_from,
                COALESCE(NEW.effective_to, 'infinity'::date),
                '[]'
              )
          ) THEN
            RAISE EXCEPTION 'Tax Code effective periods overlap'
              USING ERRCODE = '23P01',
                    CONSTRAINT = 'ex_tax_code_version_effective_period';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_tax_code_version_no_overlap
        BEFORE INSERT ON tax_code_versions
        FOR EACH ROW EXECUTE FUNCTION prevent_overlapping_tax_code_versions()
        """
    )
    op.execute(
        """
        CREATE FUNCTION prevent_overlapping_price_list_assignments() RETURNS trigger AS $$
        DECLARE
          assignment_branch uuid;
          assignment_customer uuid;
        BEGIN
          SELECT branch_id, customer_id
            INTO assignment_branch, assignment_customer
            FROM price_lists
            WHERE price_list_id = NEW.price_list_id;
          PERFORM pg_advisory_xact_lock(
            hashtext(
              'price-list:' || assignment_branch::text || ':' ||
              COALESCE(assignment_customer::text, 'branch-default')
            )
          );
          IF EXISTS (
            SELECT 1
            FROM price_list_versions existing_version
            JOIN price_lists existing_list
              ON existing_list.price_list_id = existing_version.price_list_id
            WHERE existing_list.is_active
              AND existing_list.branch_id = assignment_branch
              AND existing_list.customer_id IS NOT DISTINCT FROM assignment_customer
              AND existing_version.price_list_version_id <> NEW.price_list_version_id
              AND daterange(
                existing_version.effective_from,
                COALESCE(existing_version.effective_to, 'infinity'::date),
                '[]'
              ) && daterange(
                NEW.effective_from,
                COALESCE(NEW.effective_to, 'infinity'::date),
                '[]'
              )
          ) THEN
            RAISE EXCEPTION 'Price List assignments overlap'
              USING ERRCODE = '23P01',
                    CONSTRAINT = 'ex_price_list_assignment_effective_period';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_price_list_assignment_no_overlap
        BEFORE INSERT ON price_list_versions
        FOR EACH ROW EXECUTE FUNCTION prevent_overlapping_price_list_assignments()
        """
    )


def _create_immutable_revision_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION prevent_sales_snapshot_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'Sales pricing and order revision snapshots are immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table_name in (
        "tax_code_versions",
        "price_list_versions",
        "price_list_lines",
        "sales_order_revisions",
        "sales_order_line_revisions",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_immutable
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION prevent_sales_snapshot_mutation()
            """
        )


def downgrade() -> None:
    for table_name in (
        "sales_order_line_revisions",
        "sales_order_revisions",
        "price_list_lines",
        "price_list_versions",
        "tax_code_versions",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_immutable ON {table_name}")
    op.execute("DROP FUNCTION IF EXISTS prevent_sales_snapshot_mutation")
    op.execute("DROP TRIGGER IF EXISTS trg_price_list_assignment_no_overlap ON price_list_versions")
    op.execute("DROP FUNCTION IF EXISTS prevent_overlapping_price_list_assignments")
    op.execute("DROP TRIGGER IF EXISTS trg_tax_code_version_no_overlap ON tax_code_versions")
    op.execute("DROP FUNCTION IF EXISTS prevent_overlapping_tax_code_versions")
    op.drop_table("sales_order_line_revisions")
    op.drop_table("sales_order_revisions")
    op.drop_table("sales_orders")
    op.drop_table("price_list_lines")
    op.drop_table("price_list_versions")
    op.drop_table("price_lists")
    op.drop_table("tax_code_versions")
    op.drop_table("tax_codes")
