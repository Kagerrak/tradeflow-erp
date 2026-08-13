"""supplier directory for procurement

Revision ID: 79a7b271a628
Revises: 427e7443c910
Create Date: 2026-08-13 11:41:07.357655
"""

from collections.abc import Sequence

from alembic import op

revision: str = "79a7b271a628"
down_revision: str | None = "427e7443c910"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE suppliers (
          supplier_id UUID PRIMARY KEY,
          company_id UUID NOT NULL REFERENCES companies(company_id),
          code VARCHAR(50) NOT NULL,
          legal_name VARCHAR(200) NOT NULL,
          tax_id VARCHAR(50),
          payment_terms VARCHAR(50) NOT NULL,
          default_currency VARCHAR(3) NOT NULL,
          is_active BOOLEAN NOT NULL DEFAULT true,
          version INTEGER NOT NULL DEFAULT 1,
          created_by VARCHAR(200) NOT NULL REFERENCES users(subject),
          created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
          updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
          CONSTRAINT ck_suppliers_version_positive CHECK (version > 0),
          CONSTRAINT uq_suppliers_company_code UNIQUE (company_id, code)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX idx_suppliers_code ON suppliers(code)
        """
    )
    op.execute(
        """
        CREATE INDEX idx_suppliers_legal_name ON suppliers(legal_name)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_suppliers_legal_name")
    op.execute("DROP INDEX IF EXISTS idx_suppliers_code")
    op.execute("DROP TABLE IF EXISTS suppliers")
