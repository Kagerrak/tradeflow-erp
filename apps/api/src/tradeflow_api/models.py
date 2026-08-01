from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID

metadata = MetaData()

platform_command_receipts = Table(
    "platform_command_receipts",
    metadata,
    Column("command_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column("idempotency_key", String(200), nullable=False, unique=True),
    Column("actor_subject", String(200), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("response_json", JSONB, nullable=False),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
)

companies = Table(
    "companies",
    metadata,
    Column("company_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column("singleton_key", String(20), nullable=False, unique=True),
    Column("code", String(30), nullable=False, unique=True),
    Column("name", String(200), nullable=False),
    Column("base_currency", String(3), nullable=False),
    Column("version", Integer, nullable=False, server_default="1"),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    CheckConstraint("singleton_key = 'tradeflow'", name="ck_companies_singleton"),
    CheckConstraint("version > 0", name="ck_companies_version_positive"),
)

branches = Table(
    "branches",
    metadata,
    Column("branch_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "company_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("companies.company_id"),
        nullable=False,
    ),
    Column("code", String(30), nullable=False, unique=True),
    Column("name", String(200), nullable=False),
    Column("is_active", Boolean, nullable=False, server_default="true"),
    Column("version", Integer, nullable=False, server_default="1"),
    CheckConstraint("version > 0", name="ck_branches_version_positive"),
)

warehouses = Table(
    "warehouses",
    metadata,
    Column("warehouse_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "branch_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("branches.branch_id"),
        nullable=False,
    ),
    Column("code", String(30), nullable=False, unique=True),
    Column("name", String(200), nullable=False),
    Column("is_active", Boolean, nullable=False, server_default="true"),
    Column("version", Integer, nullable=False, server_default="1"),
    CheckConstraint("version > 0", name="ck_warehouses_version_positive"),
)

capabilities = Table(
    "capabilities",
    metadata,
    Column("code", String(100), primary_key=True),
)

role_templates = Table(
    "role_templates",
    metadata,
    Column("role_template_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column("code", String(50), nullable=False, unique=True),
    Column("name", String(200), nullable=False),
    Column("is_active", Boolean, nullable=False, server_default="true"),
    Column("version", Integer, nullable=False, server_default="1"),
)

role_template_capabilities = Table(
    "role_template_capabilities",
    metadata,
    Column(
        "role_template_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("role_templates.role_template_id"),
        primary_key=True,
    ),
    Column(
        "capability_code",
        String(100),
        ForeignKey("capabilities.code"),
        primary_key=True,
    ),
)

users = Table(
    "users",
    metadata,
    Column("subject", String(200), primary_key=True),
    Column("display_name", String(200), nullable=False),
    Column(
        "is_operations_administrator",
        Boolean,
        nullable=False,
        server_default="false",
    ),
    Column("is_active", Boolean, nullable=False, server_default="true"),
    Column("version", Integer, nullable=False, server_default="1"),
)

user_role_templates = Table(
    "user_role_templates",
    metadata,
    Column(
        "user_subject",
        String(200),
        ForeignKey("users.subject"),
        primary_key=True,
    ),
    Column(
        "role_template_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("role_templates.role_template_id"),
        primary_key=True,
    ),
)

user_branch_scopes = Table(
    "user_branch_scopes",
    metadata,
    Column(
        "user_subject",
        String(200),
        ForeignKey("users.subject"),
        primary_key=True,
    ),
    Column(
        "branch_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("branches.branch_id"),
        primary_key=True,
    ),
)

user_warehouse_scopes = Table(
    "user_warehouse_scopes",
    metadata,
    Column(
        "user_subject",
        String(200),
        ForeignKey("users.subject"),
        primary_key=True,
    ),
    Column(
        "warehouse_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("warehouses.warehouse_id"),
        primary_key=True,
    ),
)

approval_authorities = Table(
    "approval_authorities",
    metadata,
    Column("approval_authority_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "user_subject",
        String(200),
        ForeignKey("users.subject"),
        nullable=False,
    ),
    Column(
        "capability_code",
        String(100),
        ForeignKey("capabilities.code"),
        nullable=False,
    ),
    Column(
        "branch_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("branches.branch_id"),
        nullable=False,
    ),
    Column("maximum_amount", Numeric(18, 2), nullable=True),
    Column("maximum_percentage", Numeric(9, 6), nullable=True),
    Column(
        "maker_checker_required",
        Boolean,
        nullable=False,
        server_default="true",
    ),
    UniqueConstraint(
        "user_subject",
        "capability_code",
        "branch_id",
        name="uq_approval_authority_assignment",
    ),
)

customer_accounts = Table(
    "customer_accounts",
    metadata,
    Column("customer_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "branch_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("branches.branch_id"),
        nullable=False,
    ),
    Column("account_number", String(50), nullable=False, unique=True),
    Column("legal_name", String(200), nullable=False),
    Column("status", String(20), nullable=False),
    Column("payment_terms", String(50), nullable=False),
    Column("payment_timing_policy", String(30), nullable=False),
    Column("credit_limit", Numeric(18, 2), nullable=True),
    Column("credit_hold", Boolean, nullable=False, server_default="false"),
    Column(
        "created_by",
        String(200),
        ForeignKey("users.subject"),
        nullable=False,
    ),
    Column("version", Integer, nullable=False, server_default="1"),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    CheckConstraint(
        "status IN ('active', 'inactive', 'prospect')",
        name="ck_customer_accounts_status",
    ),
    CheckConstraint(
        "payment_timing_policy IN ('prepaid', 'cash_on_delivery', 'on_account')",
        name="ck_customer_accounts_payment_timing",
    ),
    CheckConstraint(
        "credit_limit IS NULL OR credit_limit >= 0",
        name="ck_customer_accounts_credit_limit",
    ),
    CheckConstraint("version > 0", name="ck_customer_accounts_version_positive"),
)

customer_contacts = Table(
    "customer_contacts",
    metadata,
    Column("contact_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "customer_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("customer_accounts.customer_id"),
        nullable=False,
    ),
    Column("name", String(200), nullable=False),
    Column("role", String(100), nullable=False),
    Column("email", String(320), nullable=True),
    Column("phone", String(50), nullable=True),
    Column("is_active", Boolean, nullable=False, server_default="true"),
    Column("version", Integer, nullable=False, server_default="1"),
)

customer_address_versions = Table(
    "customer_address_versions",
    metadata,
    Column("address_version_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "customer_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("customer_accounts.customer_id"),
        nullable=False,
    ),
    Column("address_key", String(50), nullable=False),
    Column("version", Integer, nullable=False),
    Column("kind", String(20), nullable=False),
    Column("line_1", String(200), nullable=False),
    Column("line_2", String(200), nullable=True),
    Column("city", String(100), nullable=False),
    Column("region", String(100), nullable=False),
    Column("postal_code", String(30), nullable=False),
    Column("country_code", String(2), nullable=False),
    Column("is_current", Boolean, nullable=False),
    Column(
        "created_by",
        String(200),
        ForeignKey("users.subject"),
        nullable=False,
    ),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    CheckConstraint(
        "kind IN ('billing', 'delivery')",
        name="ck_customer_address_versions_kind",
    ),
    CheckConstraint(
        "version > 0",
        name="ck_customer_address_versions_version_positive",
    ),
    UniqueConstraint(
        "customer_id",
        "address_key",
        "version",
        name="uq_customer_address_version",
    ),
)

customer_credit_approvals = Table(
    "customer_credit_approvals",
    metadata,
    Column("credit_approval_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "customer_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("customer_accounts.customer_id"),
        nullable=False,
    ),
    Column(
        "approved_by",
        String(200),
        ForeignKey("users.subject"),
        nullable=False,
    ),
    Column(
        "maker_subject",
        String(200),
        ForeignKey("users.subject"),
        nullable=False,
    ),
    Column("approved_limit", Numeric(18, 2), nullable=False),
    Column("reason", String(500), nullable=False),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
)

products = Table(
    "products",
    metadata,
    Column("product_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column("code", String(50), nullable=False, unique=True),
    Column("name", String(200), nullable=False),
    Column("is_active", Boolean, nullable=False, server_default="true"),
    Column("version", Integer, nullable=False, server_default="1"),
    Column("created_by", String(200), ForeignKey("users.subject"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("version > 0", name="ck_products_version_positive"),
)

skus = Table(
    "skus",
    metadata,
    Column("sku_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "product_id", PostgresUUID(as_uuid=True), ForeignKey("products.product_id"), nullable=False
    ),
    Column("code", String(50), nullable=False, unique=True),
    Column("name", String(200), nullable=False),
    Column("base_stocking_unit", String(30), nullable=False),
    Column("tracking_policy", String(20), nullable=False),
    Column("expiration_control", Boolean, nullable=False, server_default="false"),
    Column("is_active", Boolean, nullable=False, server_default="true"),
    Column("version", Integer, nullable=False, server_default="1"),
    Column("created_by", String(200), ForeignKey("users.subject"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "tracking_policy IN ('untracked', 'lot', 'serial')",
        name="ck_skus_tracking_policy",
    ),
    CheckConstraint(
        "NOT expiration_control OR tracking_policy IN ('lot', 'serial')",
        name="ck_skus_expiration_requires_tracking",
    ),
    CheckConstraint("version > 0", name="ck_skus_version_positive"),
)

unit_conversions = Table(
    "unit_conversions",
    metadata,
    Column("unit_conversion_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column("sku_id", PostgresUUID(as_uuid=True), ForeignKey("skus.sku_id"), nullable=False),
    Column("unit_code", String(30), nullable=False),
    Column("base_quantity", Numeric(18, 6), nullable=False),
    Column("effective_from", Date, nullable=False),
    Column("effective_to", Date, nullable=True),
    Column("version", Integer, nullable=False, server_default="1"),
    Column("created_by", String(200), ForeignKey("users.subject"), nullable=False),
    CheckConstraint("base_quantity > 0", name="ck_unit_conversions_quantity_positive"),
    CheckConstraint(
        "effective_to IS NULL OR effective_to >= effective_from",
        name="ck_unit_conversions_effective_range",
    ),
    UniqueConstraint(
        "sku_id",
        "unit_code",
        "effective_from",
        name="uq_unit_conversion_effective_date",
    ),
)

barcode_mappings = Table(
    "barcode_mappings",
    metadata,
    Column("barcode_mapping_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column("sku_id", PostgresUUID(as_uuid=True), ForeignKey("skus.sku_id"), nullable=False),
    Column(
        "unit_conversion_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("unit_conversions.unit_conversion_id"),
        nullable=True,
    ),
    Column("barcode", String(100), nullable=False),
    Column("is_active", Boolean, nullable=False, server_default="true"),
    Column("created_by", String(200), ForeignKey("users.subject"), nullable=False),
)

warehouse_stock_locations = Table(
    "warehouse_stock_locations",
    metadata,
    Column("location_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "warehouse_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("warehouses.warehouse_id"),
        nullable=False,
    ),
    Column("code", String(50), nullable=False),
    Column("name", String(200), nullable=False),
    Column("custody", String(30), nullable=False),
    Column("is_active", Boolean, nullable=False, server_default="true"),
    Column("version", Integer, nullable=False, server_default="1"),
    Column("created_by", String(200), ForeignKey("users.subject"), nullable=False),
    CheckConstraint(
        "custody IN ('available', 'quarantine', 'dispatch_staging', 'in_transit')",
        name="ck_warehouse_stock_locations_custody",
    ),
    UniqueConstraint("warehouse_id", "code", name="uq_warehouse_stock_location_code"),
)

stock_movements = Table(
    "stock_movements",
    metadata,
    Column("movement_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column("sku_id", PostgresUUID(as_uuid=True), ForeignKey("skus.sku_id"), nullable=False),
    Column(
        "warehouse_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("warehouses.warehouse_id"),
        nullable=False,
    ),
    Column(
        "location_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("warehouse_stock_locations.location_id"),
        nullable=False,
    ),
    Column("movement_type", String(30), nullable=False),
    Column("quantity_base", Numeric(18, 6), nullable=False),
    Column("unit_cost", Numeric(18, 6), nullable=False),
    Column("value_delta", Numeric(24, 6), nullable=False),
    Column("base_currency", String(3), nullable=False),
    Column("source_reference", String(100), nullable=False),
    Column("entered_unit", String(30), nullable=False),
    Column("conversion_snapshot", JSONB, nullable=False),
    Column("actor_subject", String(200), ForeignKey("users.subject"), nullable=False),
    Column("correlation_id", String(100), nullable=False),
    Column("idempotency_key", String(200), nullable=False, unique=True),
    Column("posted_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("movement_group_id", PostgresUUID(as_uuid=True), nullable=False),
    Column("movement_leg", String(40), nullable=False),
    Column(
        "reversal_of_movement_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("stock_movements.movement_id"),
        nullable=True,
    ),
    CheckConstraint(
        "movement_type IN ('opening_stock', 'pick', 'pick_reversal', 'dispatch', "
        "'delivery_confirmation')",
        name="ck_stock_movements_type",
    ),
    CheckConstraint(
        "(movement_type = 'opening_stock' AND movement_leg = 'opening_in') "
        "OR (movement_type = 'pick' "
        "AND movement_leg IN ('pick_available_out', 'pick_staging_in')) "
        "OR (movement_type = 'pick_reversal' "
        "AND movement_leg IN "
        "('pick_reversal_staging_out', 'pick_reversal_available_in')) "
        "OR (movement_type = 'dispatch' "
        "AND movement_leg IN ('dispatch_staging_out', 'dispatch_transit_in')) "
        "OR (movement_type = 'delivery_confirmation' "
        "AND movement_leg = 'delivery_outbound')",
        name="ck_stock_movements_leg",
    ),
    CheckConstraint("quantity_base > 0", name="ck_stock_movements_quantity_positive"),
    CheckConstraint("unit_cost >= 0", name="ck_stock_movements_cost_nonnegative"),
    UniqueConstraint(
        "movement_group_id",
        "movement_leg",
        name="uq_stock_movement_group_leg",
    ),
)
Index(
    "uq_stock_movement_reversal",
    stock_movements.c.reversal_of_movement_id,
    unique=True,
    postgresql_where=stock_movements.c.reversal_of_movement_id.is_not(None),
)

lot_identities = Table(
    "lot_identities",
    metadata,
    Column("lot_identity_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column("sku_id", PostgresUUID(as_uuid=True), ForeignKey("skus.sku_id"), nullable=False),
    Column("lot_code", String(100), nullable=False),
    Column("expiration_date", Date, nullable=True),
    UniqueConstraint("sku_id", "lot_code", name="uq_lot_identity"),
)

stock_lot_allocations = Table(
    "stock_lot_allocations",
    metadata,
    Column("lot_allocation_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "movement_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("stock_movements.movement_id"),
        nullable=False,
    ),
    Column(
        "lot_identity_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("lot_identities.lot_identity_id"),
        nullable=False,
    ),
    Column("quantity_base", Numeric(18, 6), nullable=False),
    CheckConstraint("quantity_base > 0", name="ck_stock_lot_allocations_quantity_positive"),
)

stock_serial_allocations = Table(
    "stock_serial_allocations",
    metadata,
    Column("serial_allocation_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "movement_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("stock_movements.movement_id"),
        nullable=False,
    ),
    Column("sku_id", PostgresUUID(as_uuid=True), ForeignKey("skus.sku_id"), nullable=False),
    Column("serial_number", String(100), nullable=False, unique=True),
    Column("expiration_date", Date, nullable=True),
)

inventory_availability = Table(
    "inventory_availability",
    metadata,
    Column("sku_id", PostgresUUID(as_uuid=True), ForeignKey("skus.sku_id"), primary_key=True),
    Column(
        "warehouse_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("warehouses.warehouse_id"),
        primary_key=True,
    ),
    Column(
        "location_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("warehouse_stock_locations.location_id"),
        primary_key=True,
    ),
    Column("identity_key", String(200), primary_key=True, server_default=""),
    Column("lot_code", String(100), nullable=True),
    Column("serial_numbers", JSONB, nullable=False, server_default="[]"),
    Column("expiration_date", Date, nullable=True),
    Column("on_hand", Numeric(18, 6), nullable=False),
    Column("reserved", Numeric(18, 6), nullable=False, server_default="0"),
)

inventory_valuation = Table(
    "inventory_valuation",
    metadata,
    Column("sku_id", PostgresUUID(as_uuid=True), ForeignKey("skus.sku_id"), primary_key=True),
    Column(
        "warehouse_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("warehouses.warehouse_id"),
        primary_key=True,
    ),
    Column("quantity_on_hand", Numeric(18, 6), nullable=False),
    Column("inventory_value", Numeric(24, 6), nullable=False),
    Column("moving_average_unit_cost", Numeric(18, 6), nullable=False),
)

tax_codes = Table(
    "tax_codes",
    metadata,
    Column("tax_code_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column("code", String(30), nullable=False, unique=True),
    Column("name", String(200), nullable=False),
    Column("is_active", Boolean, nullable=False, server_default="true"),
    Column("created_by", String(200), ForeignKey("users.subject"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

tax_code_versions = Table(
    "tax_code_versions",
    metadata,
    Column("tax_code_version_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "tax_code_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("tax_codes.tax_code_id"),
        nullable=False,
    ),
    Column("version", Integer, nullable=False),
    Column("rate", Numeric(9, 6), nullable=False),
    Column("effective_from", Date, nullable=False),
    Column("effective_to", Date, nullable=True),
    Column("created_by", String(200), ForeignKey("users.subject"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("version > 0", name="ck_tax_code_versions_version_positive"),
    CheckConstraint("rate >= 0 AND rate <= 1", name="ck_tax_code_versions_rate"),
    CheckConstraint(
        "effective_to IS NULL OR effective_to >= effective_from",
        name="ck_tax_code_versions_effective_range",
    ),
    UniqueConstraint("tax_code_id", "version", name="uq_tax_code_version"),
    UniqueConstraint(
        "tax_code_id",
        "effective_from",
        name="uq_tax_code_version_effective_date",
    ),
)

price_lists = Table(
    "price_lists",
    metadata,
    Column("price_list_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "branch_id", PostgresUUID(as_uuid=True), ForeignKey("branches.branch_id"), nullable=False
    ),
    Column(
        "customer_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("customer_accounts.customer_id"),
        nullable=True,
    ),
    Column("code", String(50), nullable=False, unique=True),
    Column("is_active", Boolean, nullable=False, server_default="true"),
    Column("created_by", String(200), ForeignKey("users.subject"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

price_list_versions = Table(
    "price_list_versions",
    metadata,
    Column("price_list_version_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "price_list_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("price_lists.price_list_id"),
        nullable=False,
    ),
    Column("version", Integer, nullable=False),
    Column("currency", String(3), nullable=False),
    Column("inclusion_mode", String(20), nullable=False),
    Column("effective_from", Date, nullable=False),
    Column("effective_to", Date, nullable=True),
    Column("created_by", String(200), ForeignKey("users.subject"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("version > 0", name="ck_price_list_versions_version_positive"),
    CheckConstraint(
        "inclusion_mode IN ('inclusive', 'exclusive')",
        name="ck_price_list_versions_inclusion_mode",
    ),
    CheckConstraint(
        "effective_to IS NULL OR effective_to >= effective_from",
        name="ck_price_list_versions_effective_range",
    ),
    UniqueConstraint("price_list_id", "version", name="uq_price_list_version"),
    UniqueConstraint(
        "price_list_id",
        "effective_from",
        name="uq_price_list_version_effective_date",
    ),
)

price_list_lines = Table(
    "price_list_lines",
    metadata,
    Column("price_list_line_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "price_list_version_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("price_list_versions.price_list_version_id"),
        nullable=False,
    ),
    Column("sku_id", PostgresUUID(as_uuid=True), ForeignKey("skus.sku_id"), nullable=False),
    Column("unit_code", String(30), nullable=False),
    Column("list_unit_price", Numeric(18, 6), nullable=False),
    Column("floor_unit_price", Numeric(18, 6), nullable=True),
    Column(
        "tax_code_version_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("tax_code_versions.tax_code_version_id"),
        nullable=False,
    ),
    Column("line_position", Integer, nullable=False),
    CheckConstraint("list_unit_price >= 0", name="ck_price_list_lines_price_nonnegative"),
    CheckConstraint(
        "floor_unit_price IS NULL OR floor_unit_price >= 0",
        name="ck_price_list_lines_floor_nonnegative",
    ),
    CheckConstraint("line_position > 0", name="ck_price_list_lines_position_positive"),
    UniqueConstraint(
        "price_list_version_id",
        "sku_id",
        "unit_code",
        name="uq_price_list_line_sku_unit",
    ),
)

sales_orders = Table(
    "sales_orders",
    metadata,
    Column("sales_order_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "branch_id", PostgresUUID(as_uuid=True), ForeignKey("branches.branch_id"), nullable=False
    ),
    Column(
        "customer_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("customer_accounts.customer_id"),
        nullable=False,
    ),
    Column("status", String(20), nullable=False, server_default="draft"),
    Column(
        "approved_revision_id",
        PostgresUUID(as_uuid=True),
        nullable=True,
    ),
    Column(
        "fulfillment_warehouse_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("warehouses.warehouse_id"),
        nullable=True,
    ),
    Column("notes", String(2000), nullable=True),
    Column("delivery_instructions", String(2000), nullable=True),
    Column("version", Integer, nullable=False, server_default="1"),
    Column("metadata_version", Integer, nullable=False, server_default="1"),
    Column("created_by", String(200), ForeignKey("users.subject"), nullable=False),
    Column("updated_by", String(200), ForeignKey("users.subject"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    ),
    CheckConstraint(
        "status IN ('draft', 'approved', 'held')",
        name="ck_sales_orders_status",
    ),
    CheckConstraint("version > 0", name="ck_sales_orders_version_positive"),
    CheckConstraint(
        "metadata_version > 0",
        name="ck_sales_orders_metadata_version_positive",
    ),
    ForeignKeyConstraint(
        ["sales_order_id", "approved_revision_id"],
        [
            "sales_order_revisions.sales_order_id",
            "sales_order_revisions.sales_order_revision_id",
        ],
        name="fk_sales_orders_approved_revision",
        use_alter=True,
    ),
)

sales_order_revisions = Table(
    "sales_order_revisions",
    metadata,
    Column("sales_order_revision_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "sales_order_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("sales_orders.sales_order_id"),
        nullable=False,
    ),
    Column("version", Integer, nullable=False),
    Column(
        "branch_id", PostgresUUID(as_uuid=True), ForeignKey("branches.branch_id"), nullable=False
    ),
    Column(
        "customer_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("customer_accounts.customer_id"),
        nullable=False,
    ),
    Column(
        "customer_version",
        Integer,
        nullable=False,
    ),
    Column(
        "delivery_address_version_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("customer_address_versions.address_version_id"),
        nullable=False,
    ),
    Column("delivery_address_snapshot", JSONB, nullable=False),
    Column("currency", String(3), nullable=False),
    Column("price_inclusion_mode", String(20), nullable=False),
    Column(
        "price_list_version_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("price_list_versions.price_list_version_id"),
        nullable=False,
    ),
    Column("price_list_code", String(50), nullable=False),
    Column("price_list_version", Integer, nullable=False),
    Column("pricing_date", Date, nullable=False),
    Column("payment_timing_default", String(30), nullable=False),
    Column("payment_timing_policy", String(30), nullable=False),
    Column("payment_timing_override_reason", String(500), nullable=True),
    Column(
        "payment_timing_overridden_by",
        String(200),
        ForeignKey("users.subject"),
        nullable=True,
    ),
    Column("order_discount_amount", Numeric(18, 6), nullable=False),
    Column("subtotal", Numeric(24, 6), nullable=False),
    Column("discount_total", Numeric(24, 6), nullable=False),
    Column("taxable_total", Numeric(24, 6), nullable=False),
    Column("tax_total", Numeric(24, 6), nullable=False),
    Column("grand_total", Numeric(24, 6), nullable=False),
    Column("calculation_contract_version", Integer, nullable=False, server_default="1"),
    Column("actor_subject", String(200), ForeignKey("users.subject"), nullable=False),
    Column("correlation_id", String(100), nullable=False),
    Column("idempotency_key", String(200), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("version > 0", name="ck_sales_order_revisions_version_positive"),
    CheckConstraint(
        "price_inclusion_mode IN ('inclusive', 'exclusive')",
        name="ck_sales_order_revisions_inclusion_mode",
    ),
    CheckConstraint(
        "payment_timing_default IN ('prepaid', 'cash_on_delivery', 'on_account')",
        name="ck_sales_order_revisions_payment_default",
    ),
    CheckConstraint(
        "payment_timing_policy IN ('prepaid', 'cash_on_delivery', 'on_account')",
        name="ck_sales_order_revisions_payment_policy",
    ),
    CheckConstraint(
        "order_discount_amount >= 0 AND discount_total >= 0",
        name="ck_sales_order_revisions_discount_nonnegative",
    ),
    CheckConstraint(
        "subtotal >= 0 AND taxable_total >= 0 AND tax_total >= 0 AND grand_total >= 0",
        name="ck_sales_order_revisions_totals_nonnegative",
    ),
    UniqueConstraint("sales_order_id", "version", name="uq_sales_order_revision"),
    UniqueConstraint(
        "sales_order_id",
        "sales_order_revision_id",
        name="uq_sales_order_revision_ownership",
    ),
    UniqueConstraint(
        "sales_order_id",
        "sales_order_revision_id",
        "customer_id",
        name="uq_sales_order_revision_customer_ownership",
    ),
)

sales_order_line_revisions = Table(
    "sales_order_line_revisions",
    metadata,
    Column("sales_order_line_revision_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "sales_order_revision_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("sales_order_revisions.sales_order_revision_id"),
        nullable=False,
    ),
    Column("line_id", PostgresUUID(as_uuid=True), nullable=False),
    Column("line_position", Integer, nullable=False),
    Column("sku_id", PostgresUUID(as_uuid=True), ForeignKey("skus.sku_id"), nullable=False),
    Column("sku_code", String(50), nullable=False),
    Column("sku_name", String(200), nullable=False),
    Column("entered_quantity", Numeric(18, 6), nullable=False),
    Column("entered_unit", String(30), nullable=False),
    Column("quantity_base", Numeric(18, 6), nullable=False),
    Column("conversion_snapshot", JSONB, nullable=False),
    Column(
        "price_list_line_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("price_list_lines.price_list_line_id"),
        nullable=False,
    ),
    Column("list_unit_price", Numeric(18, 6), nullable=False),
    Column("floor_unit_price", Numeric(18, 6), nullable=True),
    Column("manual_override_unit_price", Numeric(18, 6), nullable=True),
    Column("price_override_reason", String(500), nullable=True),
    Column("effective_unit_price", Numeric(18, 6), nullable=False),
    Column("price_source", String(20), nullable=False),
    Column("below_floor", Boolean, nullable=False),
    Column("allocated_discount", Numeric(24, 6), nullable=False),
    Column("tax_snapshot", JSONB, nullable=False),
    Column("calculation_snapshot", JSONB, nullable=False),
    Column("taxable_amount", Numeric(24, 6), nullable=False),
    Column("tax_amount", Numeric(24, 6), nullable=False),
    Column("line_total", Numeric(24, 6), nullable=False),
    CheckConstraint("line_position > 0", name="ck_sales_order_line_revisions_position"),
    CheckConstraint(
        "entered_quantity > 0 AND quantity_base > 0",
        name="ck_sales_order_line_revisions_quantity",
    ),
    CheckConstraint(
        "list_unit_price >= 0 AND effective_unit_price >= 0",
        name="ck_sales_order_line_revisions_price",
    ),
    CheckConstraint(
        "price_source IN ('customer', 'branch')",
        name="ck_sales_order_line_revisions_price_source",
    ),
    CheckConstraint(
        "allocated_discount >= 0 AND taxable_amount >= 0 AND tax_amount >= 0 AND line_total >= 0",
        name="ck_sales_order_line_revisions_amounts",
    ),
    UniqueConstraint(
        "sales_order_revision_id",
        "line_id",
        name="uq_sales_order_line_revision_identity",
    ),
    UniqueConstraint(
        "sales_order_revision_id",
        "line_position",
        name="uq_sales_order_line_revision_position",
    ),
    UniqueConstraint(
        "sales_order_revision_id",
        "line_id",
        "sku_id",
        name="uq_sales_order_line_revision_sku_ownership",
    ),
)

commercial_approvals = Table(
    "commercial_approvals",
    metadata,
    Column("commercial_approval_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column("sales_order_id", PostgresUUID(as_uuid=True), nullable=False),
    Column("sales_order_revision_id", PostgresUUID(as_uuid=True), nullable=False, unique=True),
    Column("customer_id", PostgresUUID(as_uuid=True), nullable=False),
    Column(
        "warehouse_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("warehouses.warehouse_id"),
        nullable=False,
    ),
    Column("maker_subject", String(200), ForeignKey("users.subject"), nullable=False),
    Column("approved_by", String(200), ForeignKey("users.subject"), nullable=False),
    Column("payment_timing_policy", String(30), nullable=False),
    Column("order_total", Numeric(24, 6), nullable=False),
    Column("open_balance_snapshot", Numeric(24, 6), nullable=False),
    Column("approved_uninvoiced_snapshot", Numeric(24, 6), nullable=False),
    Column("credit_limit_snapshot", Numeric(24, 6), nullable=True),
    Column("credit_excess_approved", Numeric(24, 6), nullable=False),
    Column("correlation_id", String(100), nullable=False),
    Column("idempotency_key", String(200), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "payment_timing_policy IN ('prepaid', 'cash_on_delivery', 'on_account')",
        name="ck_commercial_approvals_payment_timing",
    ),
    CheckConstraint(
        "order_total >= 0 AND open_balance_snapshot >= 0 "
        "AND approved_uninvoiced_snapshot >= 0 AND credit_excess_approved >= 0",
        name="ck_commercial_approvals_amounts",
    ),
    ForeignKeyConstraint(
        ["sales_order_id", "sales_order_revision_id", "customer_id"],
        [
            "sales_order_revisions.sales_order_id",
            "sales_order_revisions.sales_order_revision_id",
            "sales_order_revisions.customer_id",
        ],
        name="fk_commercial_approvals_customer_revision_ownership",
    ),
    UniqueConstraint(
        "commercial_approval_id",
        "sales_order_id",
        "customer_id",
        name="uq_commercial_approval_customer_ownership",
    ),
    UniqueConstraint(
        "commercial_approval_id",
        "sales_order_id",
        "sales_order_revision_id",
        "warehouse_id",
        name="uq_commercial_approval_reservation_ownership",
    ),
)

commercial_exception_approvals = Table(
    "commercial_exception_approvals",
    metadata,
    Column("exception_approval_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "commercial_approval_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("commercial_approvals.commercial_approval_id"),
        nullable=False,
    ),
    Column("exception_type", String(30), nullable=False),
    Column("maker_subject", String(200), ForeignKey("users.subject"), nullable=False),
    Column("approved_by", String(200), ForeignKey("users.subject"), nullable=False),
    Column("reason", String(500), nullable=False),
    Column("exception_amount", Numeric(24, 6), nullable=False),
    Column("exception_percentage", Numeric(9, 6), nullable=True),
    Column("authority_snapshot", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "exception_type IN ('discount', 'below_floor', 'credit_override')",
        name="ck_commercial_exception_approvals_type",
    ),
    CheckConstraint(
        "exception_amount >= 0",
        name="ck_commercial_exception_approvals_amount",
    ),
    CheckConstraint(
        "exception_percentage IS NULL "
        "OR (exception_percentage >= 0 AND exception_percentage <= 100)",
        name="ck_commercial_exception_approvals_percentage",
    ),
    CheckConstraint(
        "maker_subject <> approved_by",
        name="ck_commercial_exception_approvals_maker_checker",
    ),
    UniqueConstraint(
        "commercial_approval_id",
        "exception_type",
        name="uq_commercial_exception_approval_type",
    ),
)

commercial_approval_invalidations = Table(
    "commercial_approval_invalidations",
    metadata,
    Column("invalidation_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "commercial_approval_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("commercial_approvals.commercial_approval_id"),
        nullable=False,
        unique=True,
    ),
    Column("reason", String(500), nullable=False),
    Column("invalidated_by", String(200), ForeignKey("users.subject"), nullable=False),
    Column("correlation_id", String(100), nullable=False),
    Column("idempotency_key", String(200), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

customer_credit_exposure = Table(
    "customer_credit_exposure",
    metadata,
    Column(
        "customer_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("customer_accounts.customer_id"),
        primary_key=True,
    ),
    Column("open_balance", Numeric(24, 6), nullable=False, server_default="0"),
    Column("approved_uninvoiced", Numeric(24, 6), nullable=False, server_default="0"),
    Column("version", Integer, nullable=False, server_default="1"),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    ),
    CheckConstraint(
        "open_balance >= 0 AND approved_uninvoiced >= 0",
        name="ck_customer_credit_exposure_amounts",
    ),
    CheckConstraint("version > 0", name="ck_customer_credit_exposure_version"),
)

credit_exposure_entries = Table(
    "credit_exposure_entries",
    metadata,
    Column("entry_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "customer_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("customer_accounts.customer_id"),
        nullable=False,
    ),
    Column(
        "commercial_approval_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("commercial_approvals.commercial_approval_id"),
        nullable=True,
    ),
    Column(
        "sales_order_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("sales_orders.sales_order_id"),
        nullable=True,
    ),
    Column("component", String(30), nullable=False),
    Column("amount_delta", Numeric(24, 6), nullable=False),
    Column("source_type", String(50), nullable=False),
    Column("source_id", PostgresUUID(as_uuid=True), nullable=False),
    Column("actor_subject", String(200), ForeignKey("users.subject"), nullable=False),
    Column("correlation_id", String(100), nullable=False),
    Column("idempotency_key", String(200), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "component IN ('posted_open_balance', 'approved_uninvoiced')",
        name="ck_credit_exposure_entries_component",
    ),
    CheckConstraint(
        "amount_delta <> 0",
        name="ck_credit_exposure_entries_amount_nonzero",
    ),
    CheckConstraint(
        "(component <> 'approved_uninvoiced') "
        "OR (commercial_approval_id IS NOT NULL AND sales_order_id IS NOT NULL)",
        name="ck_credit_exposure_entries_order_reference",
    ),
    UniqueConstraint(
        "source_type",
        "source_id",
        "component",
        name="uq_credit_exposure_entry_source_component",
    ),
    UniqueConstraint(
        "idempotency_key",
        "component",
        name="uq_credit_exposure_entry_command_component",
    ),
    ForeignKeyConstraint(
        ["commercial_approval_id", "sales_order_id", "customer_id"],
        [
            "commercial_approvals.commercial_approval_id",
            "commercial_approvals.sales_order_id",
            "commercial_approvals.customer_id",
        ],
        name="fk_credit_exposure_entries_approval_ownership",
    ),
)

inventory_reservation_events = Table(
    "inventory_reservation_events",
    metadata,
    Column("reservation_event_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "commercial_approval_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("commercial_approvals.commercial_approval_id"),
        nullable=False,
    ),
    Column(
        "sales_order_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("sales_orders.sales_order_id"),
        nullable=False,
    ),
    Column(
        "sales_order_revision_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("sales_order_revisions.sales_order_revision_id"),
        nullable=False,
    ),
    Column("line_id", PostgresUUID(as_uuid=True), nullable=False),
    Column("sku_id", PostgresUUID(as_uuid=True), ForeignKey("skus.sku_id"), nullable=False),
    Column(
        "warehouse_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("warehouses.warehouse_id"),
        nullable=False,
    ),
    Column("event_type", String(20), nullable=False),
    Column("quantity_base", Numeric(18, 6), nullable=False),
    Column("reason", String(500), nullable=False),
    Column("actor_subject", String(200), ForeignKey("users.subject"), nullable=False),
    Column("correlation_id", String(100), nullable=False),
    Column("idempotency_key", String(200), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "event_type IN ('reserved', 'released', 'consumed', 'restored')",
        name="ck_inventory_reservation_events_type",
    ),
    CheckConstraint(
        "quantity_base > 0",
        name="ck_inventory_reservation_events_quantity",
    ),
    UniqueConstraint(
        "idempotency_key",
        "line_id",
        "event_type",
        name="uq_inventory_reservation_event_command_line",
    ),
    ForeignKeyConstraint(
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
    ForeignKeyConstraint(
        ["sales_order_revision_id", "line_id", "sku_id"],
        [
            "sales_order_line_revisions.sales_order_revision_id",
            "sales_order_line_revisions.line_id",
            "sales_order_line_revisions.sku_id",
        ],
        name="fk_inventory_reservation_events_line_ownership",
    ),
)

inventory_reserved_by_sku_warehouse = Table(
    "inventory_reserved_by_sku_warehouse",
    metadata,
    Column(
        "sku_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("skus.sku_id"),
        primary_key=True,
    ),
    Column(
        "warehouse_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("warehouses.warehouse_id"),
        primary_key=True,
    ),
    Column(
        "reserved_quantity_base",
        Numeric(18, 6),
        nullable=False,
        server_default="0",
    ),
    Column("version", Integer, nullable=False, server_default="1"),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    ),
    CheckConstraint(
        "reserved_quantity_base >= 0",
        name="ck_inventory_reserved_by_sku_warehouse_quantity",
    ),
    CheckConstraint(
        "version > 0",
        name="ck_inventory_reserved_by_sku_warehouse_version",
    ),
)

sales_order_line_commitments = Table(
    "sales_order_line_commitments",
    metadata,
    Column(
        "sales_order_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("sales_orders.sales_order_id"),
        primary_key=True,
    ),
    Column("line_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "commercial_approval_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("commercial_approvals.commercial_approval_id"),
        nullable=False,
    ),
    Column(
        "sales_order_revision_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("sales_order_revisions.sales_order_revision_id"),
        nullable=False,
    ),
    Column("sku_id", PostgresUUID(as_uuid=True), ForeignKey("skus.sku_id"), nullable=False),
    Column(
        "warehouse_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("warehouses.warehouse_id"),
        nullable=False,
    ),
    Column("ordered_quantity_base", Numeric(18, 6), nullable=False),
    Column("reserved_quantity_base", Numeric(18, 6), nullable=False),
    Column("picked_quantity_base", Numeric(18, 6), nullable=False, server_default="0"),
    Column("backorder_quantity_base", Numeric(18, 6), nullable=False),
    CheckConstraint(
        "ordered_quantity_base > 0 AND reserved_quantity_base >= 0 "
        "AND picked_quantity_base >= 0 AND backorder_quantity_base >= 0 "
        "AND reserved_quantity_base + picked_quantity_base + backorder_quantity_base "
        "= ordered_quantity_base",
        name="ck_sales_order_line_commitments_quantities",
    ),
    ForeignKeyConstraint(
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
    ForeignKeyConstraint(
        ["sales_order_revision_id", "line_id", "sku_id"],
        [
            "sales_order_line_revisions.sales_order_revision_id",
            "sales_order_line_revisions.line_id",
            "sales_order_line_revisions.sku_id",
        ],
        name="fk_sales_order_line_commitments_line_ownership",
    ),
)

branch_payment_deadline_policies = Table(
    "branch_payment_deadline_policies",
    metadata,
    Column("policy_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "branch_id", PostgresUUID(as_uuid=True), ForeignKey("branches.branch_id"), nullable=False
    ),
    Column("version", Integer, nullable=False),
    Column("deadline_minutes", Integer, nullable=False),
    Column("effective_from", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("is_active", Boolean, nullable=False, server_default="true"),
    Column("created_by", String(200), ForeignKey("users.subject"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("version > 0", name="ck_branch_payment_deadline_policy_version"),
    CheckConstraint(
        "deadline_minutes > 0",
        name="ck_branch_payment_deadline_policy_duration",
    ),
    UniqueConstraint(
        "branch_id",
        "version",
        name="uq_branch_payment_deadline_policy_version",
    ),
)

payment_methods = Table(
    "payment_methods",
    metadata,
    Column("payment_method_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "company_id", PostgresUUID(as_uuid=True), ForeignKey("companies.company_id"), nullable=False
    ),
    Column("code", String(50), nullable=False),
    Column("name", String(100), nullable=False),
    Column("kind", String(30), nullable=False),
    Column("requires_external_reference", Boolean, nullable=False),
    Column("requires_evidence", Boolean, nullable=False),
    Column("provider_confirmation_enabled", Boolean, nullable=False, server_default="false"),
    Column("provider_code", String(100), nullable=True),
    Column("is_active", Boolean, nullable=False, server_default="true"),
    Column("version", Integer, nullable=False, server_default="1"),
    CheckConstraint(
        "kind IN ('cash', 'bank_transfer', 'check', 'electronic')",
        name="ck_payment_methods_kind",
    ),
    CheckConstraint("version > 0", name="ck_payment_methods_version"),
    UniqueConstraint("company_id", "code", name="uq_payment_method_company_code"),
)

fulfillment_orders = Table(
    "fulfillment_orders",
    metadata,
    Column("fulfillment_order_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column("sales_order_id", PostgresUUID(as_uuid=True), nullable=False),
    Column("sales_order_revision_id", PostgresUUID(as_uuid=True), nullable=False),
    Column("commercial_approval_id", PostgresUUID(as_uuid=True), nullable=False),
    Column(
        "customer_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("customer_accounts.customer_id"),
        nullable=False,
    ),
    Column(
        "branch_id", PostgresUUID(as_uuid=True), ForeignKey("branches.branch_id"), nullable=False
    ),
    Column("warehouse_id", PostgresUUID(as_uuid=True), nullable=False),
    Column("reservation_generation", Integer, nullable=False),
    Column("payment_timing_policy", String(30), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("order_value", Numeric(24, 6), nullable=False),
    Column("payment_required", Numeric(24, 6), nullable=False),
    Column("payment_deadline_at", DateTime(timezone=True), nullable=True),
    Column(
        "payment_deadline_policy_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("branch_payment_deadline_policies.policy_id"),
        nullable=True,
    ),
    Column("payment_deadline_minutes", Integer, nullable=True),
    Column("created_by", String(200), ForeignKey("users.subject"), nullable=False),
    Column("correlation_id", String(100), nullable=False),
    Column("idempotency_key", String(200), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "reservation_generation > 0",
        name="ck_fulfillment_orders_generation",
    ),
    CheckConstraint(
        "payment_timing_policy IN ('prepaid', 'cash_on_delivery', 'on_account')",
        name="ck_fulfillment_orders_payment_timing",
    ),
    CheckConstraint(
        "order_value >= 0 AND payment_required >= 0 AND payment_required <= order_value",
        name="ck_fulfillment_orders_values",
    ),
    CheckConstraint(
        "(payment_timing_policy = 'prepaid' AND payment_deadline_at IS NOT NULL "
        "AND payment_deadline_policy_id IS NOT NULL AND payment_deadline_minutes > 0) "
        "OR (payment_timing_policy <> 'prepaid' AND payment_deadline_at IS NULL)",
        name="ck_fulfillment_orders_deadline",
    ),
    ForeignKeyConstraint(
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
        name="fk_fulfillment_orders_approval_ownership",
    ),
    UniqueConstraint(
        "sales_order_id",
        "warehouse_id",
        "reservation_generation",
        name="uq_fulfillment_order_generation",
    ),
    UniqueConstraint(
        "fulfillment_order_id",
        "sales_order_id",
        "sales_order_revision_id",
        "commercial_approval_id",
        "warehouse_id",
        name="uq_fulfillment_order_ownership",
    ),
)

fulfillment_order_lines = Table(
    "fulfillment_order_lines",
    metadata,
    Column(
        "fulfillment_order_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("fulfillment_orders.fulfillment_order_id"),
        primary_key=True,
    ),
    Column("line_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column("sales_order_id", PostgresUUID(as_uuid=True), nullable=False),
    Column("sales_order_revision_id", PostgresUUID(as_uuid=True), nullable=False),
    Column("commercial_approval_id", PostgresUUID(as_uuid=True), nullable=False),
    Column("sku_id", PostgresUUID(as_uuid=True), nullable=False),
    Column("warehouse_id", PostgresUUID(as_uuid=True), nullable=False),
    Column("ordered_quantity_base", Numeric(18, 6), nullable=False),
    Column("reserved_quantity_base", Numeric(18, 6), nullable=False),
    Column("backorder_quantity_base", Numeric(18, 6), nullable=False),
    Column("approved_line_total", Numeric(24, 6), nullable=False),
    Column("reserved_value", Numeric(24, 6), nullable=False),
    Column("calculation_snapshot", JSONB, nullable=False),
    CheckConstraint(
        "ordered_quantity_base > 0 AND reserved_quantity_base >= 0 "
        "AND backorder_quantity_base >= 0 "
        "AND reserved_quantity_base + backorder_quantity_base = ordered_quantity_base",
        name="ck_fulfillment_order_lines_quantities",
    ),
    CheckConstraint(
        "approved_line_total >= 0 AND reserved_value >= 0 "
        "AND reserved_value <= approved_line_total",
        name="ck_fulfillment_order_lines_values",
    ),
    ForeignKeyConstraint(
        [
            "fulfillment_order_id",
            "sales_order_id",
            "sales_order_revision_id",
            "commercial_approval_id",
            "warehouse_id",
        ],
        [
            "fulfillment_orders.fulfillment_order_id",
            "fulfillment_orders.sales_order_id",
            "fulfillment_orders.sales_order_revision_id",
            "fulfillment_orders.commercial_approval_id",
            "fulfillment_orders.warehouse_id",
        ],
        name="fk_fulfillment_order_lines_order_ownership",
    ),
    ForeignKeyConstraint(
        ["sales_order_revision_id", "line_id", "sku_id"],
        [
            "sales_order_line_revisions.sales_order_revision_id",
            "sales_order_line_revisions.line_id",
            "sales_order_line_revisions.sku_id",
        ],
        name="fk_fulfillment_order_lines_line_ownership",
    ),
)

fulfillment_order_state = Table(
    "fulfillment_order_state",
    metadata,
    Column(
        "fulfillment_order_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("fulfillment_orders.fulfillment_order_id"),
        primary_key=True,
    ),
    Column("status", String(30), nullable=False),
    Column("reserved_quantity_base", Numeric(18, 6), nullable=False),
    Column("backorder_quantity_base", Numeric(18, 6), nullable=False),
    Column("covered_amount", Numeric(24, 6), nullable=False, server_default="0"),
    Column("picked_quantity_base", Numeric(18, 6), nullable=False, server_default="0"),
    Column("dispatched_quantity_base", Numeric(18, 6), nullable=False, server_default="0"),
    Column("delivered_quantity_base", Numeric(18, 6), nullable=False, server_default="0"),
    Column("payment_hold", Boolean, nullable=False, server_default="false"),
    Column("version", Integer, nullable=False, server_default="1"),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "status IN ('reserved', 'payment_ready', 'pick_released', 'partially_picked', "
        "'picked', 'partially_dispatched', 'dispatched', 'delivered', "
        "'payment_hold', 'cancelled')",
        name="ck_fulfillment_order_state_status",
    ),
    CheckConstraint(
        "reserved_quantity_base >= 0 AND backorder_quantity_base >= 0 "
        "AND covered_amount >= 0 AND picked_quantity_base >= 0 "
        "AND dispatched_quantity_base >= 0 AND delivered_quantity_base >= 0 "
        "AND dispatched_quantity_base <= picked_quantity_base "
        "AND delivered_quantity_base <= dispatched_quantity_base",
        name="ck_fulfillment_order_state_amounts",
    ),
    CheckConstraint("version > 0", name="ck_fulfillment_order_state_version"),
)

payment_receipts = Table(
    "payment_receipts",
    metadata,
    Column("payment_receipt_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "company_id", PostgresUUID(as_uuid=True), ForeignKey("companies.company_id"), nullable=False
    ),
    Column(
        "branch_id", PostgresUUID(as_uuid=True), ForeignKey("branches.branch_id"), nullable=False
    ),
    Column(
        "customer_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("customer_accounts.customer_id"),
        nullable=False,
    ),
    Column(
        "payment_method_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("payment_methods.payment_method_id"),
        nullable=False,
    ),
    Column("payment_method_code", String(50), nullable=False),
    Column("payment_method_kind", String(30), nullable=False),
    Column("amount", Numeric(24, 6), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
    Column("external_reference", String(200), nullable=True),
    Column("external_reference_normalized", String(200), nullable=True),
    Column("evidence", JSONB, nullable=True),
    Column(
        "intended_sales_order_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("sales_orders.sales_order_id"),
        nullable=True,
    ),
    Column(
        "intended_fulfillment_order_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("fulfillment_orders.fulfillment_order_id"),
        nullable=True,
    ),
    Column("recorded_by", String(200), ForeignKey("users.subject"), nullable=False),
    Column("correlation_id", String(100), nullable=False),
    Column("idempotency_key", String(200), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("amount > 0", name="ck_payment_receipts_amount"),
    CheckConstraint(
        "payment_method_kind IN ('cash', 'bank_transfer', 'check', 'electronic')",
        name="ck_payment_receipts_kind",
    ),
)

payment_receipt_events = Table(
    "payment_receipt_events",
    metadata,
    Column("payment_receipt_event_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "payment_receipt_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("payment_receipts.payment_receipt_id"),
        nullable=False,
    ),
    Column("event_type", String(30), nullable=False),
    Column("actor_subject", String(200), ForeignKey("users.subject"), nullable=False),
    Column("reason", String(500), nullable=True),
    Column("evidence", JSONB, nullable=True),
    Column("source_id", PostgresUUID(as_uuid=True), nullable=False),
    Column("correlation_id", String(100), nullable=False),
    Column("idempotency_key", String(200), nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "event_type IN ('recorded', 'verified', 'bank_cleared', 'provider_confirmed', "
        "'cleared', 'rejected', 'reversed', 'refunded')",
        name="ck_payment_receipt_events_type",
    ),
    UniqueConstraint(
        "payment_receipt_id",
        "idempotency_key",
        "event_type",
        name="uq_payment_receipt_event_command_type",
    ),
)

payment_receipt_status = Table(
    "payment_receipt_status",
    metadata,
    Column(
        "payment_receipt_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("payment_receipts.payment_receipt_id"),
        primary_key=True,
    ),
    Column("company_id", PostgresUUID(as_uuid=True), nullable=False),
    Column("payment_method_id", PostgresUUID(as_uuid=True), nullable=False),
    Column("external_reference_normalized", String(200), nullable=True),
    Column("state", String(30), nullable=False),
    Column("verified_by", String(200), ForeignKey("users.subject"), nullable=True),
    Column("cleared_at", DateTime(timezone=True), nullable=True),
    Column("reversal_id", PostgresUUID(as_uuid=True), nullable=True),
    Column("version", Integer, nullable=False, server_default="1"),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "state IN ('pending_verification', 'pending_clearance', 'cleared', 'rejected', 'reversed')",
        name="ck_payment_receipt_status_state",
    ),
    CheckConstraint("version > 0", name="ck_payment_receipt_status_version"),
)

Index(
    "uq_payment_receipt_active_external_reference",
    payment_receipt_status.c.company_id,
    payment_receipt_status.c.payment_method_id,
    payment_receipt_status.c.external_reference_normalized,
    unique=True,
    postgresql_where=(
        payment_receipt_status.c.external_reference_normalized.is_not(None)
        & payment_receipt_status.c.state.in_(
            ("pending_verification", "pending_clearance", "cleared")
        )
    ),
)

payment_receipt_balances = Table(
    "payment_receipt_balances",
    metadata,
    Column(
        "payment_receipt_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("payment_receipts.payment_receipt_id"),
        primary_key=True,
    ),
    Column("cleared_amount", Numeric(24, 6), nullable=False, server_default="0"),
    Column("reversed_amount", Numeric(24, 6), nullable=False, server_default="0"),
    Column("refunded_amount", Numeric(24, 6), nullable=False, server_default="0"),
    Column("allocated_amount", Numeric(24, 6), nullable=False, server_default="0"),
    Column("coverage_designated_amount", Numeric(24, 6), nullable=False, server_default="0"),
    Column("version", Integer, nullable=False, server_default="1"),
    CheckConstraint(
        "cleared_amount >= 0 AND reversed_amount >= 0 AND refunded_amount >= 0 "
        "AND allocated_amount >= 0 AND coverage_designated_amount >= 0 "
        "AND reversed_amount + refunded_amount + allocated_amount <= cleared_amount "
        "AND coverage_designated_amount <= "
        "cleared_amount - reversed_amount - refunded_amount - allocated_amount",
        name="ck_payment_receipt_balances_nonnegative",
    ),
    CheckConstraint("version > 0", name="ck_payment_receipt_balances_version"),
)

cash_reconciliation_items = Table(
    "cash_reconciliation_items",
    metadata,
    Column(
        "payment_receipt_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("payment_receipts.payment_receipt_id"),
        primary_key=True,
    ),
    Column("status", String(20), nullable=False, server_default="pending"),
    Column("expected_amount", Numeric(24, 6), nullable=False),
    Column("counted_amount", Numeric(24, 6), nullable=True),
    Column("variance_amount", Numeric(24, 6), nullable=True),
    Column("cash_reconciliation_id", PostgresUUID(as_uuid=True), nullable=True, unique=True),
    Column("reconciled_by", String(200), ForeignKey("users.subject"), nullable=True),
    Column("reconciled_at", DateTime(timezone=True), nullable=True),
    Column("reason", String(500), nullable=True),
    CheckConstraint(
        "status IN ('pending', 'reconciled')",
        name="ck_cash_reconciliation_items_status",
    ),
)

prepayment_coverage_events = Table(
    "prepayment_coverage_events",
    metadata,
    Column("coverage_event_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "fulfillment_order_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("fulfillment_orders.fulfillment_order_id"),
        nullable=False,
    ),
    Column(
        "payment_receipt_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("payment_receipts.payment_receipt_id"),
        nullable=False,
    ),
    Column("event_type", String(20), nullable=False),
    Column("amount", Numeric(24, 6), nullable=False),
    Column("reason", String(500), nullable=False),
    Column("actor_subject", String(200), ForeignKey("users.subject"), nullable=False),
    Column("source_id", PostgresUUID(as_uuid=True), nullable=False),
    Column("correlation_id", String(100), nullable=False),
    Column("idempotency_key", String(200), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "event_type IN ('designated', 'released', 'consumed')",
        name="ck_prepayment_coverage_events_type",
    ),
    CheckConstraint("amount > 0", name="ck_prepayment_coverage_events_amount"),
    UniqueConstraint(
        "idempotency_key",
        "payment_receipt_id",
        "fulfillment_order_id",
        "event_type",
        name="uq_prepayment_coverage_event_command",
    ),
)

sales_order_hold_events = Table(
    "sales_order_hold_events",
    metadata,
    Column("hold_event_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "sales_order_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("sales_orders.sales_order_id"),
        nullable=False,
    ),
    Column(
        "fulfillment_order_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("fulfillment_orders.fulfillment_order_id"),
        nullable=False,
    ),
    Column("hold_type", String(30), nullable=False),
    Column("event_type", String(20), nullable=False),
    Column("reason", String(500), nullable=False),
    Column("actor_subject", String(200), ForeignKey("users.subject"), nullable=False),
    Column("correlation_id", String(100), nullable=False),
    Column("idempotency_key", String(200), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("hold_type IN ('payment')", name="ck_sales_order_hold_events_type"),
    CheckConstraint(
        "event_type IN ('applied', 'released')",
        name="ck_sales_order_hold_events_event",
    ),
    UniqueConstraint(
        "idempotency_key",
        "sales_order_id",
        "hold_type",
        "event_type",
        name="uq_sales_order_hold_event_command",
    ),
)

active_sales_order_holds = Table(
    "active_sales_order_holds",
    metadata,
    Column(
        "sales_order_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("sales_orders.sales_order_id"),
        primary_key=True,
    ),
    Column("hold_type", String(30), primary_key=True),
    Column(
        "fulfillment_order_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("fulfillment_orders.fulfillment_order_id"),
        nullable=False,
    ),
    Column(
        "hold_event_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("sales_order_hold_events.hold_event_id"),
        nullable=False,
    ),
    Column("applied_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

pick_releases = Table(
    "pick_releases",
    metadata,
    Column("pick_release_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "fulfillment_order_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("fulfillment_orders.fulfillment_order_id"),
        nullable=False,
        unique=True,
    ),
    Column("quantity_base", Numeric(18, 6), nullable=False),
    Column("payment_required", Numeric(24, 6), nullable=False),
    Column("cleared_payment", Numeric(24, 6), nullable=False),
    Column("reason", String(500), nullable=False),
    Column("released_by", String(200), ForeignKey("users.subject"), nullable=False),
    Column("correlation_id", String(100), nullable=False),
    Column("idempotency_key", String(200), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("quantity_base > 0", name="ck_pick_releases_quantity"),
    CheckConstraint(
        "payment_required >= 0 AND cleared_payment >= payment_required",
        name="ck_pick_releases_payment",
    ),
)

payment_refunds = Table(
    "payment_refunds",
    metadata,
    Column("payment_refund_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "payment_receipt_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("payment_receipts.payment_receipt_id"),
        nullable=False,
    ),
    Column("amount", Numeric(24, 6), nullable=False),
    Column("reason", String(500), nullable=False),
    Column("requested_by", String(200), ForeignKey("users.subject"), nullable=False),
    Column("approved_by", String(200), ForeignKey("users.subject"), nullable=False),
    Column("correlation_id", String(100), nullable=False),
    Column("idempotency_key", String(200), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("amount > 0", name="ck_payment_refunds_amount"),
    CheckConstraint(
        "requested_by <> approved_by",
        name="ck_payment_refunds_maker_checker",
    ),
)

pick_postings = Table(
    "pick_postings",
    metadata,
    Column("pick_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "fulfillment_order_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("fulfillment_orders.fulfillment_order_id"),
        nullable=False,
    ),
    Column(
        "pick_release_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("pick_releases.pick_release_id"),
        nullable=False,
    ),
    Column(
        "warehouse_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("warehouses.warehouse_id"),
        nullable=False,
    ),
    Column("event_type", String(20), nullable=False),
    Column(
        "reversal_of_pick_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("pick_postings.pick_id"),
        nullable=True,
    ),
    Column("reason", String(500), nullable=True),
    Column("actor_subject", String(200), ForeignKey("users.subject"), nullable=False),
    Column("correlation_id", String(100), nullable=False),
    Column("idempotency_key", String(200), nullable=False, unique=True),
    Column("posted_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "event_type IN ('posted', 'reversed')",
        name="ck_pick_postings_event_type",
    ),
    CheckConstraint(
        "(event_type = 'posted' AND reversal_of_pick_id IS NULL) "
        "OR (event_type = 'reversed' AND reversal_of_pick_id IS NOT NULL)",
        name="ck_pick_postings_reversal_shape",
    ),
)
Index(
    "uq_pick_posting_reversal",
    pick_postings.c.reversal_of_pick_id,
    unique=True,
    postgresql_where=pick_postings.c.reversal_of_pick_id.is_not(None),
)

pick_lines = Table(
    "pick_lines",
    metadata,
    Column("pick_line_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "pick_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("pick_postings.pick_id"),
        nullable=False,
    ),
    Column("fulfillment_order_id", PostgresUUID(as_uuid=True), nullable=False),
    Column("line_id", PostgresUUID(as_uuid=True), nullable=False),
    Column("sku_id", PostgresUUID(as_uuid=True), ForeignKey("skus.sku_id"), nullable=False),
    Column(
        "warehouse_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("warehouses.warehouse_id"),
        nullable=False,
    ),
    Column(
        "source_location_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("warehouse_stock_locations.location_id"),
        nullable=False,
    ),
    Column(
        "staging_location_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("warehouse_stock_locations.location_id"),
        nullable=False,
    ),
    Column("quantity_base", Numeric(18, 6), nullable=False),
    Column("entered_quantity", Numeric(18, 6), nullable=False),
    Column("entered_unit", String(30), nullable=False),
    Column("conversion_snapshot", JSONB, nullable=False),
    Column("capture_mode", String(20), nullable=False),
    Column(
        "barcode_mapping_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("barcode_mappings.barcode_mapping_id"),
        nullable=True,
    ),
    Column("manual_reason", String(500), nullable=True),
    Column("fefo_override_reason", String(500), nullable=True),
    Column("movement_group_id", PostgresUUID(as_uuid=True), nullable=False, unique=True),
    Column(
        "source_movement_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("stock_movements.movement_id"),
        nullable=False,
        unique=True,
    ),
    Column(
        "staging_movement_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("stock_movements.movement_id"),
        nullable=False,
        unique=True,
    ),
    CheckConstraint(
        "quantity_base > 0 AND entered_quantity > 0",
        name="ck_pick_lines_quantity",
    ),
    CheckConstraint(
        "capture_mode IN ('automatic', 'barcode', 'manual')",
        name="ck_pick_lines_capture_mode",
    ),
    CheckConstraint(
        "source_location_id <> staging_location_id",
        name="ck_pick_lines_locations",
    ),
    ForeignKeyConstraint(
        ["fulfillment_order_id", "line_id"],
        ["fulfillment_order_lines.fulfillment_order_id", "fulfillment_order_lines.line_id"],
        name="fk_pick_lines_fulfillment_line",
    ),
)

pick_identity_assignments = Table(
    "pick_identity_assignments",
    metadata,
    Column("pick_identity_assignment_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "pick_line_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("pick_lines.pick_line_id"),
        nullable=False,
    ),
    Column("tracking_policy", String(20), nullable=False),
    Column(
        "lot_identity_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("lot_identities.lot_identity_id"),
        nullable=True,
    ),
    Column(
        "serial_allocation_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("stock_serial_allocations.serial_allocation_id"),
        nullable=True,
    ),
    Column("captured_barcode", String(100), nullable=True),
    Column("quantity_base", Numeric(18, 6), nullable=False),
    CheckConstraint(
        "tracking_policy IN ('lot', 'serial')",
        name="ck_pick_identity_assignments_policy",
    ),
    CheckConstraint(
        "quantity_base > 0",
        name="ck_pick_identity_assignments_quantity",
    ),
    CheckConstraint(
        "(tracking_policy = 'lot' AND lot_identity_id IS NOT NULL "
        "AND serial_allocation_id IS NULL) "
        "OR (tracking_policy = 'serial' AND lot_identity_id IS NULL "
        "AND serial_allocation_id IS NOT NULL AND quantity_base = 1)",
        name="ck_pick_identity_assignments_shape",
    ),
    UniqueConstraint(
        "pick_line_id",
        "lot_identity_id",
        name="uq_pick_identity_assignment_lot",
    ),
    UniqueConstraint(
        "pick_line_id",
        "serial_allocation_id",
        name="uq_pick_identity_assignment_serial",
    ),
)

fulfillment_line_pick_state = Table(
    "fulfillment_line_pick_state",
    metadata,
    Column(
        "fulfillment_order_id",
        PostgresUUID(as_uuid=True),
        primary_key=True,
    ),
    Column("line_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column("released_quantity_base", Numeric(18, 6), nullable=False),
    Column("picked_quantity_base", Numeric(18, 6), nullable=False, server_default="0"),
    Column("reversed_quantity_base", Numeric(18, 6), nullable=False, server_default="0"),
    Column("version", Integer, nullable=False, server_default="1"),
    ForeignKeyConstraint(
        ["fulfillment_order_id", "line_id"],
        ["fulfillment_order_lines.fulfillment_order_id", "fulfillment_order_lines.line_id"],
        name="fk_fulfillment_line_pick_state_line",
    ),
    CheckConstraint(
        "released_quantity_base > 0 AND picked_quantity_base >= 0 "
        "AND reversed_quantity_base >= 0 "
        "AND picked_quantity_base >= reversed_quantity_base "
        "AND picked_quantity_base - reversed_quantity_base <= released_quantity_base",
        name="ck_fulfillment_line_pick_state_quantities",
    ),
    CheckConstraint(
        "version > 0",
        name="ck_fulfillment_line_pick_state_version",
    ),
)

delivery_dispatches = Table(
    "delivery_dispatches",
    metadata,
    Column("delivery_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "fulfillment_order_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("fulfillment_orders.fulfillment_order_id"),
        nullable=False,
    ),
    Column("sales_order_id", PostgresUUID(as_uuid=True), nullable=False),
    Column("sales_order_revision_id", PostgresUUID(as_uuid=True), nullable=False),
    Column(
        "customer_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("customer_accounts.customer_id"),
        nullable=False,
    ),
    Column(
        "branch_id", PostgresUUID(as_uuid=True), ForeignKey("branches.branch_id"), nullable=False
    ),
    Column(
        "warehouse_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("warehouses.warehouse_id"),
        nullable=False,
    ),
    Column(
        "delivery_address_version_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("customer_address_versions.address_version_id"),
        nullable=False,
    ),
    Column("delivery_address_snapshot", JSONB, nullable=False),
    Column("recipient_name_snapshot", String(300), nullable=False),
    Column("payment_timing_policy", String(30), nullable=False),
    Column("evidence_requirements", JSONB, nullable=False),
    Column("initial_assignee_subject", String(200), ForeignKey("users.subject"), nullable=False),
    Column("dispatched_by", String(200), ForeignKey("users.subject"), nullable=False),
    Column("correlation_id", String(100), nullable=False),
    Column("idempotency_key", String(200), nullable=False),
    Column("dispatched_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "payment_timing_policy IN ('prepaid', 'cash_on_delivery', 'on_account')",
        name="ck_delivery_dispatches_payment_timing",
    ),
    UniqueConstraint(
        "dispatched_by",
        "idempotency_key",
        name="uq_delivery_dispatch_actor_idempotency",
    ),
)

delivery_state = Table(
    "delivery_state",
    metadata,
    Column(
        "delivery_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_dispatches.delivery_id"),
        primary_key=True,
    ),
    Column("status", String(30), nullable=False),
    Column("assigned_to", String(200), ForeignKey("users.subject"), nullable=False),
    Column("version", Integer, nullable=False, server_default="1"),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("status IN ('dispatched', 'confirmed')", name="ck_delivery_state_status"),
    CheckConstraint("version > 0", name="ck_delivery_state_version"),
)

delivery_assignment_events = Table(
    "delivery_assignment_events",
    metadata,
    Column("delivery_assignment_event_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "delivery_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_dispatches.delivery_id"),
        nullable=False,
    ),
    Column("previous_assignee_subject", String(200), ForeignKey("users.subject"), nullable=False),
    Column("assigned_to", String(200), ForeignKey("users.subject"), nullable=False),
    Column("delivery_version", Integer, nullable=False),
    Column("reason", String(500), nullable=False),
    Column("assigned_by", String(200), ForeignKey("users.subject"), nullable=False),
    Column("correlation_id", String(100), nullable=False),
    Column("idempotency_key", String(200), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "previous_assignee_subject <> assigned_to",
        name="ck_delivery_assignment_events_change",
    ),
    CheckConstraint("delivery_version > 1", name="ck_delivery_assignment_events_version"),
    UniqueConstraint(
        "assigned_by",
        "idempotency_key",
        name="uq_delivery_assignment_actor_idempotency",
    ),
)

delivery_lines = Table(
    "delivery_lines",
    metadata,
    Column("delivery_line_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "delivery_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_dispatches.delivery_id"),
        nullable=False,
    ),
    Column(
        "pick_line_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("pick_lines.pick_line_id"),
        nullable=False,
        unique=True,
    ),
    Column("line_id", PostgresUUID(as_uuid=True), nullable=False),
    Column("sku_id", PostgresUUID(as_uuid=True), ForeignKey("skus.sku_id"), nullable=False),
    Column("quantity_base", Numeric(18, 6), nullable=False),
    Column("movement_group_id", PostgresUUID(as_uuid=True), nullable=False, unique=True),
    Column(
        "staging_movement_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("stock_movements.movement_id"),
        nullable=False,
        unique=True,
    ),
    Column(
        "transit_movement_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("stock_movements.movement_id"),
        nullable=False,
        unique=True,
    ),
    CheckConstraint("quantity_base > 0", name="ck_delivery_lines_quantity"),
    UniqueConstraint("delivery_id", "pick_line_id", name="uq_delivery_line_pick"),
)

delivery_evidence = Table(
    "delivery_evidence",
    metadata,
    Column("evidence_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "delivery_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_dispatches.delivery_id"),
        nullable=False,
    ),
    Column("kind", String(30), nullable=False),
    Column("object_key", String(500), nullable=False, unique=True),
    Column("content_type", String(100), nullable=False),
    Column("size_bytes", Integer, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("captured_by", String(200), ForeignKey("users.subject"), nullable=False),
    Column("device_captured_at", DateTime(timezone=True), nullable=False),
    Column("status", String(30), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("verified_at", DateTime(timezone=True), nullable=True),
    CheckConstraint("kind IN ('signature', 'photo')", name="ck_delivery_evidence_kind"),
    CheckConstraint(
        "status IN ('uploading', 'verified', 'rejected')", name="ck_delivery_evidence_status"
    ),
    UniqueConstraint("delivery_id", "evidence_id", name="uq_delivery_evidence_delivery"),
)

delivery_confirmations = Table(
    "delivery_confirmations",
    metadata,
    Column("confirmation_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "delivery_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_dispatches.delivery_id"),
        nullable=False,
        unique=True,
    ),
    Column("recipient_name", String(300), nullable=False),
    Column("device_captured_at", DateTime(timezone=True), nullable=False),
    Column("notes", String(2000), nullable=True),
    Column("confirmed_by", String(200), ForeignKey("users.subject"), nullable=False),
    Column("delivery_version", Integer, nullable=False),
    Column("correlation_id", String(100), nullable=False),
    Column("idempotency_key", String(200), nullable=False),
    Column("confirmed_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("confirmed_by", "idempotency_key", name="uq_delivery_confirmation_actor_key"),
)

delivery_confirmation_lines = Table(
    "delivery_confirmation_lines",
    metadata,
    Column("confirmation_line_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "confirmation_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_confirmations.confirmation_id"),
        nullable=False,
    ),
    Column(
        "delivery_line_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_lines.delivery_line_id"),
        nullable=False,
        unique=True,
    ),
    Column("line_id", PostgresUUID(as_uuid=True), nullable=False),
    Column("sku_id", PostgresUUID(as_uuid=True), ForeignKey("skus.sku_id"), nullable=False),
    Column("accepted_quantity_base", Numeric(18, 6), nullable=False),
    Column("unit_cost", Numeric(18, 6), nullable=False),
    Column("value_delta", Numeric(24, 6), nullable=False),
    Column(
        "outbound_movement_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("stock_movements.movement_id"),
        nullable=False,
        unique=True,
    ),
    UniqueConstraint(
        "confirmation_id",
        "delivery_line_id",
        name="uq_delivery_confirmation_delivery_line",
    ),
)

delivery_confirmation_evidence = Table(
    "delivery_confirmation_evidence",
    metadata,
    Column(
        "confirmation_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_confirmations.confirmation_id"),
        primary_key=True,
    ),
    Column(
        "evidence_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_evidence.evidence_id"),
        primary_key=True,
    ),
)

document_series = Table(
    "document_series",
    metadata,
    Column("document_series_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "branch_id", PostgresUUID(as_uuid=True), ForeignKey("branches.branch_id"), nullable=False
    ),
    Column("document_type", String(40), nullable=False),
    Column("prefix", String(30), nullable=False),
    Column("next_number", Integer, nullable=False, server_default="1"),
    UniqueConstraint("branch_id", "document_type", name="uq_document_series_branch_type"),
)

delivery_receipts = Table(
    "delivery_receipts",
    metadata,
    Column("delivery_receipt_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "confirmation_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_confirmations.confirmation_id"),
        nullable=False,
        unique=True,
    ),
    Column(
        "document_series_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("document_series.document_series_id"),
        nullable=False,
    ),
    Column(
        "branch_id", PostgresUUID(as_uuid=True), ForeignKey("branches.branch_id"), nullable=False
    ),
    Column("series_number", Integer, nullable=False),
    Column("number", String(80), nullable=False, unique=True),
    Column("snapshot", JSONB, nullable=False),
    Column("document_status", String(30), nullable=False, server_default="pending_document"),
    Column("document_object_key", String(500), nullable=True),
    Column("issued_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint(
        "document_series_id", "series_number", name="uq_delivery_receipt_series_number"
    ),
)

outbox_events = Table(
    "outbox_events",
    metadata,
    Column("outbox_event_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column("aggregate_type", String(50), nullable=False),
    Column("aggregate_id", PostgresUUID(as_uuid=True), nullable=False),
    Column("event_type", String(100), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("correlation_id", String(100), nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint(
        "aggregate_type", "aggregate_id", "event_type", name="uq_outbox_aggregate_event"
    ),
)

outbox_processing_state = Table(
    "outbox_processing_state",
    metadata,
    Column(
        "outbox_event_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("outbox_events.outbox_event_id"),
        primary_key=True,
    ),
    Column("status", String(30), nullable=False, server_default="pending"),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("available_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("last_error", String(2000), nullable=True),
    Column("processed_at", DateTime(timezone=True), nullable=True),
)

outbox_handler_receipts = Table(
    "outbox_handler_receipts",
    metadata,
    Column("outbox_handler_receipt_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "outbox_event_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("outbox_events.outbox_event_id"),
        nullable=False,
    ),
    Column("handler_name", String(100), nullable=False),
    Column("result_id", PostgresUUID(as_uuid=True), nullable=False),
    Column("processed_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("outbox_event_id", "handler_name", name="uq_outbox_handler_receipt"),
)

draft_invoices = Table(
    "draft_invoices",
    metadata,
    Column("draft_invoice_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "delivery_confirmation_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_confirmations.confirmation_id"),
        nullable=False,
        unique=True,
    ),
    Column(
        "source_event_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("outbox_events.outbox_event_id"),
        nullable=False,
        unique=True,
    ),
    Column("status", String(20), nullable=False),
    Column("sales_order_id", PostgresUUID(as_uuid=True), nullable=False),
    Column("sales_order_revision_id", PostgresUUID(as_uuid=True), nullable=False),
    Column(
        "customer_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("customer_accounts.customer_id"),
        nullable=False,
    ),
    Column(
        "branch_id", PostgresUUID(as_uuid=True), ForeignKey("branches.branch_id"), nullable=False
    ),
    Column("currency", String(3), nullable=False),
    Column("subtotal", Numeric(24, 6), nullable=False),
    Column("discount_total", Numeric(24, 6), nullable=False),
    Column("tax_total", Numeric(24, 6), nullable=False),
    Column("grand_total", Numeric(24, 6), nullable=False),
    Column("source_snapshot", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

draft_invoice_lines = Table(
    "draft_invoice_lines",
    metadata,
    Column("draft_invoice_line_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "draft_invoice_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("draft_invoices.draft_invoice_id"),
        nullable=False,
    ),
    Column("line_id", PostgresUUID(as_uuid=True), nullable=False),
    Column("sku_id", PostgresUUID(as_uuid=True), ForeignKey("skus.sku_id"), nullable=False),
    Column("accepted_quantity_base", Numeric(18, 6), nullable=False),
    Column("unit_price", Numeric(18, 6), nullable=False),
    Column("subtotal", Numeric(24, 6), nullable=False),
    Column("discount_amount", Numeric(24, 6), nullable=False),
    Column("tax_amount", Numeric(24, 6), nullable=False),
    Column("line_total", Numeric(24, 6), nullable=False),
    Column("calculation_snapshot", JSONB, nullable=False),
    UniqueConstraint("draft_invoice_id", "line_id", name="uq_draft_invoice_line"),
)
