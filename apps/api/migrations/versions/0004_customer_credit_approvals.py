"""Create customer credit approval audit records.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "customer_credit_approvals",
        sa.Column(
            "credit_approval_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approved_by", sa.String(length=200), nullable=False),
        sa.Column("maker_subject", sa.String(length=200), nullable=False),
        sa.Column("approved_limit", sa.Numeric(18, 2), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["approved_by"], ["users.subject"]),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customer_accounts.customer_id"],
        ),
        sa.ForeignKeyConstraint(["maker_subject"], ["users.subject"]),
        sa.PrimaryKeyConstraint("credit_approval_id"),
    )


def downgrade() -> None:
    op.drop_table("customer_credit_approvals")
