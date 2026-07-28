from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
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
        "custody IN ('available', 'quarantine')",
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
    CheckConstraint("movement_type = 'opening_stock'", name="ck_stock_movements_type"),
    CheckConstraint("quantity_base > 0", name="ck_stock_movements_quantity_positive"),
    CheckConstraint("unit_cost >= 0", name="ck_stock_movements_cost_nonnegative"),
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
