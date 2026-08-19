"""add purchase requests and link to purchase order lines

Revision ID: a55254931268
Revises: 0017
Create Date: 2026-08-19 18:46:22.297306
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a55254931268"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "purchase_requests",
        sa.Column("purchase_request_id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("supplier_id", sa.UUID(), nullable=False),
        sa.Column("branch_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column(
            "exchange_rate", sa.Numeric(precision=18, scale=6), server_default="1", nullable=False
        ),
        sa.Column("status", sa.String(length=30), server_default="draft", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_by", sa.String(length=200), nullable=False),
        sa.Column("approved_by", sa.String(length=200), nullable=True),
        sa.Column("rejected_by", sa.String(length=200), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("correlation_id", sa.String(length=100), nullable=False),
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
            "status IN ('draft', 'submitted', 'approved', 'partially_converted', "
            "'fully_converted', 'rejected')",
            name="ck_purchase_requests_status",
        ),
        sa.CheckConstraint("exchange_rate > 0", name="ck_purchase_requests_exchange_rate_positive"),
        sa.CheckConstraint("version > 0", name="ck_purchase_requests_version_positive"),
        sa.ForeignKeyConstraint(["approved_by"], ["users.subject"]),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.branch_id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.company_id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.subject"]),
        sa.ForeignKeyConstraint(["rejected_by"], ["users.subject"]),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.supplier_id"]),
        sa.PrimaryKeyConstraint("purchase_request_id"),
        sa.UniqueConstraint("company_id", "code", name="uq_purchase_requests_company_code"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_table(
        "purchase_request_lines",
        sa.Column("purchase_request_line_id", sa.UUID(), nullable=False),
        sa.Column("purchase_request_id", sa.UUID(), nullable=False),
        sa.Column("sku_id", sa.UUID(), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("requested_quantity", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("unit_code", sa.String(length=30), nullable=False),
        sa.Column("base_quantity", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("unit_cost", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "base_quantity > 0", name="ck_purchase_request_lines_base_quantity_positive"
        ),
        sa.CheckConstraint(
            "line_number > 0", name="ck_purchase_request_lines_line_number_positive"
        ),
        sa.CheckConstraint(
            "requested_quantity > 0", name="ck_purchase_request_lines_requested_quantity_positive"
        ),
        sa.CheckConstraint("unit_cost >= 0", name="ck_purchase_request_lines_unit_cost_positive"),
        sa.CheckConstraint("version > 0", name="ck_purchase_request_lines_version_positive"),
        sa.ForeignKeyConstraint(["purchase_request_id"], ["purchase_requests.purchase_request_id"]),
        sa.ForeignKeyConstraint(["sku_id"], ["skus.sku_id"]),
        sa.PrimaryKeyConstraint("purchase_request_line_id"),
        sa.UniqueConstraint(
            "purchase_request_id", "line_number", name="uq_purchase_request_lines_request_line"
        ),
    )
    op.add_column(
        "purchase_order_lines", sa.Column("purchase_request_line_id", sa.UUID(), nullable=True)
    )
    op.create_foreign_key(
        "fk_purchase_order_lines_request_line",
        "purchase_order_lines",
        "purchase_request_lines",
        ["purchase_request_line_id"],
        ["purchase_request_line_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_purchase_order_lines_request_line", "purchase_order_lines", type_="foreignkey"
    )
    op.drop_column("purchase_order_lines", "purchase_request_line_id")
    op.drop_table("purchase_request_lines")
    op.drop_table("purchase_requests")
