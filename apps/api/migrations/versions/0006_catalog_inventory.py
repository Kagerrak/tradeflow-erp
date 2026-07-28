"""Create catalog and immutable inventory ledgers.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("product_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(50), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(200), sa.ForeignKey("users.subject"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("version > 0", name="ck_products_version_positive"),
    )
    op.create_table(
        "skus",
        sa.Column("sku_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.product_id"),
            nullable=False,
        ),
        sa.Column("code", sa.String(50), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("base_stocking_unit", sa.String(30), nullable=False),
        sa.Column("tracking_policy", sa.String(20), nullable=False),
        sa.Column("expiration_control", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(200), sa.ForeignKey("users.subject"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "tracking_policy IN ('untracked', 'lot', 'serial')", name="ck_skus_tracking_policy"
        ),
        sa.CheckConstraint(
            "NOT expiration_control OR tracking_policy IN ('lot', 'serial')",
            name="ck_skus_expiration_requires_tracking",
        ),
        sa.CheckConstraint("version > 0", name="ck_skus_version_positive"),
    )
    op.create_table(
        "unit_conversions",
        sa.Column("unit_conversion_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "sku_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("skus.sku_id"), nullable=False
        ),
        sa.Column("unit_code", sa.String(30), nullable=False),
        sa.Column("base_quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(200), sa.ForeignKey("users.subject"), nullable=False),
        sa.CheckConstraint("base_quantity > 0", name="ck_unit_conversions_quantity_positive"),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_unit_conversions_effective_range",
        ),
        sa.UniqueConstraint(
            "sku_id", "unit_code", "effective_from", name="uq_unit_conversion_effective_date"
        ),
    )
    op.create_table(
        "barcode_mappings",
        sa.Column("barcode_mapping_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "sku_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("skus.sku_id"), nullable=False
        ),
        sa.Column(
            "unit_conversion_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("unit_conversions.unit_conversion_id"),
            nullable=True,
        ),
        sa.Column("barcode", sa.String(100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_by", sa.String(200), sa.ForeignKey("users.subject"), nullable=False),
    )
    op.create_index(
        "uq_active_barcode",
        "barcode_mappings",
        ["barcode"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.create_table(
        "warehouse_stock_locations",
        sa.Column("location_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "warehouse_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("warehouses.warehouse_id"),
            nullable=False,
        ),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("custody", sa.String(30), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(200), sa.ForeignKey("users.subject"), nullable=False),
        sa.CheckConstraint(
            "custody IN ('available', 'quarantine')", name="ck_warehouse_stock_locations_custody"
        ),
        sa.UniqueConstraint("warehouse_id", "code", name="uq_warehouse_stock_location_code"),
    )
    op.create_table(
        "stock_movements",
        sa.Column("movement_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "sku_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("skus.sku_id"), nullable=False
        ),
        sa.Column(
            "warehouse_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("warehouses.warehouse_id"),
            nullable=False,
        ),
        sa.Column(
            "location_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("warehouse_stock_locations.location_id"),
            nullable=False,
        ),
        sa.Column("movement_type", sa.String(30), nullable=False),
        sa.Column("quantity_base", sa.Numeric(18, 6), nullable=False),
        sa.Column("unit_cost", sa.Numeric(18, 6), nullable=False),
        sa.Column("value_delta", sa.Numeric(24, 6), nullable=False),
        sa.Column("base_currency", sa.String(3), nullable=False),
        sa.Column("source_reference", sa.String(100), nullable=False),
        sa.Column("entered_unit", sa.String(30), nullable=False),
        sa.Column("conversion_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("actor_subject", sa.String(200), sa.ForeignKey("users.subject"), nullable=False),
        sa.Column("correlation_id", sa.String(100), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False, unique=True),
        sa.Column(
            "posted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("movement_type = 'opening_stock'", name="ck_stock_movements_type"),
        sa.CheckConstraint("quantity_base > 0", name="ck_stock_movements_quantity_positive"),
        sa.CheckConstraint("unit_cost >= 0", name="ck_stock_movements_cost_nonnegative"),
    )
    op.create_table(
        "lot_identities",
        sa.Column("lot_identity_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "sku_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("skus.sku_id"),
            nullable=False,
        ),
        sa.Column("lot_code", sa.String(100), nullable=False),
        sa.Column("expiration_date", sa.Date(), nullable=True),
        sa.UniqueConstraint("sku_id", "lot_code", name="uq_lot_identity"),
    )
    op.create_table(
        "stock_lot_allocations",
        sa.Column("lot_allocation_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "movement_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("stock_movements.movement_id"),
            nullable=False,
        ),
        sa.Column(
            "lot_identity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("lot_identities.lot_identity_id"),
            nullable=False,
        ),
        sa.Column("quantity_base", sa.Numeric(18, 6), nullable=False),
        sa.CheckConstraint("quantity_base > 0", name="ck_stock_lot_allocations_quantity_positive"),
    )
    op.create_table(
        "stock_serial_allocations",
        sa.Column("serial_allocation_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "movement_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("stock_movements.movement_id"),
            nullable=False,
        ),
        sa.Column(
            "sku_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("skus.sku_id"), nullable=False
        ),
        sa.Column("serial_number", sa.String(100), nullable=False, unique=True),
        sa.Column("expiration_date", sa.Date(), nullable=True),
    )
    op.create_table(
        "inventory_availability",
        sa.Column(
            "sku_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("skus.sku_id"), primary_key=True
        ),
        sa.Column(
            "warehouse_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("warehouses.warehouse_id"),
            primary_key=True,
        ),
        sa.Column(
            "location_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("warehouse_stock_locations.location_id"),
            primary_key=True,
        ),
        sa.Column("identity_key", sa.String(200), primary_key=True, server_default=""),
        sa.Column("lot_code", sa.String(100), nullable=True),
        sa.Column(
            "serial_numbers",
            postgresql.JSONB(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("expiration_date", sa.Date(), nullable=True),
        sa.Column("on_hand", sa.Numeric(18, 6), nullable=False),
        sa.Column("reserved", sa.Numeric(18, 6), nullable=False, server_default="0"),
    )
    op.create_table(
        "inventory_valuation",
        sa.Column(
            "sku_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("skus.sku_id"), primary_key=True
        ),
        sa.Column(
            "warehouse_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("warehouses.warehouse_id"),
            primary_key=True,
        ),
        sa.Column("quantity_on_hand", sa.Numeric(18, 6), nullable=False),
        sa.Column("inventory_value", sa.Numeric(24, 6), nullable=False),
        sa.Column("moving_average_unit_cost", sa.Numeric(18, 6), nullable=False),
    )
    op.execute(
        """
        CREATE FUNCTION prevent_sku_base_unit_change() RETURNS trigger AS $$
        BEGIN
          IF NEW.base_stocking_unit <> OLD.base_stocking_unit THEN
            RAISE EXCEPTION 'SKU base stocking unit is immutable';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_sku_base_unit_immutable
        BEFORE UPDATE ON skus
        FOR EACH ROW EXECUTE FUNCTION prevent_sku_base_unit_change()
        """
    )
    op.execute(
        """
        CREATE FUNCTION prevent_overlapping_unit_conversions() RETURNS trigger AS $$
        BEGIN
          PERFORM pg_advisory_xact_lock(
            hashtext(NEW.sku_id::text || ':' || NEW.unit_code)
          );
          IF EXISTS (
            SELECT 1
            FROM unit_conversions existing
            WHERE existing.sku_id = NEW.sku_id
              AND existing.unit_code = NEW.unit_code
              AND existing.unit_conversion_id <> NEW.unit_conversion_id
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
            RAISE EXCEPTION 'Unit Conversion effective periods overlap'
              USING ERRCODE = '23P01',
                    CONSTRAINT = 'ex_unit_conversion_effective_period';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_unit_conversion_no_overlap
        BEFORE INSERT OR UPDATE ON unit_conversions
        FOR EACH ROW EXECUTE FUNCTION prevent_overlapping_unit_conversions()
        """
    )
    op.execute(
        """
        CREATE FUNCTION prevent_inventory_ledger_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'Posted inventory ledger records are immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table_name in (
        "stock_movements",
        "lot_identities",
        "stock_lot_allocations",
        "stock_serial_allocations",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_immutable
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION prevent_inventory_ledger_mutation()
            """
        )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_stock_serial_allocations_immutable ON stock_serial_allocations"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_stock_lot_allocations_immutable ON stock_lot_allocations"
    )
    op.execute("DROP TRIGGER IF EXISTS trg_lot_identities_immutable ON lot_identities")
    op.execute("DROP TRIGGER IF EXISTS trg_stock_movements_immutable ON stock_movements")
    op.execute("DROP FUNCTION IF EXISTS prevent_inventory_ledger_mutation")
    op.execute("DROP TRIGGER IF EXISTS trg_unit_conversion_no_overlap ON unit_conversions")
    op.execute("DROP FUNCTION IF EXISTS prevent_overlapping_unit_conversions")
    op.execute("DROP TRIGGER trg_sku_base_unit_immutable ON skus")
    op.execute("DROP FUNCTION prevent_sku_base_unit_change")
    op.drop_table("inventory_valuation")
    op.drop_table("inventory_availability")
    op.drop_table("stock_serial_allocations")
    op.drop_table("stock_lot_allocations")
    op.drop_table("lot_identities")
    op.drop_table("stock_movements")
    op.drop_table("warehouse_stock_locations")
    op.drop_index("uq_active_barcode", table_name="barcode_mappings")
    op.drop_table("barcode_mappings")
    op.drop_table("unit_conversions")
    op.drop_table("skus")
    op.drop_table("products")
