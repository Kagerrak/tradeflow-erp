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

suppliers = Table(
    "suppliers",
    metadata,
    Column("supplier_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "company_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("companies.company_id"),
        nullable=False,
    ),
    Column("code", String(50), nullable=False),
    Column("legal_name", String(200), nullable=False),
    Column("tax_id", String(50), nullable=True),
    Column("payment_terms", String(50), nullable=False),
    Column("default_currency", String(3), nullable=False),
    Column("is_active", Boolean, nullable=False, server_default="true"),
    Column("version", Integer, nullable=False, server_default="1"),
    Column("created_by", String(200), ForeignKey("users.subject"), nullable=False),
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
        onupdate=func.now(),
    ),
    CheckConstraint("version > 0", name="ck_suppliers_version_positive"),
    UniqueConstraint("company_id", "code", name="uq_suppliers_company_code"),
)

purchase_orders = Table(
    "purchase_orders",
    metadata,
    Column("purchase_order_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "company_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("companies.company_id"),
        nullable=False,
    ),
    Column(
        "supplier_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("suppliers.supplier_id"),
        nullable=False,
    ),
    Column(
        "branch_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("branches.branch_id"),
        nullable=False,
    ),
    Column("code", String(50), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("exchange_rate", Numeric(18, 6), nullable=False, server_default="1"),
    Column("status", String(30), nullable=False, server_default="draft"),
    Column("version", Integer, nullable=False, server_default="1"),
    Column("created_by", String(200), ForeignKey("users.subject"), nullable=False),
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
        onupdate=func.now(),
    ),
    CheckConstraint(
        "status IN ('draft', 'approved', 'partially_received', 'received', 'closed')",
        name="ck_purchase_orders_status",
    ),
    CheckConstraint("version > 0", name="ck_purchase_orders_version_positive"),
    CheckConstraint("exchange_rate > 0", name="ck_purchase_orders_exchange_rate_positive"),
    UniqueConstraint("company_id", "code", name="uq_purchase_orders_company_code"),
)

purchase_order_lines = Table(
    "purchase_order_lines",
    metadata,
    Column("purchase_order_line_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "purchase_order_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("purchase_orders.purchase_order_id"),
        nullable=False,
    ),
    Column(
        "sku_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("skus.sku_id"),
        nullable=False,
    ),
    Column("line_number", Integer, nullable=False),
    Column("requested_quantity", Numeric(18, 6), nullable=False),
    Column("unit_code", String(30), nullable=False),
    Column("base_quantity", Numeric(18, 6), nullable=False),
    Column("received_quantity_base", Numeric(18, 6), nullable=False, server_default="0"),
    Column("unit_cost", Numeric(18, 6), nullable=False),
    Column("version", Integer, nullable=False, server_default="1"),
    CheckConstraint("line_number > 0", name="ck_purchase_order_lines_line_number_positive"),
    CheckConstraint(
        "requested_quantity > 0",
        name="ck_purchase_order_lines_requested_quantity_positive",
    ),
    CheckConstraint("base_quantity > 0", name="ck_purchase_order_lines_base_quantity_positive"),
    CheckConstraint("unit_cost >= 0", name="ck_purchase_order_lines_unit_cost_positive"),
    CheckConstraint(
        "received_quantity_base >= 0",
        name="ck_purchase_order_lines_received_quantity_base_nonnegative",
    ),
    CheckConstraint("version > 0", name="ck_purchase_order_lines_version_positive"),
    UniqueConstraint(
        "purchase_order_id",
        "line_number",
        name="uq_purchase_order_lines_order_line",
    ),
)

goods_receipts = Table(
    "goods_receipts",
    metadata,
    Column("goods_receipt_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "purchase_order_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("purchase_orders.purchase_order_id"),
        nullable=False,
    ),
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
    Column("receipt_number", String(50), nullable=False),
    Column("status", String(30), nullable=False, server_default="posted"),
    Column("correlation_id", String(100), nullable=False),
    Column("idempotency_key", String(200), nullable=False, unique=True),
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
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    ),
    CheckConstraint(
        "status IN ('posted', 'reversed')",
        name="ck_goods_receipts_status",
    ),
    UniqueConstraint(
        "purchase_order_id",
        "receipt_number",
        name="uq_goods_receipts_purchase_order_receipt_number",
    ),
)

goods_receipt_lines = Table(
    "goods_receipt_lines",
    metadata,
    Column(
        "goods_receipt_line_id",
        PostgresUUID(as_uuid=True),
        primary_key=True,
    ),
    Column(
        "goods_receipt_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("goods_receipts.goods_receipt_id"),
        nullable=False,
    ),
    Column(
        "purchase_order_line_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("purchase_order_lines.purchase_order_line_id"),
        nullable=False,
    ),
    Column("received_quantity_base", Numeric(18, 6), nullable=False),
    Column("lot_code", String(100), nullable=True),
    Column("serial_numbers", JSONB, nullable=False, server_default="[]"),
    CheckConstraint(
        "received_quantity_base > 0",
        name="ck_goods_receipt_lines_received_quantity_positive",
    ),
)

landed_cost_charges = Table(
    "landed_cost_charges",
    metadata,
    Column(
        "landed_cost_charge_id",
        PostgresUUID(as_uuid=True),
        primary_key=True,
    ),
    Column(
        "goods_receipt_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("goods_receipts.goods_receipt_id"),
        nullable=False,
    ),
    Column("charge_type", String(50), nullable=False),
    Column("amount_base", Numeric(18, 6), nullable=False),
    Column("base_currency", String(3), nullable=False),
    Column("correlation_id", String(100), nullable=False),
    Column("idempotency_key", String(200), nullable=False, unique=True),
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
        "charge_type IN ('freight', 'insurance', 'customs', 'brokerage', 'handling')",
        name="ck_landed_cost_charges_charge_type",
    ),
    CheckConstraint(
        "amount_base > 0",
        name="ck_landed_cost_charges_amount_positive",
    ),
)

landed_cost_allocations = Table(
    "landed_cost_allocations",
    metadata,
    Column(
        "landed_cost_allocation_id",
        PostgresUUID(as_uuid=True),
        primary_key=True,
    ),
    Column(
        "landed_cost_charge_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("landed_cost_charges.landed_cost_charge_id"),
        nullable=False,
    ),
    Column(
        "goods_receipt_line_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("goods_receipt_lines.goods_receipt_line_id"),
        nullable=False,
    ),
    Column("allocated_amount_base", Numeric(18, 6), nullable=False),
    CheckConstraint(
        "allocated_amount_base > 0",
        name="ck_landed_cost_allocations_amount_positive",
    ),
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
    Column(
        "warehouse_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("warehouses.warehouse_id"),
        nullable=True,
    ),
    Column("maximum_amount", Numeric(18, 2), nullable=True),
    Column("maximum_percentage", Numeric(9, 6), nullable=True),
    Column(
        "maker_checker_required",
        Boolean,
        nullable=False,
        server_default="true",
    ),
)

Index(
    "uq_approval_authority_branch",
    approval_authorities.c.user_subject,
    approval_authorities.c.capability_code,
    approval_authorities.c.branch_id,
    unique=True,
    postgresql_where=approval_authorities.c.warehouse_id.is_(None),
)

Index(
    "uq_approval_authority_warehouse",
    approval_authorities.c.user_subject,
    approval_authorities.c.capability_code,
    approval_authorities.c.branch_id,
    approval_authorities.c.warehouse_id,
    unique=True,
    postgresql_where=approval_authorities.c.warehouse_id.is_not(None),
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
        "custody IN ('available', 'quarantine', 'dispatch_staging', 'in_transit', 'investigation')",
        name="ck_warehouse_stock_locations_custody",
    ),
    UniqueConstraint("warehouse_id", "code", name="uq_warehouse_stock_location_code"),
)
Index(
    "uq_warehouse_active_dispatch_staging",
    warehouse_stock_locations.c.warehouse_id,
    unique=True,
    postgresql_where=(warehouse_stock_locations.c.custody == "dispatch_staging")
    & warehouse_stock_locations.c.is_active,
)
Index(
    "uq_warehouse_active_in_transit",
    warehouse_stock_locations.c.warehouse_id,
    unique=True,
    postgresql_where=(warehouse_stock_locations.c.custody == "in_transit")
    & warehouse_stock_locations.c.is_active,
)
Index(
    "uq_warehouse_active_investigation",
    warehouse_stock_locations.c.warehouse_id,
    unique=True,
    postgresql_where=(warehouse_stock_locations.c.custody == "investigation")
    & warehouse_stock_locations.c.is_active,
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
    Column("movement_leg", String(64), nullable=False),
    Column(
        "reversal_of_movement_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("stock_movements.movement_id"),
        nullable=True,
    ),
    CheckConstraint(
        "movement_type IN ('opening_stock', 'pick', 'pick_reversal', 'dispatch', "
        "'delivery_confirmation', 'delivery_exception', 'return_to_warehouse', "
        "'investigation_resolution', 'delivery_correction', 'goods_receipt', 'transfer')",
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
        "AND movement_leg = 'delivery_outbound') "
        "OR (movement_type = 'delivery_exception' "
        "AND movement_leg IN ('exception_transit_out', 'exception_investigation_in')) "
        "OR (movement_type = 'return_to_warehouse' "
        "AND movement_leg IN ('return_transit_out', 'return_quarantine_in')) "
        "OR (movement_type = 'investigation_resolution' "
        "AND movement_leg IN ('recovery_investigation_out', 'recovery_quarantine_in', "
        "'carrier_claim_investigation_out', 'inventory_adjustment_investigation_out')) "
        "OR (movement_type = 'delivery_correction' AND movement_leg IN "
        "('correction_accepted_reversal_in', "
        "'correction_exception_reversal_transit_in', "
        "'correction_exception_reversal_investigation_out', "
        "'correction_accepted_replacement_out', "
        "'correction_exception_replacement_transit_out', "
        "'correction_exception_replacement_investigation_in')) "
        "OR (movement_type = 'goods_receipt' "
        "AND movement_leg = 'goods_receipt_in') "
        "OR (movement_type = 'transfer' "
        "AND movement_leg IN ('transfer_source_out', 'transfer_in_transit_in', "
        "'transfer_in_transit_out', 'transfer_destination_in'))",
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

inventory_transfers = Table(
    "inventory_transfers",
    metadata,
    Column("transfer_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column("sku_id", PostgresUUID(as_uuid=True), ForeignKey("skus.sku_id"), nullable=False),
    Column(
        "from_warehouse_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("warehouses.warehouse_id"),
        nullable=False,
    ),
    Column(
        "to_warehouse_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("warehouses.warehouse_id"),
        nullable=False,
    ),
    Column(
        "from_location_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("warehouse_stock_locations.location_id"),
        nullable=False,
    ),
    Column(
        "to_location_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("warehouse_stock_locations.location_id"),
        nullable=False,
    ),
    Column("quantity_base", Numeric(18, 6), nullable=False),
    Column("unit_cost", Numeric(18, 6), nullable=False),
    Column("base_currency", String(3), nullable=False),
    Column("status", String(20), nullable=False, server_default="released"),
    Column("version", Integer, nullable=False, server_default="1"),
    Column("reason", String(500), nullable=False),
    Column("source_reference", String(100), nullable=False),
    Column("lot_code", String(100), nullable=True),
    Column("requested_by", String(200), ForeignKey("users.subject"), nullable=False),
    Column("requested_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("received_by", String(200), ForeignKey("users.subject"), nullable=True),
    Column("received_at", DateTime(timezone=True), nullable=True),
    Column("release_movement_group_id", PostgresUUID(as_uuid=True), nullable=False),
    Column("receive_movement_group_id", PostgresUUID(as_uuid=True), nullable=True),
    Column("correlation_id", String(100), nullable=False),
    Column("idempotency_key", String(200), nullable=False, unique=True),
    CheckConstraint(
        "status IN ('released', 'received')",
        name="ck_inventory_transfers_status",
    ),
    CheckConstraint("version > 0", name="ck_inventory_transfers_version"),
    CheckConstraint("quantity_base > 0", name="ck_inventory_transfers_quantity"),
    CheckConstraint("btrim(reason) <> ''", name="ck_inventory_transfers_reason"),
    CheckConstraint(
        "unit_cost >= 0",
        name="ck_inventory_transfers_unit_cost",
    ),
    CheckConstraint(
        "(status = 'released' AND received_by IS NULL AND received_at IS NULL "
        "AND receive_movement_group_id IS NULL) "
        "OR (status = 'received' AND received_by IS NOT NULL AND received_at IS NOT NULL "
        "AND receive_movement_group_id IS NOT NULL)",
        name="ck_inventory_transfers_received_shape",
    ),
    Index(
        "ix_inventory_transfers_sku_from",
        "sku_id",
        "from_warehouse_id",
        "status",
    ),
    Index(
        "ix_inventory_transfers_sku_to",
        "sku_id",
        "to_warehouse_id",
        "status",
    ),
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
        "'picked', 'partially_dispatched', 'dispatched', 'partially_delivered', 'delivered', "
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

payment_allocations = Table(
    "payment_allocations",
    metadata,
    Column("allocation_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "payment_receipt_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("payment_receipts.payment_receipt_id"),
        nullable=False,
    ),
    Column(
        "invoice_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("draft_invoices.draft_invoice_id"),
        nullable=False,
    ),
    Column("amount", Numeric(24, 6), nullable=False),
    Column("currency", String(3), nullable=False),
    Column(
        "branch_id", PostgresUUID(as_uuid=True), ForeignKey("branches.branch_id"), nullable=False
    ),
    Column("actor_subject", String(200), ForeignKey("users.subject"), nullable=False),
    Column("correlation_id", String(100), nullable=False),
    Column("idempotency_key", String(200), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("amount > 0", name="ck_payment_allocation_amount_positive"),
    Index("idx_payment_allocations_receipt", "payment_receipt_id"),
    Index("idx_payment_allocations_invoice", "invoice_id"),
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

cash_reconciliation_events = Table(
    "cash_reconciliation_events",
    metadata,
    Column("cash_reconciliation_event_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "payment_receipt_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("payment_receipts.payment_receipt_id"),
        nullable=False,
    ),
    Column("cash_reconciliation_id", PostgresUUID(as_uuid=True), nullable=False),
    Column("event_type", String(20), nullable=False),
    Column("expected_amount", Numeric(24, 6), nullable=False),
    Column("counted_amount", Numeric(24, 6), nullable=False),
    Column("variance_amount", Numeric(24, 6), nullable=False),
    Column("reason", String(500), nullable=False),
    Column("actor_subject", String(200), ForeignKey("users.subject"), nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("idempotency_key", String(200), nullable=False, unique=True),
    CheckConstraint(
        "event_type IN ('reconciled','adjusted','reversed')",
        name="ck_cash_reconciliation_events_type",
    ),
    CheckConstraint(
        "expected_amount >= 0 AND counted_amount >= 0 "
        "AND variance_amount = counted_amount - expected_amount",
        name="ck_cash_reconciliation_events_amounts",
    ),
)

cod_collections = Table(
    "cod_collections",
    metadata,
    Column(
        "confirmation_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_confirmations.confirmation_id"),
        primary_key=True,
    ),
    Column(
        "delivery_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_dispatches.delivery_id"),
        nullable=False,
        unique=True,
    ),
    Column(
        "payment_receipt_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("payment_receipts.payment_receipt_id"),
        nullable=False,
        unique=True,
    ),
    Column("amount_due", Numeric(24, 6), nullable=False),
    Column("amount_collected", Numeric(24, 6), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("status", String(30), nullable=False),
    Column("collected_by", String(200), ForeignKey("users.subject"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "amount_due > 0 AND amount_collected >= amount_due",
        name="ck_cod_collections_sufficient",
    ),
    CheckConstraint(
        "status IN ('captured','pending_verification','cleared','reconciled','reversed')",
        name="ck_cod_collections_status",
    ),
    ForeignKeyConstraint(
        ["delivery_id", "confirmation_id"],
        ["delivery_confirmations.delivery_id", "delivery_confirmations.confirmation_id"],
        name="fk_cod_collection_confirmation_delivery",
    ),
)

cod_on_account_conversions = Table(
    "cod_on_account_conversions",
    metadata,
    Column("conversion_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "delivery_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_dispatches.delivery_id"),
        nullable=False,
        unique=True,
    ),
    Column(
        "confirmation_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_confirmations.confirmation_id"),
        nullable=True,
        unique=True,
    ),
    Column(
        "commercial_approval_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("commercial_approvals.commercial_approval_id"),
        nullable=False,
    ),
    Column("amount", Numeric(24, 6), nullable=False),
    Column("consumed_amount", Numeric(24, 6), nullable=False, server_default="0"),
    Column("currency", String(3), nullable=False),
    Column("open_balance_snapshot", Numeric(24, 6), nullable=False),
    Column("approved_uninvoiced_snapshot", Numeric(24, 6), nullable=False),
    Column("credit_limit_snapshot", Numeric(24, 6), nullable=True),
    Column("credit_excess_approved", Numeric(24, 6), nullable=False),
    Column("reason", String(500), nullable=False),
    Column("approved_by", String(200), ForeignKey("users.subject"), nullable=False),
    Column("status", String(20), nullable=False),
    Column("correlation_id", String(100), nullable=False),
    Column("idempotency_key", String(200), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "amount > 0 AND open_balance_snapshot >= 0 "
        "AND approved_uninvoiced_snapshot >= 0 AND credit_excess_approved >= 0",
        name="ck_cod_on_account_conversion_amounts",
    ),
    CheckConstraint(
        "consumed_amount >= 0 AND consumed_amount <= amount",
        name="ck_cod_conversion_consumed_amount",
    ),
    CheckConstraint(
        "status IN ('approved','consumed','reversed')",
        name="ck_cod_on_account_conversion_status",
    ),
    CheckConstraint(
        "(status = 'consumed' AND confirmation_id IS NOT NULL) "
        "OR (status IN ('approved','reversed') AND confirmation_id IS NULL)",
        name="ck_cod_on_account_conversion_confirmation",
    ),
    ForeignKeyConstraint(
        ["delivery_id", "confirmation_id"],
        ["delivery_confirmations.delivery_id", "delivery_confirmations.confirmation_id"],
        name="fk_cod_conversion_confirmation_delivery",
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
    Column("dispatch_kind", String(20), nullable=False, server_default="initial"),
    Column(
        "parent_delivery_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_dispatches.delivery_id"),
        nullable=True,
    ),
    Column("dispatched_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "payment_timing_policy IN ('prepaid', 'cash_on_delivery', 'on_account')",
        name="ck_delivery_dispatches_payment_timing",
    ),
    CheckConstraint(
        "(dispatch_kind = 'initial' AND parent_delivery_id IS NULL) "
        "OR (dispatch_kind = 'retry' AND parent_delivery_id IS NOT NULL)",
        name="ck_delivery_dispatch_kind",
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
    ),
    Column("line_id", PostgresUUID(as_uuid=True), nullable=False),
    Column("sku_id", PostgresUUID(as_uuid=True), ForeignKey("skus.sku_id"), nullable=False),
    Column("quantity_base", Numeric(18, 6), nullable=False),
    Column("movement_group_id", PostgresUUID(as_uuid=True), nullable=False, unique=True),
    Column(
        "staging_movement_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("stock_movements.movement_id"),
        nullable=True,
    ),
    Column(
        "transit_movement_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("stock_movements.movement_id"),
        nullable=True,
    ),
    Column(
        "source_exception_case_id",
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "delivery_exception_cases.exception_case_id",
            name="fk_delivery_line_source_exception",
            use_alter=True,
        ),
        nullable=True,
    ),
    CheckConstraint("quantity_base > 0", name="ck_delivery_lines_quantity"),
    UniqueConstraint("delivery_id", "pick_line_id", name="uq_delivery_line_pick"),
)
Index(
    "uq_initial_delivery_line_pick",
    delivery_lines.c.pick_line_id,
    unique=True,
    postgresql_where=delivery_lines.c.source_exception_case_id.is_(None),
)
Index(
    "uq_initial_delivery_line_staging_movement",
    delivery_lines.c.staging_movement_id,
    unique=True,
    postgresql_where=delivery_lines.c.staging_movement_id.is_not(None),
)
Index(
    "uq_initial_delivery_line_transit_movement",
    delivery_lines.c.transit_movement_id,
    unique=True,
    postgresql_where=delivery_lines.c.transit_movement_id.is_not(None),
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
    Column("upload_id", String(500), nullable=True),
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
    UniqueConstraint(
        "delivery_id", "confirmation_id", name="uq_delivery_confirmation_delivery_identity"
    ),
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
    Column("refused_quantity_base", Numeric(18, 6), nullable=False, server_default="0"),
    Column("damaged_quantity_base", Numeric(18, 6), nullable=False, server_default="0"),
    Column("short_missing_quantity_base", Numeric(18, 6), nullable=False, server_default="0"),
    Column("still_undelivered_quantity_base", Numeric(18, 6), nullable=False, server_default="0"),
    Column("unit_cost", Numeric(18, 6), nullable=False),
    Column("value_delta", Numeric(24, 6), nullable=False),
    Column(
        "outbound_movement_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("stock_movements.movement_id"),
        nullable=True,
        unique=True,
    ),
    UniqueConstraint(
        "confirmation_id",
        "delivery_line_id",
        name="uq_delivery_confirmation_delivery_line",
    ),
    CheckConstraint(
        "accepted_quantity_base >= 0 AND refused_quantity_base >= 0 "
        "AND damaged_quantity_base >= 0 AND short_missing_quantity_base >= 0 "
        "AND still_undelivered_quantity_base >= 0 "
        "AND accepted_quantity_base + refused_quantity_base + damaged_quantity_base "
        "+ short_missing_quantity_base + still_undelivered_quantity_base > 0",
        name="ck_delivery_confirmation_line_partition",
    ),
    CheckConstraint(
        "unit_cost >= 0 AND value_delta <= 0 "
        "AND ((accepted_quantity_base = 0 AND outbound_movement_id IS NULL "
        "AND value_delta = 0) OR (accepted_quantity_base > 0 "
        "AND outbound_movement_id IS NOT NULL))",
        name="ck_delivery_confirmation_line_value",
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

delivery_line_identity_allocations = Table(
    "delivery_line_identity_allocations",
    metadata,
    Column("allocation_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "delivery_line_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_lines.delivery_line_id"),
        nullable=False,
    ),
    Column(
        "pick_identity_assignment_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("pick_identity_assignments.pick_identity_assignment_id"),
        nullable=False,
    ),
    Column("quantity_base", Numeric(18, 6), nullable=False),
    CheckConstraint("quantity_base > 0", name="ck_delivery_line_identity_allocation_quantity"),
    UniqueConstraint(
        "delivery_line_id",
        "pick_identity_assignment_id",
        name="uq_delivery_line_identity_allocation",
    ),
)

stock_movement_identity_allocations = Table(
    "stock_movement_identity_allocations",
    metadata,
    Column("allocation_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "movement_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("stock_movements.movement_id"),
        nullable=False,
    ),
    Column(
        "delivery_line_identity_allocation_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_line_identity_allocations.allocation_id"),
        nullable=False,
    ),
    Column("quantity_base", Numeric(18, 6), nullable=False),
    CheckConstraint("quantity_base > 0", name="ck_stock_movement_identity_allocation_quantity"),
    UniqueConstraint(
        "movement_id",
        "delivery_line_identity_allocation_id",
        name="uq_stock_movement_identity_allocation",
    ),
)

delivery_confirmation_identity_partitions = Table(
    "delivery_confirmation_identity_partitions",
    metadata,
    Column("partition_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "confirmation_line_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_confirmation_lines.confirmation_line_id"),
        nullable=False,
    ),
    Column(
        "delivery_line_identity_allocation_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_line_identity_allocations.allocation_id"),
        nullable=False,
    ),
    Column("accepted_quantity_base", Numeric(18, 6), nullable=False, server_default="0"),
    Column("refused_quantity_base", Numeric(18, 6), nullable=False, server_default="0"),
    Column("damaged_quantity_base", Numeric(18, 6), nullable=False, server_default="0"),
    Column("short_missing_quantity_base", Numeric(18, 6), nullable=False, server_default="0"),
    Column("still_undelivered_quantity_base", Numeric(18, 6), nullable=False, server_default="0"),
    CheckConstraint(
        "accepted_quantity_base >= 0 AND refused_quantity_base >= 0 "
        "AND damaged_quantity_base >= 0 AND short_missing_quantity_base >= 0 "
        "AND still_undelivered_quantity_base >= 0",
        name="ck_delivery_identity_partition_nonnegative",
    ),
    UniqueConstraint(
        "confirmation_line_id",
        "delivery_line_identity_allocation_id",
        name="uq_delivery_confirmation_identity_partition",
    ),
)

delivery_exception_cases = Table(
    "delivery_exception_cases",
    metadata,
    Column("exception_case_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "confirmation_line_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_confirmation_lines.confirmation_line_id"),
        nullable=False,
    ),
    Column(
        "correction_line_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_correction_lines.correction_line_id", use_alter=True),
        nullable=True,
    ),
    Column("exception_kind", String(30), nullable=False),
    Column("original_quantity_base", Numeric(18, 6), nullable=False),
    Column("initial_custody", String(30), nullable=False),
    Column("responsible_party_type", String(20), nullable=False),
    Column("responsible_subject", String(200), nullable=True),
    Column("responsible_snapshot", JSONB, nullable=False, server_default="{}"),
    Column("investigation_movement_group_id", PostgresUUID(as_uuid=True), nullable=True),
    Column(
        "investigation_out_movement_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("stock_movements.movement_id"),
        nullable=True,
    ),
    Column(
        "investigation_in_movement_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("stock_movements.movement_id"),
        nullable=True,
    ),
    Column("opened_by", String(200), ForeignKey("users.subject"), nullable=False),
    Column("correlation_id", String(100), nullable=False),
    Column("opened_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "exception_kind IN ('refused', 'damaged', 'short_missing', 'still_undelivered')",
        name="ck_delivery_exception_case_kind",
    ),
    CheckConstraint("original_quantity_base > 0", name="ck_delivery_exception_original_quantity"),
    CheckConstraint(
        "initial_custody IN ('in_transit', 'investigation')",
        name="ck_delivery_exception_initial_custody",
    ),
    CheckConstraint(
        "responsible_party_type IN ('staff', 'carrier', 'customer', 'unknown')",
        name="ck_delivery_exception_responsible_party",
    ),
)
Index(
    "uq_delivery_exception_original_case_kind",
    delivery_exception_cases.c.confirmation_line_id,
    delivery_exception_cases.c.exception_kind,
    unique=True,
    postgresql_where=delivery_exception_cases.c.correction_line_id.is_(None),
)
Index(
    "uq_delivery_exception_correction_case_kind",
    delivery_exception_cases.c.correction_line_id,
    delivery_exception_cases.c.exception_kind,
    unique=True,
    postgresql_where=delivery_exception_cases.c.correction_line_id.is_not(None),
)

delivery_exception_case_evidence = Table(
    "delivery_exception_case_evidence",
    metadata,
    Column(
        "exception_case_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_exception_cases.exception_case_id"),
        primary_key=True,
    ),
    Column(
        "evidence_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_evidence.evidence_id"),
        primary_key=True,
    ),
)

delivery_exception_events = Table(
    "delivery_exception_events",
    metadata,
    Column("exception_event_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "exception_case_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_exception_cases.exception_case_id"),
        nullable=False,
    ),
    Column("event_type", String(40), nullable=False),
    Column("quantity_base", Numeric(18, 6), nullable=False),
    Column("source_document_type", String(50), nullable=False),
    Column("source_document_id", PostgresUUID(as_uuid=True), nullable=False),
    Column("from_custody", String(30), nullable=True),
    Column("to_custody", String(30), nullable=True),
    Column("reason", String(500), nullable=True),
    Column("approved_by", String(200), ForeignKey("users.subject"), nullable=True),
    Column(
        "approval_authority_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("approval_authorities.approval_authority_id"),
        nullable=True,
    ),
    Column("movement_group_id", PostgresUUID(as_uuid=True), nullable=True),
    Column("actor_subject", String(200), ForeignKey("users.subject"), nullable=False),
    Column("correlation_id", String(100), nullable=False),
    Column("idempotency_key", String(200), nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "event_type IN ('opened', 'return_received', 'retry_allocated', 'recovered', "
        "'carrier_claim_resolved', 'inventory_adjustment_resolved', "
        "'superseded_by_correction')",
        name="ck_delivery_exception_event_type",
    ),
    CheckConstraint("quantity_base > 0", name="ck_delivery_exception_event_quantity"),
    UniqueConstraint(
        "actor_subject",
        "idempotency_key",
        "exception_case_id",
        "event_type",
        name="uq_delivery_exception_event_actor_key",
    ),
)

delivery_exception_event_evidence = Table(
    "delivery_exception_event_evidence",
    metadata,
    Column(
        "exception_event_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_exception_events.exception_event_id"),
        primary_key=True,
    ),
    Column(
        "evidence_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_evidence.evidence_id"),
        primary_key=True,
    ),
)

delivery_exception_state = Table(
    "delivery_exception_state",
    metadata,
    Column(
        "exception_case_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_exception_cases.exception_case_id"),
        primary_key=True,
    ),
    Column("status", String(30), nullable=False),
    Column("custody", String(30), nullable=False),
    Column("open_quantity_base", Numeric(18, 6), nullable=False),
    Column("returned_quantity_base", Numeric(18, 6), nullable=False, server_default="0"),
    Column("retry_allocated_quantity_base", Numeric(18, 6), nullable=False, server_default="0"),
    Column("resolved_quantity_base", Numeric(18, 6), nullable=False, server_default="0"),
    Column("version", Integer, nullable=False, server_default="1"),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "status IN ('open', 'partially_resolved', 'resolved')",
        name="ck_delivery_exception_state_status",
    ),
    CheckConstraint(
        "custody IN ('in_transit', 'investigation', 'quarantine', 'outbound')",
        name="ck_delivery_exception_state_custody",
    ),
    CheckConstraint(
        "open_quantity_base >= 0 AND returned_quantity_base >= 0 "
        "AND retry_allocated_quantity_base >= 0 AND resolved_quantity_base >= 0",
        name="ck_delivery_exception_state_quantities",
    ),
    CheckConstraint("version > 0", name="ck_delivery_exception_state_version"),
)

return_to_warehouse_receipts = Table(
    "return_to_warehouse_receipts",
    metadata,
    Column("receipt_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "delivery_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_dispatches.delivery_id"),
        nullable=False,
    ),
    Column(
        "warehouse_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("warehouses.warehouse_id"),
        nullable=False,
    ),
    Column("received_by", String(200), ForeignKey("users.subject"), nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
    Column("notes", String(2000), nullable=True),
    Column("correlation_id", String(100), nullable=False),
    Column("idempotency_key", String(200), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("received_by", "idempotency_key", name="uq_return_receipt_actor_key"),
)

return_to_warehouse_receipt_lines = Table(
    "return_to_warehouse_receipt_lines",
    metadata,
    Column("receipt_line_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "receipt_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("return_to_warehouse_receipts.receipt_id"),
        nullable=False,
    ),
    Column(
        "exception_case_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_exception_cases.exception_case_id"),
        nullable=False,
    ),
    Column("quantity_base", Numeric(18, 6), nullable=False),
    Column(
        "transit_out_movement_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("stock_movements.movement_id"),
        nullable=False,
        unique=True,
    ),
    Column(
        "quarantine_in_movement_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("stock_movements.movement_id"),
        nullable=False,
        unique=True,
    ),
    CheckConstraint("quantity_base > 0", name="ck_return_receipt_line_quantity"),
    UniqueConstraint("receipt_id", "exception_case_id", name="uq_return_receipt_case"),
)

investigation_resolutions = Table(
    "investigation_resolutions",
    metadata,
    Column("resolution_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "exception_case_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_exception_cases.exception_case_id"),
        nullable=False,
    ),
    Column("resolution_type", String(30), nullable=False),
    Column("quantity_base", Numeric(18, 6), nullable=False),
    Column("reason", String(500), nullable=False),
    Column("external_reference", String(200), nullable=True),
    Column("approved_by", String(200), ForeignKey("users.subject"), nullable=False),
    Column(
        "approval_authority_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("approval_authorities.approval_authority_id"),
        nullable=True,
    ),
    Column("movement_group_id", PostgresUUID(as_uuid=True), nullable=False),
    Column(
        "investigation_out_movement_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("stock_movements.movement_id"),
        nullable=False,
        unique=True,
    ),
    Column(
        "quarantine_in_movement_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("stock_movements.movement_id"),
        nullable=True,
        unique=True,
    ),
    Column("correlation_id", String(100), nullable=False),
    Column("idempotency_key", String(200), nullable=False),
    Column("resolved_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "resolution_type IN ('recovery', 'carrier_claim', 'inventory_adjustment')",
        name="ck_investigation_resolution_type",
    ),
    CheckConstraint("quantity_base > 0", name="ck_investigation_resolution_quantity"),
    UniqueConstraint(
        "approved_by", "idempotency_key", name="uq_investigation_resolution_actor_key"
    ),
)

delivery_retry_allocations = Table(
    "delivery_retry_allocations",
    metadata,
    Column("retry_allocation_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "source_exception_case_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_exception_cases.exception_case_id"),
        nullable=False,
    ),
    Column(
        "retry_delivery_line_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_lines.delivery_line_id"),
        nullable=False,
        unique=True,
    ),
    Column("quantity_base", Numeric(18, 6), nullable=False),
    Column("allocated_by", String(200), ForeignKey("users.subject"), nullable=False),
    Column("reason", String(500), nullable=False),
    Column("correlation_id", String(100), nullable=False),
    Column("idempotency_key", String(200), nullable=False),
    Column("allocated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("quantity_base > 0", name="ck_delivery_retry_allocation_quantity"),
    UniqueConstraint(
        "allocated_by",
        "idempotency_key",
        "source_exception_case_id",
        name="uq_delivery_retry_allocation_actor_key",
    ),
)
Index(
    "ix_delivery_exception_queue",
    delivery_exception_state.c.status,
    delivery_exception_state.c.custody,
    delivery_exception_state.c.updated_at,
)

delivery_corrections = Table(
    "delivery_corrections",
    metadata,
    Column("correction_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "original_delivery_receipt_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_receipts.delivery_receipt_id", use_alter=True),
        nullable=False,
        unique=True,
    ),
    Column(
        "delivery_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_dispatches.delivery_id"),
        nullable=False,
    ),
    Column(
        "confirmation_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_confirmations.confirmation_id"),
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
    Column("reason", String(500), nullable=False),
    Column("requested_by", String(200), ForeignKey("users.subject"), nullable=False),
    Column("correlation_id", String(100), nullable=False),
    Column("idempotency_key", String(200), nullable=False),
    Column("base_currency", String(3), nullable=False),
    Column("affected_inventory_value", Numeric(24, 6), nullable=False),
    Column("affected_draft_invoice_value", Numeric(24, 6), nullable=False),
    Column("affected_value_base_currency", Numeric(24, 6), nullable=False),
    Column(
        "original_draft_invoice_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("draft_invoices.draft_invoice_id", use_alter=True),
        nullable=False,
    ),
    Column("reversal_draft_invoice_id", PostgresUUID(as_uuid=True), nullable=False, unique=True),
    Column("replacement_draft_invoice_id", PostgresUUID(as_uuid=True), nullable=True, unique=True),
    Column("requested_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("sealed_at", DateTime(timezone=True), nullable=True),
    UniqueConstraint("requested_by", "idempotency_key", name="uq_delivery_correction_actor_key"),
    CheckConstraint("btrim(reason) <> ''", name="ck_delivery_correction_reason"),
    CheckConstraint(
        "affected_inventory_value >= 0 AND affected_draft_invoice_value >= 0 "
        "AND affected_value_base_currency = greatest(affected_inventory_value, "
        "affected_draft_invoice_value)",
        name="ck_delivery_correction_affected_value",
    ),
)

delivery_correction_lines = Table(
    "delivery_correction_lines",
    metadata,
    Column("correction_line_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "correction_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_corrections.correction_id"),
        nullable=False,
    ),
    Column(
        "confirmation_line_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_confirmation_lines.confirmation_line_id"),
        nullable=False,
    ),
    Column(
        "delivery_line_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_lines.delivery_line_id"),
        nullable=False,
    ),
    Column("line_id", PostgresUUID(as_uuid=True), nullable=False),
    Column("sku_id", PostgresUUID(as_uuid=True), ForeignKey("skus.sku_id"), nullable=False),
    Column("accepted_quantity_base", Numeric(18, 6), nullable=False),
    Column("refused_quantity_base", Numeric(18, 6), nullable=False),
    Column("damaged_quantity_base", Numeric(18, 6), nullable=False),
    Column("short_missing_quantity_base", Numeric(18, 6), nullable=False),
    Column("still_undelivered_quantity_base", Numeric(18, 6), nullable=False),
    Column("unit_cost", Numeric(18, 6), nullable=False),
    Column("value_delta", Numeric(24, 6), nullable=False),
    UniqueConstraint("correction_id", "delivery_line_id", name="uq_delivery_correction_line"),
    UniqueConstraint(
        "correction_line_id", "confirmation_line_id", name="uq_correction_line_source"
    ),
    CheckConstraint(
        "accepted_quantity_base >= 0 AND refused_quantity_base >= 0 "
        "AND damaged_quantity_base >= 0 AND short_missing_quantity_base >= 0 "
        "AND still_undelivered_quantity_base >= 0",
        name="ck_delivery_correction_line_nonnegative",
    ),
    CheckConstraint(
        "unit_cost >= 0 AND value_delta <= 0",
        name="ck_delivery_correction_line_value",
    ),
)

delivery_correction_identity_positions = Table(
    "delivery_correction_identity_positions",
    metadata,
    Column("correction_identity_position_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "correction_line_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_correction_lines.correction_line_id"),
        nullable=False,
    ),
    Column(
        "delivery_line_identity_allocation_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_line_identity_allocations.allocation_id"),
        nullable=False,
    ),
    Column("accepted_quantity_base", Numeric(18, 6), nullable=False, server_default="0"),
    Column("refused_quantity_base", Numeric(18, 6), nullable=False, server_default="0"),
    Column("damaged_quantity_base", Numeric(18, 6), nullable=False, server_default="0"),
    Column("short_missing_quantity_base", Numeric(18, 6), nullable=False, server_default="0"),
    Column("still_undelivered_quantity_base", Numeric(18, 6), nullable=False, server_default="0"),
    UniqueConstraint(
        "correction_line_id",
        "delivery_line_identity_allocation_id",
        name="uq_delivery_correction_identity_position",
    ),
    CheckConstraint(
        "accepted_quantity_base >= 0 AND refused_quantity_base >= 0 "
        "AND damaged_quantity_base >= 0 AND short_missing_quantity_base >= 0 "
        "AND still_undelivered_quantity_base >= 0",
        name="ck_delivery_correction_identity_nonnegative",
    ),
)

delivery_correction_evidence = Table(
    "delivery_correction_evidence",
    metadata,
    Column(
        "correction_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_corrections.correction_id"),
        primary_key=True,
    ),
    Column(
        "evidence_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_evidence.evidence_id"),
        primary_key=True,
    ),
)

delivery_correction_authorizations = Table(
    "delivery_correction_authorizations",
    metadata,
    Column(
        "correction_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_corrections.correction_id"),
        primary_key=True,
    ),
    Column("authorized_by", String(200), ForeignKey("users.subject"), nullable=False),
    Column(
        "approval_authority_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("approval_authorities.approval_authority_id"),
        nullable=False,
    ),
    Column("idempotency_key", String(200), nullable=False),
    Column("correlation_id", String(100), nullable=False),
    Column("authorized_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("authorized_by", "idempotency_key", name="uq_correction_authorization_key"),
)

delivery_correction_movement_effects = Table(
    "delivery_correction_movement_effects",
    metadata,
    Column("movement_effect_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "correction_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_corrections.correction_id"),
        nullable=False,
    ),
    Column(
        "correction_line_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_correction_lines.correction_line_id"),
        nullable=False,
    ),
    Column("effect_role", String(20), nullable=False),
    Column("outcome", String(30), nullable=False),
    Column(
        "movement_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("stock_movements.movement_id"),
        nullable=False,
    ),
    Column(
        "original_movement_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("stock_movements.movement_id"),
        nullable=True,
    ),
    UniqueConstraint(
        "correction_id", "effect_role", "movement_id", name="uq_correction_movement_effect"
    ),
    CheckConstraint(
        "effect_role IN ('original','reversal','replacement')",
        name="ck_correction_movement_effect_role",
    ),
    CheckConstraint(
        "outcome IN ('accepted','short_missing')", name="ck_correction_movement_effect_outcome"
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
    ),
    Column(
        "correction_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_corrections.correction_id", use_alter=True),
        nullable=True,
        unique=True,
    ),
    Column(
        "corrects_delivery_receipt_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_receipts.delivery_receipt_id"),
        nullable=True,
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
    Column("issued_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint(
        "document_series_id", "series_number", name="uq_delivery_receipt_series_number"
    ),
)
Index(
    "uq_original_delivery_receipt_confirmation",
    delivery_receipts.c.confirmation_id,
    unique=True,
    postgresql_where=delivery_receipts.c.correction_id.is_(None),
)

delivery_receipt_documents = Table(
    "delivery_receipt_documents",
    metadata,
    Column(
        "delivery_receipt_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_receipts.delivery_receipt_id"),
        primary_key=True,
    ),
    Column("status", String(30), nullable=False, server_default="pending_document"),
    Column("object_key", String(500), nullable=False, unique=True),
    Column("checksum_sha256", String(64), nullable=True),
    Column("size_bytes", Integer, nullable=True),
    Column("rendered_at", DateTime(timezone=True), nullable=True),
    Column("last_error", String(2000), nullable=True),
)

document_series_number_audit = Table(
    "document_series_number_audit",
    metadata,
    Column("document_series_number_audit_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "document_series_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("document_series.document_series_id"),
        nullable=False,
    ),
    Column("series_number", Integer, nullable=False),
    Column("status", String(20), nullable=False),
    Column(
        "delivery_receipt_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_receipts.delivery_receipt_id"),
        nullable=True,
    ),
    Column(
        "credit_note_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("credit_notes.credit_note_id"),
        nullable=True,
    ),
    Column("reason", String(500), nullable=True),
    Column("recorded_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("document_series_id", "series_number", name="uq_document_series_number_audit"),
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
    ),
    Column(
        "source_event_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("outbox_events.outbox_event_id"),
        nullable=False,
    ),
    Column("invoice_kind", String(20), nullable=False, server_default="original"),
    Column(
        "correction_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("delivery_corrections.correction_id", use_alter=True),
        nullable=True,
    ),
    Column(
        "reversal_of_draft_invoice_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("draft_invoices.draft_invoice_id"),
        nullable=True,
        unique=True,
    ),
    Column(
        "replaces_draft_invoice_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("draft_invoices.draft_invoice_id"),
        nullable=True,
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
    UniqueConstraint("source_event_id", "invoice_kind", name="uq_draft_invoice_event_kind"),
    CheckConstraint(
        "invoice_kind IN ('original','reversal','replacement')",
        name="ck_draft_invoice_kind",
    ),
    CheckConstraint(
        "((invoice_kind IN ('original','replacement') "
        "AND grand_total = subtotal - discount_total + tax_total AND subtotal >= 0 "
        "AND discount_total >= 0 AND tax_total >= 0 AND grand_total >= 0) "
        "OR (invoice_kind = 'reversal' AND subtotal <= 0 AND discount_total <= 0 "
        "AND tax_total <= 0 AND grand_total <= 0))",
        name="ck_draft_invoice_signed_totals",
        postgresql_not_valid=True,
    ),
)
Index(
    "uq_original_draft_invoice_confirmation",
    draft_invoices.c.delivery_confirmation_id,
    unique=True,
    postgresql_where=draft_invoices.c.invoice_kind == "original",
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
    Column("invoice_kind", String(20), nullable=False, server_default="original"),
    Column("unit_price", Numeric(18, 6), nullable=False),
    Column("subtotal", Numeric(24, 6), nullable=False),
    Column("discount_amount", Numeric(24, 6), nullable=False),
    Column("tax_amount", Numeric(24, 6), nullable=False),
    Column("line_total", Numeric(24, 6), nullable=False),
    Column("calculation_snapshot", JSONB, nullable=False),
    UniqueConstraint("draft_invoice_id", "line_id", name="uq_draft_invoice_line"),
    CheckConstraint(
        "invoice_kind IN ('original','reversal','replacement')",
        name="ck_draft_invoice_line_kind",
    ),
    CheckConstraint(
        "unit_price >= 0 AND ((invoice_kind IN ('original','replacement') "
        "AND line_total = subtotal - discount_amount + tax_amount "
        "AND accepted_quantity_base > 0 "
        "AND subtotal >= 0 AND discount_amount >= 0 AND tax_amount >= 0 "
        "AND line_total >= 0) OR (invoice_kind = 'reversal' "
        "AND accepted_quantity_base < 0 AND subtotal <= 0 AND discount_amount <= 0 "
        "AND tax_amount <= 0 AND line_total <= 0))",
        name="ck_draft_invoice_line_signed_values",
        postgresql_not_valid=True,
    ),
)

customer_ledger_entries = Table(
    "customer_ledger_entries",
    metadata,
    Column("entry_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "customer_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("customer_accounts.customer_id"),
        nullable=False,
    ),
    Column("entry_type", String(30), nullable=False),
    Column("source_type", String(50), nullable=False),
    Column("source_id", PostgresUUID(as_uuid=True), nullable=False),
    Column(
        "invoice_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("draft_invoices.draft_invoice_id"),
        nullable=True,
    ),
    Column("amount", Numeric(24, 6), nullable=False),
    Column("currency", String(3), nullable=False),
    Column(
        "branch_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("branches.branch_id"),
        nullable=False,
    ),
    Column("actor_subject", String(200), ForeignKey("users.subject"), nullable=False),
    Column("correlation_id", String(100), nullable=False),
    Column("idempotency_key", String(200), nullable=False),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    Column(
        "posted_at",
        DateTime(timezone=True),
        nullable=True,
    ),
    CheckConstraint(
        "entry_type IN ('invoice', 'allocation', 'credit_note', 'void')",
        name="ck_customer_ledger_entry_type",
    ),
    CheckConstraint(
        "source_type IN ('draft_invoice', 'payment_receipt', 'payment_allocation', "
        "'credit_note', 'invoice_void', 'credit_note_reversal')",
        name="ck_customer_ledger_source_type",
    ),
    CheckConstraint(
        "amount <> 0",
        name="ck_customer_ledger_amount_nonzero",
    ),
    UniqueConstraint(
        "source_type",
        "source_id",
        "entry_type",
        name="uq_customer_ledger_source_entry_type",
    ),
    Index(
        "idx_customer_ledger_entries_customer_created",
        "customer_id",
        "created_at",
    ),
)

credit_notes = Table(
    "credit_notes",
    metadata,
    Column("credit_note_id", PostgresUUID(as_uuid=True), primary_key=True),
    Column(
        "draft_invoice_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("draft_invoices.draft_invoice_id"),
        nullable=False,
    ),
    Column(
        "customer_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("customer_accounts.customer_id"),
        nullable=False,
    ),
    Column(
        "branch_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("branches.branch_id"),
        nullable=False,
    ),
    Column(
        "document_series_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("document_series.document_series_id"),
        nullable=True,
    ),
    Column("series_number", Integer, nullable=True),
    Column("number", String(80), nullable=True, unique=True),
    Column("amount", Numeric(24, 6), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("reason", String(500), nullable=False),
    Column(
        "requested_by",
        String(200),
        ForeignKey("users.subject"),
        nullable=False,
    ),
    Column(
        "requested_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    Column(
        "posted_by",
        String(200),
        ForeignKey("users.subject"),
        nullable=True,
    ),
    Column("posted_at", DateTime(timezone=True), nullable=True),
    Column(
        "ledger_entry_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("customer_ledger_entries.entry_id"),
        nullable=True,
    ),
    Column(
        "reversed_by",
        String(200),
        ForeignKey("users.subject"),
        nullable=True,
    ),
    Column("reversed_at", DateTime(timezone=True), nullable=True),
    Column("reversal_reason", String(500), nullable=True),
    Column(
        "reversal_ledger_entry_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("customer_ledger_entries.entry_id"),
        nullable=True,
    ),
    Column(
        "status",
        String(30),
        nullable=False,
        server_default="pending_authorization",
    ),
    Column("correlation_id", String(100), nullable=False),
    Column("idempotency_key", String(200), nullable=False),
    CheckConstraint("amount > 0", name="ck_credit_note_amount_positive"),
    CheckConstraint(
        "status IN ('pending_authorization', 'posted', 'reversed')",
        name="ck_credit_note_status",
    ),
    CheckConstraint("btrim(reason) <> ''", name="ck_credit_note_reason_non_empty"),
    CheckConstraint(
        "(status = 'pending_authorization' "
        "AND document_series_id IS NULL AND series_number IS NULL AND number IS NULL "
        "AND posted_by IS NULL AND posted_at IS NULL AND ledger_entry_id IS NULL "
        "AND reversed_by IS NULL AND reversed_at IS NULL "
        "AND reversal_reason IS NULL AND reversal_ledger_entry_id IS NULL) "
        "OR "
        "(status = 'posted' "
        "AND document_series_id IS NOT NULL AND series_number IS NOT NULL "
        "AND number IS NOT NULL AND posted_by IS NOT NULL AND posted_at IS NOT NULL "
        "AND ledger_entry_id IS NOT NULL AND reversed_by IS NULL "
        "AND reversed_at IS NULL AND reversal_reason IS NULL "
        "AND reversal_ledger_entry_id IS NULL) "
        "OR "
        "(status = 'reversed' "
        "AND document_series_id IS NOT NULL AND series_number IS NOT NULL "
        "AND number IS NOT NULL AND posted_by IS NOT NULL AND posted_at IS NOT NULL "
        "AND ledger_entry_id IS NOT NULL AND reversed_by IS NOT NULL "
        "AND reversed_at IS NOT NULL AND reversal_reason IS NOT NULL "
        "AND reversal_ledger_entry_id IS NOT NULL)",
        name="ck_credit_note_posted_shape",
    ),
    UniqueConstraint("requested_by", "idempotency_key", name="uq_credit_note_actor_key"),
)

Index(
    "uq_credit_note_series_number",
    credit_notes.c.document_series_id,
    credit_notes.c.series_number,
    unique=True,
    postgresql_where=(credit_notes.c.document_series_id.is_not(None)),
)


credit_note_authorizations = Table(
    "credit_note_authorizations",
    metadata,
    Column(
        "credit_note_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("credit_notes.credit_note_id"),
        primary_key=True,
    ),
    Column(
        "authorized_by",
        String(200),
        ForeignKey("users.subject"),
        nullable=False,
    ),
    Column(
        "approval_authority_id",
        PostgresUUID(as_uuid=True),
        ForeignKey("approval_authorities.approval_authority_id"),
        nullable=False,
    ),
    Column("idempotency_key", String(200), nullable=False),
    Column("correlation_id", String(100), nullable=False),
    Column(
        "authorized_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    UniqueConstraint(
        "authorized_by",
        "idempotency_key",
        name="uq_credit_note_authorization_key",
    ),
)
