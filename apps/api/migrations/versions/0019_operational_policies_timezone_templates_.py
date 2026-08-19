"""operational policies timezone templates and base currency guard

Revision ID: 0019
Revises: aefae8360657
Create Date: 2026-08-19 21:22:45.182812
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0019"
down_revision: str | None = "aefae8360657"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE companies
          ADD COLUMN timezone VARCHAR(64) NOT NULL DEFAULT 'UTC',
          ADD CONSTRAINT ck_companies_timezone_not_empty CHECK (btrim(timezone) <> '')
        """
    )
    op.execute(
        """
        ALTER TABLE branches
          ADD COLUMN timezone VARCHAR(64) NOT NULL DEFAULT 'UTC',
          ADD CONSTRAINT ck_branches_timezone_not_empty CHECK (btrim(timezone) <> '')
        """
    )

    op.execute(
        """
        CREATE TABLE document_templates (
          document_template_id UUID PRIMARY KEY,
          company_id UUID NOT NULL REFERENCES companies(company_id),
          branch_id UUID REFERENCES branches(branch_id),
          document_type VARCHAR(40) NOT NULL,
          version INTEGER NOT NULL,
          name VARCHAR(200) NOT NULL,
          template_body TEXT NOT NULL,
          effective_from DATE NOT NULL,
          effective_to DATE,
          is_active BOOLEAN NOT NULL DEFAULT true,
          created_by VARCHAR(200) NOT NULL REFERENCES users(subject),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT ck_document_templates_version_positive CHECK (version > 0),
          CONSTRAINT ck_document_templates_effective_range CHECK (
            effective_to IS NULL OR effective_to >= effective_from
          )
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_document_template_company_type_version
          ON document_templates(company_id, document_type, version)
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_document_template_branch_type_version
          ON document_templates(branch_id, document_type, version)
          WHERE branch_id IS NOT NULL
        """
    )

    op.execute(
        """
        ALTER TABLE document_series
          ADD COLUMN version INTEGER NOT NULL DEFAULT 1,
          ADD CONSTRAINT ck_document_series_version_positive CHECK (version > 0)
        """
    )

    op.execute(
        """
        ALTER TABLE document_series
          DROP CONSTRAINT ck_document_series_type,
          ADD CONSTRAINT ck_document_series_type
            CHECK (document_type ~ '^[a-z][a-z0-9_]{1,38}$')
        """
    )

    op.execute("DROP TRIGGER IF EXISTS trg_document_series_protected ON document_series")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION protect_document_series() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Document Series cannot be deleted';
          END IF;
          IF NEW.branch_id <> OLD.branch_id OR NEW.document_type <> OLD.document_type THEN
            RAISE EXCEPTION 'Document Series identity is immutable';
          END IF;
          IF NEW.version = OLD.version THEN
            IF NEW.prefix <> OLD.prefix OR NEW.next_number <> OLD.next_number + 1 THEN
              RAISE EXCEPTION 'Document Series identity is immutable and sequence is monotonic';
            END IF;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_document_series_protected
        BEFORE UPDATE OR DELETE ON document_series
        FOR EACH ROW EXECUTE FUNCTION protect_document_series()
        """
    )

    op.execute("DROP TRIGGER IF EXISTS trg_document_series_audit_complete ON document_series")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION validate_document_series_audit() RETURNS trigger AS $$
        DECLARE missing_number integer;
        DECLARE too_high integer;
        BEGIN
          IF TG_OP = 'INSERT' THEN
            RETURN NEW;
          END IF;
          IF NEW.version <> OLD.version THEN
            SELECT max(series_number) INTO too_high FROM document_series_number_audit
              WHERE document_series_id = NEW.document_series_id
                AND series_number >= NEW.next_number;
            IF too_high IS NOT NULL THEN
              RAISE EXCEPTION 'Document Series numbers cannot be reused';
            END IF;
          ELSE
            SELECT n INTO missing_number FROM generate_series(1,NEW.next_number - 1) n
            WHERE NOT EXISTS (
              SELECT 1 FROM document_series_number_audit a
              WHERE a.document_series_id = NEW.document_series_id
                AND a.series_number = n) LIMIT 1;
            SELECT max(series_number) INTO too_high FROM document_series_number_audit
              WHERE document_series_id = NEW.document_series_id
                AND series_number >= NEW.next_number;
            IF missing_number IS NOT NULL OR too_high IS NOT NULL THEN
              RAISE EXCEPTION 'Every consumed Document Series number must be audited exactly once';
            END IF;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_document_series_audit_complete
        AFTER INSERT OR UPDATE OF next_number ON document_series
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION validate_document_series_audit()
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_base_currency_change_with_postings()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.base_currency IS DISTINCT FROM OLD.base_currency THEN
                IF EXISTS (SELECT 1 FROM stock_movements LIMIT 1)
                   OR EXISTS (SELECT 1 FROM customer_ledger_entries LIMIT 1) THEN
                    RAISE EXCEPTION 'Company Base Currency is immutable after dependent postings'
                        USING ERRCODE = 'check_violation';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute("DROP TRIGGER IF EXISTS companies_base_currency_immutable ON companies")
    op.execute(
        """
        CREATE TRIGGER companies_base_currency_immutable
        BEFORE UPDATE OF base_currency ON companies
        FOR EACH ROW
        EXECUTE FUNCTION prevent_base_currency_change_with_postings()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS companies_base_currency_immutable ON companies")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_company_base_currency_change()
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
    op.execute("DROP FUNCTION IF EXISTS prevent_base_currency_change_with_postings()")

    op.execute(
        """
        ALTER TABLE document_series
          DROP CONSTRAINT ck_document_series_type,
          ADD CONSTRAINT ck_document_series_type
            CHECK (document_type IN ('delivery_receipt', 'credit_note'))
        """
    )

    op.execute("DROP TRIGGER IF EXISTS trg_document_series_protected ON document_series")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION protect_document_series() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Document Series cannot be deleted';
          END IF;
          IF NEW.branch_id <> OLD.branch_id
             OR NEW.document_type <> OLD.document_type
             OR NEW.prefix <> OLD.prefix
             OR NEW.next_number <> OLD.next_number + 1 THEN
            RAISE EXCEPTION 'Document Series identity is immutable and sequence is monotonic';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_document_series_protected
        BEFORE UPDATE OR DELETE ON document_series
        FOR EACH ROW EXECUTE FUNCTION protect_document_series()
        """
    )

    op.execute("DROP TRIGGER IF EXISTS trg_document_series_audit_complete ON document_series")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION validate_document_series_audit() RETURNS trigger AS $$
        DECLARE missing_number integer;
        DECLARE too_high integer;
        BEGIN
          SELECT n INTO missing_number FROM generate_series(1,NEW.next_number - 1) n
          WHERE NOT EXISTS (
            SELECT 1 FROM document_series_number_audit a
            WHERE a.document_series_id = NEW.document_series_id
              AND a.series_number = n) LIMIT 1;
          SELECT max(series_number) INTO too_high FROM document_series_number_audit
            WHERE document_series_id = NEW.document_series_id
              AND series_number >= NEW.next_number;
          IF missing_number IS NOT NULL OR too_high IS NOT NULL THEN
            RAISE EXCEPTION 'Every consumed Document Series number must be audited exactly once';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_document_series_audit_complete
        AFTER INSERT OR UPDATE OF next_number ON document_series
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION validate_document_series_audit()
        """
    )

    op.execute(
        """
        ALTER TABLE document_series
          DROP CONSTRAINT ck_document_series_version_positive,
          DROP COLUMN version
        """
    )

    op.execute("DROP TABLE document_templates")

    op.execute(
        "ALTER TABLE branches DROP CONSTRAINT IF EXISTS ck_branches_timezone_not_empty"
    )
    op.execute("ALTER TABLE branches DROP COLUMN timezone")
    op.execute(
        "ALTER TABLE companies DROP CONSTRAINT IF EXISTS ck_companies_timezone_not_empty"
    )
    op.execute("ALTER TABLE companies DROP COLUMN timezone")
