"""Create customer accounts, contacts, and address versions.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "customer_accounts",
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_number", sa.String(length=50), nullable=False),
        sa.Column("legal_name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("payment_terms", sa.String(length=50), nullable=False),
        sa.Column("payment_timing_policy", sa.String(length=30), nullable=False),
        sa.Column("credit_limit", sa.Numeric(18, 2), nullable=True),
        sa.Column("credit_hold", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_by", sa.String(length=200), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "credit_limit IS NULL OR credit_limit >= 0",
            name="ck_customer_accounts_credit_limit",
        ),
        sa.CheckConstraint(
            "payment_timing_policy IN ('prepaid', 'cash_on_delivery', 'on_account')",
            name="ck_customer_accounts_payment_timing",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'inactive', 'prospect')",
            name="ck_customer_accounts_status",
        ),
        sa.CheckConstraint(
            "version > 0",
            name="ck_customer_accounts_version_positive",
        ),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.branch_id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.subject"]),
        sa.PrimaryKeyConstraint("customer_id"),
        sa.UniqueConstraint("account_number"),
    )
    op.create_table(
        "customer_contacts",
        sa.Column("contact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("role", sa.String(length=100), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customer_accounts.customer_id"],
        ),
        sa.PrimaryKeyConstraint("contact_id"),
    )
    op.create_table(
        "customer_address_versions",
        sa.Column(
            "address_version_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("address_key", sa.String(length=50), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("line_1", sa.String(length=200), nullable=False),
        sa.Column("line_2", sa.String(length=200), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=False),
        sa.Column("region", sa.String(length=100), nullable=False),
        sa.Column("postal_code", sa.String(length=30), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('billing', 'delivery')",
            name="ck_customer_address_versions_kind",
        ),
        sa.CheckConstraint(
            "version > 0",
            name="ck_customer_address_versions_version_positive",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customer_accounts.customer_id"],
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.subject"]),
        sa.PrimaryKeyConstraint("address_version_id"),
        sa.UniqueConstraint(
            "customer_id",
            "address_key",
            "version",
            name="uq_customer_address_version",
        ),
    )
    op.create_index(
        "uq_customer_address_current",
        "customer_address_versions",
        ["customer_id", "address_key"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_customer_address_current",
        table_name="customer_address_versions",
    )
    op.drop_table("customer_address_versions")
    op.drop_table("customer_contacts")
    op.drop_table("customer_accounts")
