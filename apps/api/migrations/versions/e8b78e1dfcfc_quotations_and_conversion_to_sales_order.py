"""quotations_and_conversion_to_sales_order

Revision ID: e8b78e1dfcfc
Revises: 1df3f2114a12
Create Date: 2026-08-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e8b78e1dfcfc"
down_revision: str | None = "1df3f2114a12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE quotations (
          quotation_id UUID PRIMARY KEY,
          branch_id UUID NOT NULL REFERENCES branches(branch_id),
          customer_id UUID NOT NULL REFERENCES customer_accounts(customer_id),
          status VARCHAR(20) NOT NULL DEFAULT 'draft',
          version INTEGER NOT NULL DEFAULT 1,
          document_series_id UUID NOT NULL REFERENCES document_series(document_series_id),
          series_number INTEGER NOT NULL,
          number VARCHAR(80) NOT NULL UNIQUE,
          expiry_date DATE NOT NULL,
          approved_revision_id UUID,
          converted_sales_order_id UUID REFERENCES sales_orders(sales_order_id),
          created_by VARCHAR(200) NOT NULL REFERENCES users(subject),
          updated_by VARCHAR(200) NOT NULL REFERENCES users(subject),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT ck_quotations_status
            CHECK (status IN ('draft','approved','converted','expired')),
          CONSTRAINT ck_quotations_version_positive CHECK (version > 0),
          CONSTRAINT ck_quotations_series_number_positive CHECK (series_number > 0),
          CONSTRAINT uq_quotations_document_series_number
            UNIQUE (document_series_id, series_number)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE quotation_revisions (
          quotation_revision_id UUID PRIMARY KEY,
          quotation_id UUID NOT NULL REFERENCES quotations(quotation_id),
          version INTEGER NOT NULL,
          branch_id UUID NOT NULL REFERENCES branches(branch_id),
          customer_id UUID NOT NULL REFERENCES customer_accounts(customer_id),
          customer_version INTEGER NOT NULL,
          delivery_address_version_id UUID NOT NULL
            REFERENCES customer_address_versions(address_version_id),
          delivery_address_snapshot JSONB NOT NULL,
          currency VARCHAR(3) NOT NULL,
          price_inclusion_mode VARCHAR(20) NOT NULL,
          price_list_version_id UUID NOT NULL
            REFERENCES price_list_versions(price_list_version_id),
          price_list_code VARCHAR(50) NOT NULL,
          price_list_version INTEGER NOT NULL,
          pricing_date DATE NOT NULL,
          payment_timing_default VARCHAR(30) NOT NULL,
          payment_timing_policy VARCHAR(30) NOT NULL,
          payment_timing_override_reason VARCHAR(500),
          payment_timing_overridden_by VARCHAR(200) REFERENCES users(subject),
          order_discount_amount NUMERIC(18, 6) NOT NULL,
          subtotal NUMERIC(24, 6) NOT NULL,
          discount_total NUMERIC(24, 6) NOT NULL,
          taxable_total NUMERIC(24, 6) NOT NULL,
          tax_total NUMERIC(24, 6) NOT NULL,
          grand_total NUMERIC(24, 6) NOT NULL,
          expiry_date DATE NOT NULL,
          calculation_contract_version INTEGER NOT NULL DEFAULT 1,
          actor_subject VARCHAR(200) NOT NULL REFERENCES users(subject),
          correlation_id VARCHAR(100) NOT NULL,
          idempotency_key VARCHAR(200) NOT NULL UNIQUE,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT ck_quotation_revisions_version_positive CHECK (version > 0),
          CONSTRAINT ck_quotation_revisions_inclusion_mode
            CHECK (price_inclusion_mode IN ('inclusive','exclusive')),
          CONSTRAINT ck_quotation_revisions_payment_default
            CHECK (payment_timing_default IN ('prepaid','cash_on_delivery','on_account')),
          CONSTRAINT ck_quotation_revisions_payment_policy
            CHECK (payment_timing_policy IN ('prepaid','cash_on_delivery','on_account')),
          CONSTRAINT ck_quotation_revisions_discount_nonnegative
            CHECK (order_discount_amount >= 0 AND discount_total >= 0),
          CONSTRAINT ck_quotation_revisions_totals_nonnegative
            CHECK (subtotal >= 0 AND taxable_total >= 0 AND tax_total >= 0 AND grand_total >= 0),
          CONSTRAINT uq_quotation_revision UNIQUE (quotation_id, version),
          CONSTRAINT uq_quotation_revision_ownership
            UNIQUE (quotation_id, quotation_revision_id),
          CONSTRAINT uq_quotation_revision_customer_ownership
            UNIQUE (quotation_id, quotation_revision_id, customer_id)
        )
        """
    )

    op.execute(
        """
        ALTER TABLE quotations
          ADD CONSTRAINT fk_quotations_approved_revision
            FOREIGN KEY (quotation_id, approved_revision_id)
            REFERENCES quotation_revisions(quotation_id, quotation_revision_id)
        """
    )

    op.execute(
        """
        CREATE TABLE quotation_line_revisions (
          quotation_line_revision_id UUID PRIMARY KEY,
          quotation_revision_id UUID NOT NULL
            REFERENCES quotation_revisions(quotation_revision_id),
          line_id UUID NOT NULL,
          line_position INTEGER NOT NULL,
          sku_id UUID NOT NULL REFERENCES skus(sku_id),
          sku_code VARCHAR(50) NOT NULL,
          sku_name VARCHAR(200) NOT NULL,
          entered_quantity NUMERIC(18, 6) NOT NULL,
          entered_unit VARCHAR(30) NOT NULL,
          quantity_base NUMERIC(18, 6) NOT NULL,
          conversion_snapshot JSONB NOT NULL,
          price_list_line_id UUID NOT NULL
            REFERENCES price_list_lines(price_list_line_id),
          list_unit_price NUMERIC(18, 6) NOT NULL,
          floor_unit_price NUMERIC(18, 6),
          manual_override_unit_price NUMERIC(18, 6),
          price_override_reason VARCHAR(500),
          effective_unit_price NUMERIC(18, 6) NOT NULL,
          price_source VARCHAR(20) NOT NULL,
          below_floor BOOLEAN NOT NULL,
          allocated_discount NUMERIC(24, 6) NOT NULL,
          tax_snapshot JSONB NOT NULL,
          calculation_snapshot JSONB NOT NULL,
          taxable_amount NUMERIC(24, 6) NOT NULL,
          tax_amount NUMERIC(24, 6) NOT NULL,
          line_total NUMERIC(24, 6) NOT NULL,
          CONSTRAINT ck_quotation_line_revisions_position CHECK (line_position > 0),
          CONSTRAINT ck_quotation_line_revisions_quantity
            CHECK (entered_quantity > 0 AND quantity_base > 0),
          CONSTRAINT ck_quotation_line_revisions_price
            CHECK (list_unit_price >= 0 AND effective_unit_price >= 0),
          CONSTRAINT ck_quotation_line_revisions_price_source
            CHECK (price_source IN ('customer','branch')),
          CONSTRAINT ck_quotation_line_revisions_amounts
            CHECK (allocated_discount >= 0 AND taxable_amount >= 0
                   AND tax_amount >= 0 AND line_total >= 0),
          CONSTRAINT uq_quotation_line_revision_identity
            UNIQUE (quotation_revision_id, line_id),
          CONSTRAINT uq_quotation_line_revision_position
            UNIQUE (quotation_revision_id, line_position),
          CONSTRAINT uq_quotation_line_revision_sku_ownership
            UNIQUE (quotation_revision_id, line_id, sku_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE quotation_approvals (
          quotation_approval_id UUID PRIMARY KEY,
          quotation_id UUID NOT NULL REFERENCES quotations(quotation_id),
          quotation_revision_id UUID NOT NULL UNIQUE,
          customer_id UUID NOT NULL REFERENCES customer_accounts(customer_id),
          maker_subject VARCHAR(200) NOT NULL REFERENCES users(subject),
          approved_by VARCHAR(200) NOT NULL REFERENCES users(subject),
          order_total NUMERIC(24, 6) NOT NULL,
          correlation_id VARCHAR(100) NOT NULL,
          idempotency_key VARCHAR(200) NOT NULL UNIQUE,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT ck_quotation_approvals_order_total CHECK (order_total >= 0),
          CONSTRAINT uq_quotation_approval_customer_ownership
            UNIQUE (quotation_approval_id, quotation_id, customer_id),
          FOREIGN KEY (quotation_id, quotation_revision_id, customer_id)
            REFERENCES quotation_revisions(quotation_id, quotation_revision_id, customer_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE quotation_approval_exceptions (
          exception_approval_id UUID PRIMARY KEY,
          quotation_approval_id UUID NOT NULL
            REFERENCES quotation_approvals(quotation_approval_id),
          exception_type VARCHAR(30) NOT NULL,
          maker_subject VARCHAR(200) NOT NULL REFERENCES users(subject),
          approved_by VARCHAR(200) NOT NULL REFERENCES users(subject),
          reason VARCHAR(500) NOT NULL,
          exception_amount NUMERIC(24, 6) NOT NULL,
          exception_percentage NUMERIC(9, 6),
          authority_snapshot JSONB NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT ck_quotation_approval_exceptions_type
            CHECK (exception_type IN ('discount','below_floor')),
          CONSTRAINT ck_quotation_approval_exceptions_amount CHECK (exception_amount >= 0),
          CONSTRAINT ck_quotation_approval_exceptions_percentage
            CHECK (exception_percentage IS NULL
                   OR (exception_percentage >= 0 AND exception_percentage <= 100)),
          CONSTRAINT ck_quotation_approval_exceptions_maker_checker
            CHECK (maker_subject <> approved_by),
          CONSTRAINT uq_quotation_approval_exception_type
            UNIQUE (quotation_approval_id, exception_type)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE quotation_conversion_events (
          conversion_event_id UUID PRIMARY KEY,
          quotation_id UUID NOT NULL REFERENCES quotations(quotation_id),
          quotation_revision_id UUID NOT NULL,
          sales_order_id UUID NOT NULL REFERENCES sales_orders(sales_order_id),
          sales_order_revision_id UUID NOT NULL,
          converted_by VARCHAR(200) NOT NULL REFERENCES users(subject),
          correlation_id VARCHAR(100) NOT NULL,
          idempotency_key VARCHAR(200) NOT NULL UNIQUE,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT uq_quotation_conversion_quotation UNIQUE (quotation_id),
          CONSTRAINT uq_quotation_conversion_sales_order UNIQUE (sales_order_id),
          FOREIGN KEY (quotation_id, quotation_revision_id)
            REFERENCES quotation_revisions(quotation_id, quotation_revision_id),
          FOREIGN KEY (sales_order_id, sales_order_revision_id)
            REFERENCES sales_order_revisions(sales_order_id, sales_order_revision_id)
        )
        """
    )

    op.execute(
        """
        ALTER TABLE document_series_number_audit
          ADD COLUMN quotation_id UUID REFERENCES quotations(quotation_id)
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
            (status = 'issued' AND (
              delivery_receipt_id IS NOT NULL
              OR credit_note_id IS NOT NULL
              OR quotation_id IS NOT NULL
            ))
            OR (status IN ('voided','skipped') AND reason IS NOT NULL)
          )
        """
    )

    op.execute(
        """
        CREATE INDEX idx_quotations_customer
          ON quotations(customer_id, created_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX idx_quotation_revisions_quotation
          ON quotation_revisions(quotation_id, version DESC)
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_quotation_snapshot_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          RAISE EXCEPTION 'Quotation pricing and revision snapshots are immutable'
            USING ERRCODE = 'check_violation';
        END;
        $$
        """
    )

    for table_name in (
        "quotation_revisions",
        "quotation_line_revisions",
        "quotation_approvals",
        "quotation_approval_exceptions",
        "quotation_conversion_events",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_immutable
              BEFORE UPDATE OR DELETE ON {table_name}
              FOR EACH ROW
              EXECUTE FUNCTION prevent_quotation_snapshot_mutation()
            """
        )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_quotation_converted_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF OLD.status = 'converted' THEN
            RAISE EXCEPTION 'Converted quotations cannot be modified'
              USING ERRCODE = 'check_violation';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_quotations_converted_immutable
          BEFORE UPDATE OR DELETE ON quotations
          FOR EACH ROW
          EXECUTE FUNCTION prevent_quotation_converted_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_quotations_converted_immutable ON quotations")
    op.execute("DROP FUNCTION IF EXISTS prevent_quotation_converted_mutation()")
    for table_name in (
        "quotation_conversion_events",
        "quotation_approval_exceptions",
        "quotation_approvals",
        "quotation_line_revisions",
        "quotation_revisions",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_immutable ON {table_name}")
    op.execute("DROP FUNCTION IF EXISTS prevent_quotation_snapshot_mutation()")
    op.execute("DROP INDEX IF EXISTS idx_quotation_revisions_quotation")
    op.execute("DROP INDEX IF EXISTS idx_quotations_customer")
    op.execute(
        """
        ALTER TABLE quotations
          DROP CONSTRAINT IF EXISTS fk_quotations_approved_revision
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
            OR (status IN ('voided','skipped') AND reason IS NOT NULL)
          )
        """
    )
    op.execute("ALTER TABLE document_series_number_audit DROP COLUMN quotation_id")
    op.execute("DROP TABLE IF EXISTS quotation_conversion_events")
    op.execute("DROP TABLE IF EXISTS quotation_approval_exceptions")
    op.execute("DROP TABLE IF EXISTS quotation_approvals")
    op.execute("DROP TABLE IF EXISTS quotation_line_revisions")
    op.execute("DROP TABLE IF EXISTS quotation_revisions")
    op.execute("DROP TABLE IF EXISTS quotations")
