"""Add immutable Return Requests and maker-checker authorizations.

Revision ID: e93736a741bd
Revises: d524a29c32b8
Create Date: 2026-08-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e93736a741bd"
down_revision: str | None = "d524a29c32b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO capabilities(code)
        VALUES ('returns:request'), ('returns:authorize')
        ON CONFLICT (code) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO role_template_capabilities(role_template_id, capability_code)
        SELECT template.role_template_id, capability.code
        FROM role_templates template
        CROSS JOIN (VALUES ('returns:request'), ('returns:authorize')) capability(code)
        WHERE template.code = 'WAREHOUSE_SUPERVISOR'
        ON CONFLICT DO NOTHING
        """
    )
    op.execute(
        """
        CREATE TABLE return_reasons (
          code varchar(50) PRIMARY KEY,
          label varchar(200) NOT NULL,
          is_active boolean NOT NULL DEFAULT true
        )
        """
    )
    op.execute(
        """
        CREATE TABLE return_responsible_parties (
          code varchar(50) PRIMARY KEY,
          label varchar(200) NOT NULL,
          is_active boolean NOT NULL DEFAULT true
        )
        """
    )
    op.execute(
        """
        INSERT INTO return_reasons(code, label) VALUES
          ('TRANSIT_DAMAGE', 'Transit damage'),
          ('PRODUCT_DEFECT', 'Product defect'),
          ('WRONG_ITEM', 'Wrong item'),
          ('EXCESS_DELIVERY', 'Excess delivery'),
          ('CUSTOMER_ERROR', 'Customer error')
        """
    )
    op.execute(
        """
        INSERT INTO return_responsible_parties(code, label) VALUES
          ('CUSTOMER', 'Customer'), ('CARRIER', 'Carrier'),
          ('SUPPLIER', 'Supplier'), ('WAREHOUSE', 'Warehouse'),
          ('COMPANY', 'Company')
        """
    )
    op.execute(
        """
        CREATE TABLE return_requests (
          return_request_id uuid PRIMARY KEY,
          delivery_receipt_id uuid NOT NULL REFERENCES delivery_receipts(delivery_receipt_id),
          confirmation_id uuid NOT NULL REFERENCES delivery_confirmations(confirmation_id),
          delivery_id uuid NOT NULL REFERENCES delivery_dispatches(delivery_id),
          branch_id uuid NOT NULL REFERENCES branches(branch_id),
          warehouse_id uuid NOT NULL REFERENCES warehouses(warehouse_id),
          reason_code varchar(50) NOT NULL CHECK (btrim(reason_code) <> ''),
          reason_label varchar(200) NOT NULL CHECK (btrim(reason_label) <> ''),
          responsible_party_code varchar(50) NOT NULL
            CHECK (btrim(responsible_party_code) <> ''),
          responsible_party_label varchar(200) NOT NULL
            CHECK (btrim(responsible_party_label) <> ''),
          notes varchar(2000),
          requested_by varchar(200) NOT NULL REFERENCES users(subject),
          base_currency varchar(3) NOT NULL,
          affected_value_base_currency numeric(24,6) NOT NULL
            CHECK (affected_value_base_currency >= 0),
          correlation_id varchar(100) NOT NULL,
          idempotency_key varchar(200) NOT NULL,
          requested_at timestamptz NOT NULL DEFAULT now(),
          sealed_at timestamptz,
          CONSTRAINT uq_return_request_actor_key UNIQUE(requested_by, idempotency_key)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE return_request_lines (
          return_request_line_id uuid PRIMARY KEY,
          return_request_id uuid NOT NULL REFERENCES return_requests(return_request_id),
          delivery_line_id uuid NOT NULL REFERENCES delivery_lines(delivery_line_id),
          line_id uuid NOT NULL,
          sku_id uuid NOT NULL REFERENCES skus(sku_id),
          quantity_base numeric(18,6) NOT NULL,
          delivered_quantity_base numeric(18,6) NOT NULL,
          affected_value_base_currency numeric(24,6) NOT NULL,
          CONSTRAINT uq_return_request_line UNIQUE(return_request_id, delivery_line_id),
          CONSTRAINT ck_return_request_line_quantity CHECK (
            quantity_base > 0 AND delivered_quantity_base > 0
            AND quantity_base <= delivered_quantity_base
          ),
          CONSTRAINT ck_return_request_line_value_nonnegative CHECK (
            affected_value_base_currency >= 0
          )
        )
        """
    )
    op.execute(
        """
        CREATE TABLE return_authorizations (
          return_request_id uuid PRIMARY KEY REFERENCES return_requests(return_request_id),
          authorized_by varchar(200) NOT NULL REFERENCES users(subject),
          approval_authority_id uuid NOT NULL
            REFERENCES approval_authorities(approval_authority_id),
          idempotency_key varchar(200) NOT NULL,
          correlation_id varchar(100) NOT NULL,
          authorized_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_return_authorization_actor_key UNIQUE(authorized_by, idempotency_key)
        )
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_sealed_return_request_mutation()
          RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.sealed_at IS NOT NULL THEN
            RAISE EXCEPTION 'sealed Return Request is immutable';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER reject_sealed_return_request_mutation
          BEFORE UPDATE OR DELETE ON return_requests
          FOR EACH ROW EXECUTE FUNCTION reject_sealed_return_request_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION currency_minor_scale(currency_code varchar(3))
          RETURNS integer LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
          SELECT CASE
            WHEN currency_code IN (
              'BIF','CLP','DJF','GNF','ISK','JPY','KMF','KRW','PYG','RWF','UGX',
              'VND','VUV','XAF','XOF','XPF'
            ) THEN 0
            WHEN currency_code IN ('BHD','IQD','JOD','KWD','LYD','OMR','TND') THEN 3
            WHEN currency_code IN ('CLF','UYW') THEN 4
            ELSE 2
          END
          $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION validate_return_request_seal()
          RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE source_record record;
        DECLARE invalid_lines integer;
        DECLARE expected_total numeric(24,6);
        DECLARE expected_currency varchar(3);
        BEGIN
          IF NEW.sealed_at IS NULL OR OLD.sealed_at IS NOT NULL THEN
            RETURN NEW;
          END IF;
          SELECT receipt.confirmation_id, receipt.branch_id, receipt.correction_id,
                 confirmation.delivery_id, dispatch.warehouse_id
          INTO source_record
          FROM delivery_receipts receipt
          JOIN delivery_confirmations confirmation USING (confirmation_id)
          JOIN delivery_dispatches dispatch USING (delivery_id)
          WHERE receipt.delivery_receipt_id = NEW.delivery_receipt_id;
          SELECT count(*) INTO invalid_lines
          FROM return_request_lines requested
          JOIN delivery_lines delivered USING (delivery_line_id)
          LEFT JOIN delivery_confirmation_lines original
            ON original.confirmation_id = source_record.confirmation_id
           AND original.delivery_line_id = requested.delivery_line_id
           AND source_record.correction_id IS NULL
          LEFT JOIN delivery_correction_lines corrected
            ON corrected.correction_id = source_record.correction_id
           AND corrected.delivery_line_id = requested.delivery_line_id
          WHERE requested.return_request_id = NEW.return_request_id
            AND (delivered.delivery_id IS DISTINCT FROM NEW.delivery_id
              OR requested.line_id IS DISTINCT FROM delivered.line_id
              OR requested.sku_id IS DISTINCT FROM delivered.sku_id
              OR requested.delivered_quantity_base IS DISTINCT FROM
                 coalesce(corrected.accepted_quantity_base, original.accepted_quantity_base));
          SELECT coalesce(sum(round(
                   revision_line.line_total * requested.quantity_base
                   / revision_line.quantity_base, currency_minor_scale(NEW.base_currency)
                 )), 0), revision.currency
          INTO expected_total, expected_currency
          FROM return_request_lines requested
          JOIN sales_order_line_revisions revision_line
            ON revision_line.sales_order_revision_id = (
              SELECT sales_order_revision_id FROM delivery_dispatches
              WHERE delivery_id = NEW.delivery_id
            )
           AND revision_line.line_id = requested.line_id
          JOIN sales_order_revisions revision
            ON revision.sales_order_revision_id = revision_line.sales_order_revision_id
          WHERE requested.return_request_id = NEW.return_request_id
          GROUP BY revision.currency;
          IF NOT FOUND
             OR source_record.confirmation_id IS DISTINCT FROM NEW.confirmation_id
             OR source_record.delivery_id IS DISTINCT FROM NEW.delivery_id
             OR source_record.branch_id IS DISTINCT FROM NEW.branch_id
             OR source_record.warehouse_id IS DISTINCT FROM NEW.warehouse_id
             OR expected_currency IS DISTINCT FROM NEW.base_currency
             OR expected_currency IS DISTINCT FROM (
               SELECT base_currency FROM companies LIMIT 1
             )
             OR expected_total IS DISTINCT FROM NEW.affected_value_base_currency
             OR EXISTS (
               SELECT 1 FROM return_request_lines requested
               JOIN sales_order_line_revisions revision_line
                 ON revision_line.sales_order_revision_id = (
                   SELECT sales_order_revision_id FROM delivery_dispatches
                   WHERE delivery_id = NEW.delivery_id
                 ) AND revision_line.line_id = requested.line_id
               JOIN sales_order_revisions revision
                 ON revision.sales_order_revision_id = revision_line.sales_order_revision_id
               WHERE requested.return_request_id = NEW.return_request_id
                 AND requested.affected_value_base_currency IS DISTINCT FROM round(
                   revision_line.line_total * requested.quantity_base
                   / revision_line.quantity_base, currency_minor_scale(NEW.base_currency)
                 )
             )
             OR NOT EXISTS (
               SELECT 1 FROM return_reasons
               WHERE code = NEW.reason_code AND label = NEW.reason_label AND is_active
             )
             OR NOT EXISTS (
               SELECT 1 FROM return_responsible_parties
               WHERE code = NEW.responsible_party_code
                 AND label = NEW.responsible_party_label AND is_active
             )
             OR invalid_lines <> 0
             OR NOT EXISTS (
               SELECT 1 FROM return_request_lines
               WHERE return_request_id = NEW.return_request_id
             ) THEN
            RAISE EXCEPTION 'Return Request source ownership is invalid';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER validate_return_request_seal
          BEFORE UPDATE OF sealed_at ON return_requests
          FOR EACH ROW EXECUTE FUNCTION validate_return_request_seal()
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_sealed_return_request_line_mutation()
          RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE request_is_sealed boolean;
        BEGIN
          SELECT sealed_at IS NOT NULL INTO request_is_sealed
          FROM return_requests
          WHERE return_request_id = coalesce(OLD.return_request_id, NEW.return_request_id);
          IF request_is_sealed THEN
            RAISE EXCEPTION 'sealed Return Request lines are immutable';
          END IF;
          RETURN coalesce(NEW, OLD);
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER reject_sealed_return_request_line_mutation
          BEFORE INSERT OR UPDATE OR DELETE ON return_request_lines
          FOR EACH ROW EXECUTE FUNCTION reject_sealed_return_request_line_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION validate_return_authorization()
          RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE request_record record;
        DECLARE authority_record record;
        DECLARE invalid_lines integer;
        BEGIN
          SELECT * INTO request_record FROM return_requests
          WHERE return_request_id = NEW.return_request_id;
          PERFORM pg_advisory_xact_lock(
            hashtextextended('delivery-receipt-chain:' || request_record.delivery_receipt_id, 0)
          );
          SELECT * INTO authority_record FROM approval_authorities
          WHERE approval_authority_id = NEW.approval_authority_id;
          SELECT count(*) INTO invalid_lines
          FROM return_request_lines candidate
          WHERE candidate.return_request_id = NEW.return_request_id
            AND candidate.quantity_base > candidate.delivered_quantity_base - coalesce((
              SELECT sum(prior.quantity_base)
              FROM return_request_lines prior
              JOIN return_requests prior_request USING (return_request_id)
              JOIN return_authorizations prior_authorization USING (return_request_id)
              WHERE prior_request.delivery_receipt_id = request_record.delivery_receipt_id
                AND prior.delivery_line_id = candidate.delivery_line_id
            ), 0);
          IF request_record.sealed_at IS NULL
             OR EXISTS (
               SELECT 1 FROM delivery_receipts successor
               WHERE successor.corrects_delivery_receipt_id = request_record.delivery_receipt_id
             )
             OR EXISTS (
               SELECT 1 FROM delivery_corrections correction
               WHERE correction.original_delivery_receipt_id = request_record.delivery_receipt_id
             )
             OR request_record.requested_by = NEW.authorized_by
             OR authority_record.user_subject IS DISTINCT FROM NEW.authorized_by
             OR authority_record.capability_code IS DISTINCT FROM 'returns:authorize'
             OR authority_record.branch_id IS DISTINCT FROM request_record.branch_id
             OR (authority_record.warehouse_id IS NOT NULL AND
                 authority_record.warehouse_id IS DISTINCT FROM request_record.warehouse_id)
             OR (authority_record.maximum_amount IS NOT NULL AND
                 authority_record.maximum_amount < request_record.affected_value_base_currency)
             OR invalid_lines <> 0 THEN
            RAISE EXCEPTION 'Return Authorization violates eligibility or maker-checker authority';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_return_authorization_mutation()
          RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'Return Authorization is immutable';
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER reject_return_authorization_mutation
          BEFORE UPDATE OR DELETE ON return_authorizations
          FOR EACH ROW EXECUTE FUNCTION reject_return_authorization_mutation()
        """
    )
    op.execute(
        """
        CREATE TRIGGER validate_return_authorization
          BEFORE INSERT ON return_authorizations
          FOR EACH ROW EXECUTE FUNCTION validate_return_authorization()
        """
    )


def downgrade() -> None:
    op.execute(
        """DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM return_requests) THEN
          RAISE EXCEPTION
            'Cannot downgrade while immutable Return Request history exists';
        END IF;
        END $$"""
    )
    op.execute("DROP FUNCTION IF EXISTS validate_return_authorization() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS reject_return_authorization_mutation() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS validate_return_request_seal() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS currency_minor_scale(varchar)")
    op.execute("DROP FUNCTION IF EXISTS reject_sealed_return_request_line_mutation() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS reject_sealed_return_request_mutation() CASCADE")
    op.execute("DROP TABLE return_authorizations")
    op.execute("DROP TABLE return_request_lines")
    op.execute("DROP TABLE return_requests")
    op.execute("DROP TABLE IF EXISTS return_responsible_parties")
    op.execute("DROP TABLE IF EXISTS return_reasons")
    op.execute(
        """
        DELETE FROM role_template_capabilities
        WHERE capability_code IN ('returns:request', 'returns:authorize')
        """
    )
    op.execute(
        """
        DELETE FROM capabilities capability
        WHERE capability.code IN ('returns:request', 'returns:authorize')
          AND NOT EXISTS (
            SELECT 1 FROM role_template_capabilities assignment
            WHERE assignment.capability_code = capability.code
          )
          AND NOT EXISTS (
            SELECT 1 FROM approval_authorities authority
            WHERE authority.capability_code = capability.code
          )
        """
    )
