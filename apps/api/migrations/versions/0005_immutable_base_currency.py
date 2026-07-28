"""Protect the Company Base Currency from mutation.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION prevent_company_base_currency_change()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.base_currency IS DISTINCT FROM OLD.base_currency THEN
                RAISE EXCEPTION 'Company Base Currency is immutable'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER companies_base_currency_immutable
        BEFORE UPDATE OF base_currency ON companies
        FOR EACH ROW
        EXECUTE FUNCTION prevent_company_base_currency_change()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER companies_base_currency_immutable ON companies")
    op.execute("DROP FUNCTION prevent_company_base_currency_change()")
