from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
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
