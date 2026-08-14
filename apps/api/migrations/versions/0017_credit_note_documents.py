"""credit note documents with maker-checker control

Revision ID: 0017
Revises: d53dcaa7ede3
Create Date: 2026-08-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | Sequence[str] | None = ("d53dcaa7ede3", "d524a29c32b8")
depends_on: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Expand document series to support credit notes.
    op.execute(
        """
        ALTER TABLE document_series
          DROP CONSTRAINT ck_document_series_type
        """
    )
    op.execute(
        """
        ALTER TABLE document_series
          ADD CONSTRAINT ck_document_series_type
            CHECK (document_type IN ('delivery_receipt', 'credit_note'))
        """
    )

    # 2. Expand customer ledger source_type to support credit note reversals.
    op.execute(
        """
        ALTER TABLE customer_ledger_entries
          DROP CONSTRAINT ck_customer_ledger_source_type
        """
    )
    op.execute(
        """
        ALTER TABLE customer_ledger_entries
          ADD CONSTRAINT ck_customer_ledger_source_type CHECK (
            source_type IN (
              'draft_invoice', 'payment_receipt', 'payment_allocation',
              'credit_note', 'invoice_void', 'credit_note_reversal'
            )
          )
        """
    )

    # 3. Create credit_notes table.
    op.execute(
        """
        CREATE TABLE credit_notes (
          credit_note_id uuid PRIMARY KEY,
          draft_invoice_id uuid NOT NULL REFERENCES draft_invoices(draft_invoice_id),
          customer_id uuid NOT NULL REFERENCES customer_accounts(customer_id),
          branch_id uuid NOT NULL REFERENCES branches(branch_id),
          document_series_id uuid REFERENCES document_series(document_series_id),
          series_number integer,
          number varchar(80) UNIQUE,
          amount numeric(24,6) NOT NULL,
          currency varchar(3) NOT NULL,
          reason varchar(500) NOT NULL,
          requested_by varchar(200) NOT NULL REFERENCES users(subject),
          requested_at timestamptz NOT NULL DEFAULT now(),
          posted_by varchar(200) REFERENCES users(subject),
          posted_at timestamptz,
          ledger_entry_id uuid REFERENCES customer_ledger_entries(entry_id),
          reversed_by varchar(200) REFERENCES users(subject),
          reversed_at timestamptz,
          reversal_reason varchar(500),
          reversal_ledger_entry_id uuid REFERENCES customer_ledger_entries(entry_id),
          status varchar(30) NOT NULL DEFAULT 'pending_authorization',
          correlation_id varchar(100) NOT NULL,
          idempotency_key varchar(200) NOT NULL,
          CONSTRAINT ck_credit_note_amount CHECK (amount > 0),
          CONSTRAINT ck_credit_note_status CHECK (
            status IN ('pending_authorization', 'posted', 'reversed')
          ),
          CONSTRAINT ck_credit_note_reason CHECK (btrim(reason) <> ''),
          CONSTRAINT ck_credit_note_posted_shape CHECK (
            (status = 'pending_authorization'
              AND document_series_id IS NULL
              AND series_number IS NULL
              AND number IS NULL
              AND posted_by IS NULL
              AND posted_at IS NULL
              AND ledger_entry_id IS NULL
              AND reversed_by IS NULL
              AND reversed_at IS NULL
              AND reversal_reason IS NULL
              AND reversal_ledger_entry_id IS NULL)
            OR
            (status = 'posted'
              AND document_series_id IS NOT NULL
              AND series_number IS NOT NULL
              AND number IS NOT NULL
              AND posted_by IS NOT NULL
              AND posted_at IS NOT NULL
              AND ledger_entry_id IS NOT NULL
              AND reversed_by IS NULL
              AND reversed_at IS NULL
              AND reversal_reason IS NULL
              AND reversal_ledger_entry_id IS NULL)
            OR
            (status = 'reversed'
              AND document_series_id IS NOT NULL
              AND series_number IS NOT NULL
              AND number IS NOT NULL
              AND posted_by IS NOT NULL
              AND posted_at IS NOT NULL
              AND ledger_entry_id IS NOT NULL
              AND reversed_by IS NOT NULL
              AND reversed_at IS NOT NULL
              AND reversal_reason IS NOT NULL
              AND reversal_ledger_entry_id IS NOT NULL)
          ),
          CONSTRAINT uq_credit_note_actor_key UNIQUE (requested_by, idempotency_key)
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_credit_note_series_number
          ON credit_notes(document_series_id, series_number)
          WHERE document_series_id IS NOT NULL
        """
    )

    # 4. Create credit_note_authorizations table.
    op.execute(
        """
        CREATE TABLE credit_note_authorizations (
          credit_note_id uuid PRIMARY KEY REFERENCES credit_notes(credit_note_id),
          authorized_by varchar(200) NOT NULL REFERENCES users(subject),
          approval_authority_id uuid NOT NULL
            REFERENCES approval_authorities(approval_authority_id),
          idempotency_key varchar(200) NOT NULL,
          correlation_id varchar(100) NOT NULL,
          authorized_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_credit_note_authorization_key UNIQUE (authorized_by, idempotency_key)
        )
        """
    )

    # 5. Add credit_note_id to document series audit now that credit_notes exists.
    op.execute(
        """
        ALTER TABLE document_series_number_audit
          ADD COLUMN credit_note_id uuid REFERENCES credit_notes(credit_note_id)
        """
    )
    op.execute(
        """
        ALTER TABLE document_series_number_audit
          DROP CONSTRAINT ck_document_series_number_audit_reason
        """
    )
    op.execute(
        """
        ALTER TABLE document_series_number_audit
          ADD CONSTRAINT ck_document_series_number_audit_reason CHECK (
            (status = 'issued' AND (delivery_receipt_id IS NOT NULL OR credit_note_id IS NOT NULL))
            OR (status IN ('voided', 'skipped') AND reason IS NOT NULL)
          )
        """
    )

    # 6. Immutable-history triggers.
    op.execute(
        """
        CREATE FUNCTION reject_credit_note_mutation() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'UPDATE'
             AND OLD.status = 'pending_authorization'
             AND NEW.status = 'posted'
             AND (to_jsonb(OLD) - 'status' - 'document_series_id' - 'series_number'
                  - 'number' - 'posted_by' - 'posted_at' - 'ledger_entry_id')
                 = (to_jsonb(NEW) - 'status' - 'document_series_id' - 'series_number'
                    - 'number' - 'posted_by' - 'posted_at' - 'ledger_entry_id') THEN
            RETURN NEW;
          END IF;
          IF TG_OP = 'UPDATE'
             AND OLD.status = 'posted'
             AND NEW.status = 'reversed'
             AND (to_jsonb(OLD) - 'status' - 'reversed_by' - 'reversed_at'
                  - 'reversal_reason' - 'reversal_ledger_entry_id')
                 = (to_jsonb(NEW) - 'status' - 'reversed_by' - 'reversed_at'
                    - 'reversal_reason' - 'reversal_ledger_entry_id') THEN
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'Credit Note history is immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_credit_notes_immutable
          BEFORE UPDATE OR DELETE ON credit_notes
          FOR EACH ROW EXECUTE FUNCTION reject_credit_note_mutation()
        """
    )

    op.execute(
        """
        CREATE FUNCTION reject_credit_note_authorization_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'Credit Note authorization history is immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_credit_note_authorizations_immutable
          BEFORE UPDATE OR DELETE ON credit_note_authorizations
          FOR EACH ROW EXECUTE FUNCTION reject_credit_note_authorization_mutation()
        """
    )

    # 7. Append guard against sealed credit notes.
    op.execute(
        """
        CREATE FUNCTION reject_sealed_credit_note_append() RETURNS trigger AS $$
        DECLARE note_status varchar(30);
        BEGIN
          SELECT status INTO note_status FROM credit_notes
            WHERE credit_note_id = NEW.credit_note_id;
          IF note_status <> 'pending_authorization' THEN
            RAISE EXCEPTION 'Credit Note proposal is sealed';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_credit_note_authorizations_append_guard
          BEFORE INSERT ON credit_note_authorizations
          FOR EACH ROW EXECUTE FUNCTION reject_sealed_credit_note_append()
        """
    )

    # 8. Deferred maker-checker authorization validation.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION validate_credit_note_authorization()
          RETURNS trigger AS $$
        DECLARE note record;
        DECLARE authority record;
        BEGIN
          SELECT * INTO note FROM credit_notes
            WHERE credit_note_id = NEW.credit_note_id;
          SELECT * INTO authority FROM approval_authorities
            WHERE approval_authority_id = NEW.approval_authority_id;

          IF NOT FOUND
             OR note.status <> 'pending_authorization'
             OR NEW.authorized_by = note.requested_by
             OR authority.user_subject IS DISTINCT FROM NEW.authorized_by
             OR authority.capability_code IS DISTINCT FROM 'finance:credit-note-approve'
             OR authority.branch_id IS DISTINCT FROM note.branch_id
             OR (authority.maximum_amount IS NOT NULL
                 AND authority.maximum_amount < note.amount) THEN
            RAISE EXCEPTION 'Credit Note authorization violates maker-checker authority';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_credit_note_authorization_valid
          AFTER INSERT ON credit_note_authorizations
          FOR EACH ROW EXECUTE FUNCTION validate_credit_note_authorization()
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM credit_notes) THEN
            RAISE EXCEPTION 'Cannot downgrade while immutable Credit Note history exists';
          END IF;
        END $$
        """
    )

    op.execute(
        "DROP TRIGGER IF EXISTS trg_credit_note_authorization_valid ON credit_note_authorizations"
    )
    op.execute("DROP FUNCTION IF EXISTS validate_credit_note_authorization")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_credit_note_authorizations_append_guard"
        " ON credit_note_authorizations"
    )
    op.execute("DROP FUNCTION IF EXISTS reject_sealed_credit_note_append")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_credit_note_authorizations_immutable"
        " ON credit_note_authorizations"
    )
    op.execute("DROP FUNCTION IF EXISTS reject_credit_note_authorization_mutation")
    op.execute("DROP TRIGGER IF EXISTS trg_credit_notes_immutable ON credit_notes")
    op.execute("DROP FUNCTION IF EXISTS reject_credit_note_mutation")

    op.execute(
        """
        ALTER TABLE document_series_number_audit
          DROP CONSTRAINT ck_document_series_number_audit_reason
        """
    )
    op.execute(
        """
        ALTER TABLE document_series_number_audit
          ADD CONSTRAINT ck_document_series_number_audit_reason CHECK (
            (status = 'issued' AND delivery_receipt_id IS NOT NULL)
            OR (status IN ('voided', 'skipped') AND reason IS NOT NULL)
          )
        """
    )
    op.execute(
        """
        ALTER TABLE document_series_number_audit
          DROP COLUMN credit_note_id
        """
    )

    op.execute("DROP TABLE credit_note_authorizations")
    op.execute("DROP TABLE credit_notes")

    op.execute(
        """
        ALTER TABLE customer_ledger_entries
          DROP CONSTRAINT ck_customer_ledger_source_type
        """
    )
    op.execute(
        """
        ALTER TABLE customer_ledger_entries
          ADD CONSTRAINT ck_customer_ledger_source_type CHECK (
            source_type IN (
              'draft_invoice', 'payment_receipt', 'payment_allocation',
              'credit_note', 'invoice_void'
            )
          )
        """
    )

    op.execute(
        """
        ALTER TABLE document_series
          DROP CONSTRAINT ck_document_series_type
        """
    )
    op.execute(
        """
        ALTER TABLE document_series
          ADD CONSTRAINT ck_document_series_type
            CHECK (document_type = 'delivery_receipt')
        """
    )
