"""Link COD delivery confirmation to its sufficient Payment Receipt.

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE delivery_confirmations
          ADD CONSTRAINT uq_delivery_confirmation_delivery_identity
          UNIQUE (delivery_id, confirmation_id)
        """
    )
    op.execute(
        """
        CREATE TABLE cod_collections (
          confirmation_id uuid PRIMARY KEY
            REFERENCES delivery_confirmations(confirmation_id),
          delivery_id uuid NOT NULL UNIQUE REFERENCES delivery_dispatches(delivery_id),
          payment_receipt_id uuid NOT NULL UNIQUE
            REFERENCES payment_receipts(payment_receipt_id),
          amount_due numeric(24,6) NOT NULL,
          amount_collected numeric(24,6) NOT NULL,
          currency varchar(3) NOT NULL,
          status varchar(30) NOT NULL,
          collected_by varchar(200) NOT NULL REFERENCES users(subject),
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_cod_collections_sufficient
            CHECK (amount_due > 0 AND amount_collected >= amount_due),
          CONSTRAINT ck_cod_collections_status
            CHECK (status IN ('captured','pending_verification','cleared',
                              'reconciled','reversed')),
          CONSTRAINT fk_cod_collection_confirmation_delivery
            FOREIGN KEY (delivery_id, confirmation_id)
            REFERENCES delivery_confirmations(delivery_id, confirmation_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE cash_reconciliation_events (
          cash_reconciliation_event_id uuid PRIMARY KEY,
          payment_receipt_id uuid NOT NULL REFERENCES payment_receipts(payment_receipt_id),
          cash_reconciliation_id uuid NOT NULL,
          event_type varchar(20) NOT NULL,
          expected_amount numeric(24,6) NOT NULL,
          counted_amount numeric(24,6) NOT NULL,
          variance_amount numeric(24,6) NOT NULL,
          reason varchar(500) NOT NULL,
          actor_subject varchar(200) NOT NULL REFERENCES users(subject),
          occurred_at timestamptz NOT NULL,
          idempotency_key varchar(200) NOT NULL UNIQUE,
          CONSTRAINT ck_cash_reconciliation_events_type
            CHECK (event_type IN ('reconciled','adjusted','reversed')),
          CONSTRAINT ck_cash_reconciliation_events_amounts CHECK (
            expected_amount >= 0 AND counted_amount >= 0
            AND variance_amount = counted_amount - expected_amount
          )
        )
        """
    )
    op.execute(
        """
        CREATE TRIGGER cash_reconciliation_events_immutable
          BEFORE UPDATE OR DELETE ON cash_reconciliation_events
          FOR EACH ROW EXECUTE FUNCTION prevent_payment_fulfillment_ledger_mutation()
        """
    )
    op.execute(
        """
        CREATE TABLE cod_on_account_conversions (
          conversion_id uuid PRIMARY KEY,
          delivery_id uuid NOT NULL UNIQUE REFERENCES delivery_dispatches(delivery_id),
          confirmation_id uuid UNIQUE REFERENCES delivery_confirmations(confirmation_id),
          commercial_approval_id uuid NOT NULL
            REFERENCES commercial_approvals(commercial_approval_id),
          amount numeric(24,6) NOT NULL,
          currency varchar(3) NOT NULL,
          open_balance_snapshot numeric(24,6) NOT NULL,
          approved_uninvoiced_snapshot numeric(24,6) NOT NULL,
          credit_limit_snapshot numeric(24,6),
          credit_excess_approved numeric(24,6) NOT NULL,
          reason varchar(500) NOT NULL,
          approved_by varchar(200) NOT NULL REFERENCES users(subject),
          status varchar(20) NOT NULL,
          correlation_id varchar(100) NOT NULL,
          idempotency_key varchar(200) NOT NULL UNIQUE,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_cod_on_account_conversion_amounts CHECK (
            amount > 0 AND open_balance_snapshot >= 0
            AND approved_uninvoiced_snapshot >= 0 AND credit_excess_approved >= 0
          ),
          CONSTRAINT ck_cod_on_account_conversion_status
            CHECK (status IN ('approved','consumed','reversed')),
          CONSTRAINT ck_cod_on_account_conversion_confirmation CHECK (
            (status = 'consumed' AND confirmation_id IS NOT NULL)
            OR (status IN ('approved','reversed') AND confirmation_id IS NULL)
          ),
          CONSTRAINT fk_cod_conversion_confirmation_delivery
            FOREIGN KEY (delivery_id, confirmation_id)
            REFERENCES delivery_confirmations(delivery_id, confirmation_id)
        )
        """
    )
    op.execute(
        """
        CREATE FUNCTION protect_cod_collection_terms() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'COD collections are immutable';
          END IF;
          IF NEW.confirmation_id IS DISTINCT FROM OLD.confirmation_id
             OR NEW.delivery_id IS DISTINCT FROM OLD.delivery_id
             OR NEW.payment_receipt_id IS DISTINCT FROM OLD.payment_receipt_id
             OR NEW.amount_due IS DISTINCT FROM OLD.amount_due
             OR NEW.amount_collected IS DISTINCT FROM OLD.amount_collected
             OR NEW.currency IS DISTINCT FROM OLD.currency
             OR NEW.collected_by IS DISTINCT FROM OLD.collected_by
             OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION 'COD collection terms are immutable';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER cod_collection_terms_immutable
          BEFORE UPDATE OR DELETE ON cod_collections
          FOR EACH ROW EXECUTE FUNCTION protect_cod_collection_terms()
        """
    )
    op.execute(
        """
        CREATE FUNCTION protect_cod_conversion_approval() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'COD On Account approvals are immutable';
          END IF;
          IF NEW.conversion_id IS DISTINCT FROM OLD.conversion_id
             OR NEW.delivery_id IS DISTINCT FROM OLD.delivery_id
             OR NEW.commercial_approval_id IS DISTINCT FROM OLD.commercial_approval_id
             OR NEW.amount IS DISTINCT FROM OLD.amount
             OR NEW.currency IS DISTINCT FROM OLD.currency
             OR NEW.open_balance_snapshot IS DISTINCT FROM OLD.open_balance_snapshot
             OR NEW.approved_uninvoiced_snapshot IS DISTINCT FROM OLD.approved_uninvoiced_snapshot
             OR NEW.credit_limit_snapshot IS DISTINCT FROM OLD.credit_limit_snapshot
             OR NEW.credit_excess_approved IS DISTINCT FROM OLD.credit_excess_approved
             OR NEW.reason IS DISTINCT FROM OLD.reason
             OR NEW.approved_by IS DISTINCT FROM OLD.approved_by
             OR NEW.correlation_id IS DISTINCT FROM OLD.correlation_id
             OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
             OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION 'COD On Account approval is immutable';
          END IF;
          IF NEW.status IS NOT DISTINCT FROM OLD.status
             AND NEW.confirmation_id IS NOT DISTINCT FROM OLD.confirmation_id THEN
            RETURN NEW;
          END IF;
          IF OLD.status = 'approved' AND OLD.confirmation_id IS NULL
             AND (
               (NEW.status = 'consumed' AND NEW.confirmation_id IS NOT NULL)
               OR (NEW.status = 'reversed' AND NEW.confirmation_id IS NULL)
             ) THEN
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'Invalid COD On Account approval transition';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER cod_conversion_approval_immutable
          BEFORE UPDATE OR DELETE ON cod_on_account_conversions
          FOR EACH ROW EXECUTE FUNCTION protect_cod_conversion_approval()
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS cod_on_account_conversions")
    op.execute("DROP TABLE IF EXISTS cash_reconciliation_events")
    op.execute("DROP TABLE IF EXISTS cod_collections")
    op.execute("DROP FUNCTION IF EXISTS protect_cod_conversion_approval()")
    op.execute("DROP FUNCTION IF EXISTS protect_cod_collection_terms()")
    op.execute(
        "ALTER TABLE delivery_confirmations "
        "DROP CONSTRAINT IF EXISTS uq_delivery_confirmation_delivery_identity"
    )
