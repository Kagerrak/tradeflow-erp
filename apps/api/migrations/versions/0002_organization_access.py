"""Create organization and access-control records.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("singleton_key", sa.String(length=20), nullable=False),
        sa.Column("code", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("base_currency", sa.String(length=3), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "singleton_key = 'tradeflow'",
            name="ck_companies_singleton",
        ),
        sa.CheckConstraint("version > 0", name="ck_companies_version_positive"),
        sa.PrimaryKeyConstraint("company_id"),
        sa.UniqueConstraint("code"),
        sa.UniqueConstraint("singleton_key"),
    )
    op.create_table(
        "branches",
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint("version > 0", name="ck_branches_version_positive"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.company_id"]),
        sa.PrimaryKeyConstraint("branch_id"),
        sa.UniqueConstraint("code"),
    )
    op.create_table(
        "warehouses",
        sa.Column("warehouse_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint("version > 0", name="ck_warehouses_version_positive"),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.branch_id"]),
        sa.PrimaryKeyConstraint("warehouse_id"),
        sa.UniqueConstraint("code"),
    )
    op.create_table(
        "capabilities",
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.PrimaryKeyConstraint("code"),
    )
    op.create_table(
        "role_templates",
        sa.Column(
            "role_template_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.PrimaryKeyConstraint("role_template_id"),
        sa.UniqueConstraint("code"),
    )
    op.create_table(
        "users",
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column(
            "is_operations_administrator",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.PrimaryKeyConstraint("subject"),
    )
    op.create_table(
        "role_template_capabilities",
        sa.Column(
            "role_template_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("capability_code", sa.String(length=100), nullable=False),
        sa.ForeignKeyConstraint(["capability_code"], ["capabilities.code"]),
        sa.ForeignKeyConstraint(
            ["role_template_id"],
            ["role_templates.role_template_id"],
        ),
        sa.PrimaryKeyConstraint("role_template_id", "capability_code"),
    )
    op.create_table(
        "user_role_templates",
        sa.Column("user_subject", sa.String(length=200), nullable=False),
        sa.Column(
            "role_template_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["role_template_id"],
            ["role_templates.role_template_id"],
        ),
        sa.ForeignKeyConstraint(["user_subject"], ["users.subject"]),
        sa.PrimaryKeyConstraint("user_subject", "role_template_id"),
    )
    op.create_table(
        "user_branch_scopes",
        sa.Column("user_subject", sa.String(length=200), nullable=False),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.branch_id"]),
        sa.ForeignKeyConstraint(["user_subject"], ["users.subject"]),
        sa.PrimaryKeyConstraint("user_subject", "branch_id"),
    )
    op.create_table(
        "user_warehouse_scopes",
        sa.Column("user_subject", sa.String(length=200), nullable=False),
        sa.Column("warehouse_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["user_subject"], ["users.subject"]),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.warehouse_id"]),
        sa.PrimaryKeyConstraint("user_subject", "warehouse_id"),
    )
    op.create_table(
        "approval_authorities",
        sa.Column(
            "approval_authority_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("user_subject", sa.String(length=200), nullable=False),
        sa.Column("capability_code", sa.String(length=100), nullable=False),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("maximum_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("maximum_percentage", sa.Numeric(9, 6), nullable=True),
        sa.Column(
            "maker_checker_required",
            sa.Boolean(),
            server_default="true",
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.branch_id"]),
        sa.ForeignKeyConstraint(["capability_code"], ["capabilities.code"]),
        sa.ForeignKeyConstraint(["user_subject"], ["users.subject"]),
        sa.PrimaryKeyConstraint("approval_authority_id"),
        sa.UniqueConstraint(
            "user_subject",
            "capability_code",
            "branch_id",
            name="uq_approval_authority_assignment",
        ),
    )


def downgrade() -> None:
    op.drop_table("approval_authorities")
    op.drop_table("user_warehouse_scopes")
    op.drop_table("user_branch_scopes")
    op.drop_table("user_role_templates")
    op.drop_table("role_template_capabilities")
    op.drop_table("users")
    op.drop_table("role_templates")
    op.drop_table("capabilities")
    op.drop_table("warehouses")
    op.drop_table("branches")
    op.drop_table("companies")
