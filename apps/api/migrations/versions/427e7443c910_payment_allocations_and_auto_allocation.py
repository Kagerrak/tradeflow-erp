"""payment allocations and auto allocation

Revision ID: 427e7443c910
Revises: bff3a637931a
Create Date: 2026-08-13 10:17:36.191379
"""

from collections.abc import Sequence

from alembic import op

revision: str = "427e7443c910"
down_revision: str | None = "bff3a637931a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE payment_allocations (
          allocation_id UUID PRIMARY KEY,
          payment_receipt_id UUID NOT NULL REFERENCES payment_receipts(payment_receipt_id),
          invoice_id UUID NOT NULL REFERENCES draft_invoices(draft_invoice_id),
          amount NUMERIC(24, 6) NOT NULL,
          currency VARCHAR(3) NOT NULL,
          branch_id UUID NOT NULL REFERENCES branches(branch_id),
          actor_subject VARCHAR(200) NOT NULL REFERENCES users(subject),
          correlation_id VARCHAR(100) NOT NULL,
          idempotency_key VARCHAR(200) NOT NULL UNIQUE,
          created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
          CONSTRAINT ck_payment_allocation_amount_positive CHECK (amount > 0)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX idx_payment_allocations_receipt
          ON payment_allocations(payment_receipt_id)
        """
    )
    op.execute(
        """
        CREATE INDEX idx_payment_allocations_invoice
          ON payment_allocations(invoice_id)
        """
    )
    op.execute(
        """
        CREATE FUNCTION prevent_payment_allocation_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'Payment allocations are immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_payment_allocations_immutable
          BEFORE UPDATE OR DELETE ON payment_allocations
          FOR EACH ROW EXECUTE FUNCTION prevent_payment_allocation_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_payment_allocations_immutable ON payment_allocations")
    op.execute("DROP FUNCTION IF EXISTS prevent_payment_allocation_mutation()")
    op.execute("DROP INDEX IF EXISTS idx_payment_allocations_invoice")
    op.execute("DROP INDEX IF EXISTS idx_payment_allocations_receipt")
    op.execute("DROP TABLE IF EXISTS payment_allocations")
