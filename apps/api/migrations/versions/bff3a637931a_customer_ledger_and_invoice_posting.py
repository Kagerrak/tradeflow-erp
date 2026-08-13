"""customer ledger and invoice posting

Revision ID: bff3a637931a
Revises: 0016
Create Date: 2026-08-13 09:07:34.640655
"""

from collections.abc import Sequence

from alembic import op

revision: str = "bff3a637931a"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE customer_ledger_entries (
          entry_id UUID PRIMARY KEY,
          customer_id UUID NOT NULL REFERENCES customer_accounts(customer_id),
          entry_type VARCHAR(30) NOT NULL,
          source_type VARCHAR(50) NOT NULL,
          source_id UUID NOT NULL,
          invoice_id UUID REFERENCES draft_invoices(draft_invoice_id),
          amount NUMERIC(24, 6) NOT NULL,
          currency VARCHAR(3) NOT NULL,
          branch_id UUID NOT NULL REFERENCES branches(branch_id),
          actor_subject VARCHAR(200) NOT NULL REFERENCES users(subject),
          correlation_id VARCHAR(100) NOT NULL,
          idempotency_key VARCHAR(200) NOT NULL,
          created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
          posted_at TIMESTAMP WITH TIME ZONE,
          CONSTRAINT ck_customer_ledger_entry_type
            CHECK (entry_type IN ('invoice', 'allocation', 'credit_note', 'void')),
          CONSTRAINT ck_customer_ledger_source_type
            CHECK (source_type IN (
              'draft_invoice', 'payment_receipt', 'payment_allocation',
              'credit_note', 'invoice_void'
            )),
          CONSTRAINT ck_customer_ledger_amount_nonzero
            CHECK (amount <> 0),
          CONSTRAINT uq_customer_ledger_source_entry_type
            UNIQUE (source_type, source_id, entry_type)
        )
        """
    )

    op.execute(
        """
        CREATE INDEX idx_customer_ledger_entries_customer_created
          ON customer_ledger_entries(customer_id, created_at)
        """
    )

    op.execute(
        """
        CREATE FUNCTION prevent_customer_ledger_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'Customer ledger entries are immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_customer_ledger_entries_immutable
          BEFORE UPDATE OR DELETE ON customer_ledger_entries
          FOR EACH ROW EXECUTE FUNCTION prevent_customer_ledger_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_customer_ledger_entries_immutable ON customer_ledger_entries"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_customer_ledger_mutation()")
    op.execute("DROP INDEX IF EXISTS idx_customer_ledger_entries_customer_created")
    op.execute("DROP TABLE customer_ledger_entries")
