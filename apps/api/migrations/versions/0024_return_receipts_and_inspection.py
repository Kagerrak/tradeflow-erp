"""Add Return Receipts and inspection custody.

Revision ID: 0024
Revises: e93736a741bd
Create Date: 2026-08-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0024"
down_revision: str | None = "e93736a741bd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO capabilities(code)
        VALUES ('returns:receive')
        ON CONFLICT (code) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO role_template_capabilities(role_template_id, capability_code)
        SELECT template.role_template_id, capability.code
        FROM role_templates template
        CROSS JOIN (VALUES ('returns:receive')) capability(code)
        WHERE template.code = 'WAREHOUSE_SUPERVISOR'
        ON CONFLICT DO NOTHING
        """
    )

    op.execute(
        """
        CREATE TABLE return_request_evidence (
          evidence_id uuid PRIMARY KEY,
          return_request_id uuid NOT NULL REFERENCES return_requests(return_request_id),
          kind varchar(30) NOT NULL CHECK (kind IN ('photo')),
          object_key varchar(500) NOT NULL UNIQUE,
          content_type varchar(100) NOT NULL,
          size_bytes integer NOT NULL CHECK (size_bytes > 0),
          sha256 varchar(64) NOT NULL,
          upload_id varchar(500),
          captured_by varchar(200) NOT NULL REFERENCES users(subject),
          device_captured_at timestamptz NOT NULL,
          status varchar(30) NOT NULL CHECK (status IN ('uploading','verified','rejected')),
          created_at timestamptz NOT NULL DEFAULT now(),
          verified_at timestamptz,
          CONSTRAINT uq_return_request_evidence UNIQUE (return_request_id, evidence_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE return_receipts (
          return_receipt_id uuid PRIMARY KEY,
          return_request_id uuid NOT NULL UNIQUE REFERENCES return_requests(return_request_id),
          received_by varchar(200) NOT NULL REFERENCES users(subject),
          received_at timestamptz NOT NULL,
          notes varchar(2000),
          correlation_id varchar(100) NOT NULL,
          idempotency_key varchar(200) NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_return_receipts_actor_key UNIQUE (received_by, idempotency_key)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE return_receipt_lines (
          return_receipt_line_id uuid PRIMARY KEY,
          return_receipt_id uuid NOT NULL REFERENCES return_receipts(return_receipt_id),
          return_request_line_id uuid NOT NULL REFERENCES
            return_request_lines(return_request_line_id),
          received_quantity_base numeric(18,6) NOT NULL,
          outcome varchar(30) NOT NULL CHECK (
            outcome IN ('restock','quarantine','damaged','rejected')),
          notes varchar(2000),
          movement_id uuid REFERENCES stock_movements(movement_id),
          CONSTRAINT uq_return_receipt_line UNIQUE (return_receipt_id, return_request_line_id),
          CONSTRAINT ck_return_receipt_line_quantity CHECK (received_quantity_base >= 0),
          CONSTRAINT ck_return_receipt_line_outcome_shape CHECK (
            (outcome = 'rejected' AND received_quantity_base = 0 AND movement_id IS NULL)
            OR (outcome <> 'rejected' AND received_quantity_base > 0 AND movement_id IS NOT NULL)
          )
        )
        """
    )

    op.execute(
        """
        CREATE TABLE return_receipt_evidence (
          return_receipt_id uuid NOT NULL REFERENCES return_receipts(return_receipt_id),
          evidence_id uuid NOT NULL REFERENCES return_request_evidence(evidence_id),
          PRIMARY KEY (return_receipt_id, evidence_id)
        )
        """
    )

    op.execute("CREATE INDEX ix_return_receipts_request ON return_receipts(return_request_id)")
    op.execute(
        "CREATE INDEX ix_return_receipt_lines_request_line "
        "ON return_receipt_lines(return_request_line_id)"
    )

    op.execute(
        """
        CREATE FUNCTION reject_return_receipt_mutation()
          RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'Return Receipt is immutable';
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER reject_return_receipt_mutation
          BEFORE UPDATE OR DELETE ON return_receipts
          FOR EACH ROW EXECUTE FUNCTION reject_return_receipt_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_return_receipt_line_mutation()
          RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'Return Receipt lines are immutable';
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER reject_return_receipt_line_mutation
          BEFORE UPDATE OR DELETE ON return_receipt_lines
          FOR EACH ROW EXECUTE FUNCTION reject_return_receipt_line_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_return_receipt_evidence_mutation()
          RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'Return Receipt evidence links are immutable';
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER reject_return_receipt_evidence_mutation
          BEFORE UPDATE OR DELETE ON return_receipt_evidence
          FOR EACH ROW EXECUTE FUNCTION reject_return_receipt_evidence_mutation()
        """
    )
    op.execute("ALTER TABLE stock_movements DROP CONSTRAINT ck_stock_movements_type")
    op.execute("ALTER TABLE stock_movements DROP CONSTRAINT ck_stock_movements_leg")
    op.execute(
        """
        ALTER TABLE stock_movements ADD CONSTRAINT ck_stock_movements_type CHECK (
          movement_type IN ('opening_stock','pick','pick_reversal','dispatch',
            'delivery_confirmation','delivery_exception','return_to_warehouse',
            'investigation_resolution','delivery_correction','goods_receipt','transfer',
            'inventory_adjustment','authorized_return_receipt')
        )
        """
    )
    op.execute(
        """
        ALTER TABLE stock_movements ADD CONSTRAINT ck_stock_movements_leg CHECK (
          (movement_type = 'opening_stock' AND movement_leg = 'opening_in')
          OR (movement_type = 'pick' AND movement_leg IN
            ('pick_available_out','pick_staging_in'))
          OR (movement_type = 'pick_reversal' AND movement_leg IN
            ('pick_reversal_staging_out','pick_reversal_available_in'))
          OR (movement_type = 'dispatch' AND movement_leg IN
            ('dispatch_staging_out','dispatch_transit_in'))
          OR (movement_type = 'delivery_confirmation' AND movement_leg = 'delivery_outbound')
          OR (movement_type = 'delivery_exception' AND movement_leg IN
            ('exception_transit_out','exception_investigation_in'))
          OR (movement_type = 'return_to_warehouse' AND movement_leg IN
            ('return_transit_out','return_quarantine_in'))
          OR (movement_type = 'investigation_resolution' AND movement_leg IN
            ('recovery_investigation_out','recovery_quarantine_in',
             'carrier_claim_investigation_out','inventory_adjustment_investigation_out'))
          OR (movement_type = 'delivery_correction' AND movement_leg IN
            ('correction_accepted_reversal_in',
             'correction_exception_reversal_transit_in',
             'correction_exception_reversal_investigation_out',
             'correction_accepted_replacement_out',
             'correction_exception_replacement_transit_out',
             'correction_exception_replacement_investigation_in'))
          OR (movement_type = 'goods_receipt' AND movement_leg = 'goods_receipt_in')
          OR (movement_type = 'transfer' AND movement_leg IN
            ('transfer_source_out','transfer_in_transit_in',
             'transfer_in_transit_out','transfer_destination_in'))
          OR (movement_type = 'inventory_adjustment' AND movement_leg IN
            ('adjustment_surplus_in','adjustment_shortage_out',
             'adjustment_surplus_reversal_out','adjustment_shortage_reversal_in'))
          OR (movement_type = 'authorized_return_receipt' AND movement_leg IN
            ('authorized_return_available_in','authorized_return_quarantine_in'))
        )
        """
    )


def downgrade() -> None:
    op.execute(
        """DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM return_receipts) THEN
          RAISE EXCEPTION 'Cannot downgrade while immutable Return Receipt history exists';
        END IF;
        END $$"""
    )
    op.execute("ALTER TABLE stock_movements DROP CONSTRAINT ck_stock_movements_type")
    op.execute("ALTER TABLE stock_movements DROP CONSTRAINT ck_stock_movements_leg")
    op.execute(
        """
        ALTER TABLE stock_movements ADD CONSTRAINT ck_stock_movements_type CHECK (
          movement_type IN ('opening_stock','pick','pick_reversal','dispatch',
            'delivery_confirmation','delivery_exception','return_to_warehouse',
            'investigation_resolution','delivery_correction','goods_receipt','transfer',
            'inventory_adjustment')
        )
        """
    )
    op.execute(
        """
        ALTER TABLE stock_movements ADD CONSTRAINT ck_stock_movements_leg CHECK (
          (movement_type = 'opening_stock' AND movement_leg = 'opening_in')
          OR (movement_type = 'pick' AND movement_leg IN
            ('pick_available_out','pick_staging_in'))
          OR (movement_type = 'pick_reversal' AND movement_leg IN
            ('pick_reversal_staging_out','pick_reversal_available_in'))
          OR (movement_type = 'dispatch' AND movement_leg IN
            ('dispatch_staging_out','dispatch_transit_in'))
          OR (movement_type = 'delivery_confirmation' AND movement_leg = 'delivery_outbound')
          OR (movement_type = 'delivery_exception' AND movement_leg IN
            ('exception_transit_out','exception_investigation_in'))
          OR (movement_type = 'return_to_warehouse' AND movement_leg IN
            ('return_transit_out','return_quarantine_in'))
          OR (movement_type = 'investigation_resolution' AND movement_leg IN
            ('recovery_investigation_out','recovery_quarantine_in',
             'carrier_claim_investigation_out','inventory_adjustment_investigation_out'))
          OR (movement_type = 'delivery_correction' AND movement_leg IN
            ('correction_accepted_reversal_in',
             'correction_exception_reversal_transit_in',
             'correction_exception_reversal_investigation_out',
             'correction_accepted_replacement_out',
             'correction_exception_replacement_transit_out',
             'correction_exception_replacement_investigation_in'))
          OR (movement_type = 'goods_receipt' AND movement_leg = 'goods_receipt_in')
          OR (movement_type = 'transfer' AND movement_leg IN
            ('transfer_source_out','transfer_in_transit_in',
             'transfer_in_transit_out','transfer_destination_in'))
          OR (movement_type = 'inventory_adjustment' AND movement_leg IN
            ('adjustment_surplus_in','adjustment_shortage_out',
             'adjustment_surplus_reversal_out','adjustment_shortage_reversal_in'))
        )
        """
    )
    op.execute(
        "DROP TRIGGER IF EXISTS reject_return_request_evidence_mutation ON return_request_evidence"
    )
    op.execute("DROP FUNCTION IF EXISTS reject_return_request_evidence_mutation()")
    op.execute(
        "DROP TRIGGER IF EXISTS reject_return_receipt_evidence_mutation ON return_receipt_evidence"
    )
    op.execute("DROP FUNCTION IF EXISTS reject_return_receipt_evidence_mutation()")
    op.execute("DROP TRIGGER IF EXISTS reject_return_receipt_line_mutation ON return_receipt_lines")
    op.execute("DROP FUNCTION IF EXISTS reject_return_receipt_line_mutation()")
    op.execute("DROP TRIGGER IF EXISTS reject_return_receipt_mutation ON return_receipts")
    op.execute("DROP FUNCTION IF EXISTS reject_return_receipt_mutation()")
    op.execute("DROP TABLE return_receipt_evidence")
    op.execute("DROP TABLE return_receipt_lines")
    op.execute("DROP TABLE return_receipts")
    op.execute("DROP TABLE return_request_evidence")
    op.execute(
        """
        DELETE FROM role_template_capabilities
        WHERE capability_code = 'returns:receive'
        """
    )
    op.execute(
        """
        DELETE FROM capabilities capability
        WHERE capability.code = 'returns:receive'
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
