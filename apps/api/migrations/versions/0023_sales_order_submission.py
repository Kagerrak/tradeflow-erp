"""Add an authoritative sales-order submission state.

Revision ID: 0023
Revises: 0022
Create Date: 2026-08-24
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_sales_orders_status", "sales_orders", type_="check")
    op.create_check_constraint(
        "ck_sales_orders_status",
        "sales_orders",
        "status IN ('draft', 'awaiting_approval', 'approved', 'held', "
        "'partially_cancelled', 'cancelled')",
    )


def downgrade() -> None:
    op.execute("UPDATE sales_orders SET status = 'draft' WHERE status = 'awaiting_approval'")
    op.drop_constraint("ck_sales_orders_status", "sales_orders", type_="check")
    op.create_check_constraint(
        "ck_sales_orders_status",
        "sales_orders",
        "status IN ('draft', 'approved', 'held', 'partially_cancelled', 'cancelled')",
    )
