"""Create accepted Delivery Confirmation, receipt, and outbox ledgers.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    statements = """
        ALTER TABLE stock_movements DROP CONSTRAINT ck_stock_movements_type;
        ALTER TABLE stock_movements DROP CONSTRAINT ck_stock_movements_leg;
        ALTER TABLE stock_movements ADD CONSTRAINT ck_stock_movements_type CHECK (
          movement_type IN ('opening_stock','pick','pick_reversal','dispatch',
                            'delivery_confirmation')
        );
        ALTER TABLE stock_movements ADD CONSTRAINT ck_stock_movements_leg CHECK (
          (movement_type = 'opening_stock' AND movement_leg = 'opening_in')
          OR (movement_type = 'pick'
              AND movement_leg IN ('pick_available_out','pick_staging_in'))
          OR (movement_type = 'pick_reversal'
              AND movement_leg IN ('pick_reversal_staging_out',
                                   'pick_reversal_available_in'))
          OR (movement_type = 'dispatch'
              AND movement_leg IN ('dispatch_staging_out','dispatch_transit_in'))
          OR (movement_type = 'delivery_confirmation'
              AND movement_leg = 'delivery_outbound')
        );

        ALTER TABLE fulfillment_order_state
          ADD COLUMN delivered_quantity_base numeric(18,6) NOT NULL DEFAULT 0;
        ALTER TABLE fulfillment_order_state
          DROP CONSTRAINT ck_fulfillment_order_state_status;
        ALTER TABLE fulfillment_order_state
          ADD CONSTRAINT ck_fulfillment_order_state_status CHECK (
            status IN ('reserved','payment_ready','pick_released','partially_picked',
                       'picked','partially_dispatched','dispatched','delivered',
                       'payment_hold','cancelled')
          );
        ALTER TABLE fulfillment_order_state
          DROP CONSTRAINT ck_fulfillment_order_state_amounts;
        ALTER TABLE fulfillment_order_state
          ADD CONSTRAINT ck_fulfillment_order_state_amounts CHECK (
            reserved_quantity_base >= 0 AND backorder_quantity_base >= 0
            AND covered_amount >= 0 AND picked_quantity_base >= 0
            AND dispatched_quantity_base >= 0 AND delivered_quantity_base >= 0
            AND dispatched_quantity_base <= picked_quantity_base
            AND delivered_quantity_base <= dispatched_quantity_base
          );

        ALTER TABLE delivery_state DROP CONSTRAINT ck_delivery_state_status;
        ALTER TABLE delivery_state ADD CONSTRAINT ck_delivery_state_status
          CHECK (status IN ('dispatched','confirmed'));

        CREATE TABLE delivery_evidence (
          evidence_id uuid PRIMARY KEY,
          delivery_id uuid NOT NULL REFERENCES delivery_dispatches(delivery_id),
          kind varchar(30) NOT NULL,
          object_key varchar(500) NOT NULL UNIQUE,
          content_type varchar(100) NOT NULL,
          size_bytes integer NOT NULL,
          sha256 varchar(64) NOT NULL,
          captured_by varchar(200) NOT NULL REFERENCES users(subject),
          device_captured_at timestamptz NOT NULL,
          status varchar(30) NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          verified_at timestamptz,
          CONSTRAINT ck_delivery_evidence_kind CHECK (kind IN ('signature','photo')),
          CONSTRAINT ck_delivery_evidence_content CHECK (
            content_type IN ('image/png','image/jpeg','image/webp')
          ),
          CONSTRAINT ck_delivery_evidence_size CHECK (size_bytes > 0 AND size_bytes <= 10485760),
          CONSTRAINT ck_delivery_evidence_sha CHECK (sha256 ~ '^[0-9a-f]{64}$'),
          CONSTRAINT ck_delivery_evidence_status CHECK (
            status IN ('uploading','verified','rejected')
          ),
          CONSTRAINT uq_delivery_evidence_delivery UNIQUE (delivery_id,evidence_id)
        );

        CREATE TABLE delivery_confirmations (
          confirmation_id uuid PRIMARY KEY,
          delivery_id uuid NOT NULL UNIQUE REFERENCES delivery_dispatches(delivery_id),
          recipient_name varchar(300) NOT NULL,
          device_captured_at timestamptz NOT NULL,
          notes varchar(2000),
          confirmed_by varchar(200) NOT NULL REFERENCES users(subject),
          delivery_version integer NOT NULL,
          correlation_id varchar(100) NOT NULL,
          idempotency_key varchar(200) NOT NULL,
          confirmed_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_delivery_confirmation_version CHECK (delivery_version > 1),
          CONSTRAINT uq_delivery_confirmation_actor_key UNIQUE (confirmed_by,idempotency_key)
        );

        CREATE TABLE delivery_confirmation_lines (
          confirmation_line_id uuid PRIMARY KEY,
          confirmation_id uuid NOT NULL REFERENCES delivery_confirmations(confirmation_id),
          delivery_line_id uuid NOT NULL UNIQUE REFERENCES delivery_lines(delivery_line_id),
          line_id uuid NOT NULL,
          sku_id uuid NOT NULL REFERENCES skus(sku_id),
          accepted_quantity_base numeric(18,6) NOT NULL,
          unit_cost numeric(18,6) NOT NULL,
          value_delta numeric(24,6) NOT NULL,
          outbound_movement_id uuid NOT NULL UNIQUE REFERENCES stock_movements(movement_id),
          CONSTRAINT ck_delivery_confirmation_line_quantity CHECK (accepted_quantity_base > 0),
          CONSTRAINT ck_delivery_confirmation_line_value CHECK (
            unit_cost >= 0 AND value_delta <= 0
          ),
          CONSTRAINT uq_delivery_confirmation_delivery_line
            UNIQUE (confirmation_id,delivery_line_id)
        );

        CREATE TABLE delivery_confirmation_evidence (
          confirmation_id uuid NOT NULL REFERENCES delivery_confirmations(confirmation_id),
          evidence_id uuid NOT NULL REFERENCES delivery_evidence(evidence_id),
          PRIMARY KEY (confirmation_id,evidence_id)
        );

        CREATE TABLE document_series (
          document_series_id uuid PRIMARY KEY,
          branch_id uuid NOT NULL REFERENCES branches(branch_id),
          document_type varchar(40) NOT NULL,
          prefix varchar(30) NOT NULL,
          next_number integer NOT NULL DEFAULT 1,
          CONSTRAINT ck_document_series_type CHECK (document_type = 'delivery_receipt'),
          CONSTRAINT ck_document_series_next CHECK (next_number > 0),
          CONSTRAINT uq_document_series_branch_type UNIQUE (branch_id,document_type)
        );

        CREATE TABLE delivery_receipts (
          delivery_receipt_id uuid PRIMARY KEY,
          confirmation_id uuid NOT NULL UNIQUE REFERENCES delivery_confirmations(confirmation_id),
          document_series_id uuid NOT NULL REFERENCES document_series(document_series_id),
          branch_id uuid NOT NULL REFERENCES branches(branch_id),
          series_number integer NOT NULL,
          number varchar(80) NOT NULL UNIQUE,
          snapshot jsonb NOT NULL,
          document_status varchar(30) NOT NULL DEFAULT 'pending_document',
          document_object_key varchar(500),
          issued_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_delivery_receipt_number CHECK (series_number > 0),
          CONSTRAINT ck_delivery_receipt_document_status CHECK (
            document_status IN ('pending_document','ready','unavailable')
          ),
          CONSTRAINT uq_delivery_receipt_series_number
            UNIQUE (document_series_id,series_number)
        );

        CREATE TABLE outbox_events (
          outbox_event_id uuid PRIMARY KEY,
          aggregate_type varchar(50) NOT NULL,
          aggregate_id uuid NOT NULL,
          event_type varchar(100) NOT NULL,
          payload jsonb NOT NULL,
          correlation_id varchar(100) NOT NULL,
          occurred_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_outbox_aggregate_event UNIQUE (aggregate_type,aggregate_id,event_type)
        );

        CREATE TABLE outbox_processing_state (
          outbox_event_id uuid PRIMARY KEY REFERENCES outbox_events(outbox_event_id),
          status varchar(30) NOT NULL DEFAULT 'pending',
          attempts integer NOT NULL DEFAULT 0,
          available_at timestamptz NOT NULL DEFAULT now(),
          last_error varchar(2000),
          processed_at timestamptz,
          CONSTRAINT ck_outbox_processing_status CHECK (
            status IN ('pending','processing','completed','failed')
          ),
          CONSTRAINT ck_outbox_processing_attempts CHECK (attempts >= 0)
        );

        CREATE TABLE outbox_handler_receipts (
          outbox_handler_receipt_id uuid PRIMARY KEY,
          outbox_event_id uuid NOT NULL REFERENCES outbox_events(outbox_event_id),
          handler_name varchar(100) NOT NULL,
          result_id uuid NOT NULL,
          processed_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_outbox_handler_receipt UNIQUE (outbox_event_id,handler_name)
        );

        CREATE TABLE draft_invoices (
          draft_invoice_id uuid PRIMARY KEY,
          delivery_confirmation_id uuid NOT NULL UNIQUE
            REFERENCES delivery_confirmations(confirmation_id),
          source_event_id uuid NOT NULL UNIQUE REFERENCES outbox_events(outbox_event_id),
          status varchar(20) NOT NULL,
          sales_order_id uuid NOT NULL,
          sales_order_revision_id uuid NOT NULL,
          customer_id uuid NOT NULL REFERENCES customer_accounts(customer_id),
          branch_id uuid NOT NULL REFERENCES branches(branch_id),
          currency varchar(3) NOT NULL,
          subtotal numeric(24,6) NOT NULL,
          discount_total numeric(24,6) NOT NULL,
          tax_total numeric(24,6) NOT NULL,
          grand_total numeric(24,6) NOT NULL,
          source_snapshot jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_draft_invoice_status CHECK (status = 'draft'),
          CONSTRAINT ck_draft_invoice_totals CHECK (
            subtotal >= 0 AND discount_total >= 0
            AND tax_total >= 0 AND grand_total >= 0
          )
        );

        CREATE TABLE draft_invoice_lines (
          draft_invoice_line_id uuid PRIMARY KEY,
          draft_invoice_id uuid NOT NULL REFERENCES draft_invoices(draft_invoice_id),
          line_id uuid NOT NULL,
          sku_id uuid NOT NULL REFERENCES skus(sku_id),
          accepted_quantity_base numeric(18,6) NOT NULL,
          unit_price numeric(18,6) NOT NULL,
          subtotal numeric(24,6) NOT NULL,
          discount_amount numeric(24,6) NOT NULL,
          tax_amount numeric(24,6) NOT NULL,
          line_total numeric(24,6) NOT NULL,
          calculation_snapshot jsonb NOT NULL,
          CONSTRAINT ck_draft_invoice_line_values CHECK (
            accepted_quantity_base > 0 AND unit_price >= 0
            AND subtotal >= 0 AND discount_amount >= 0
            AND tax_amount >= 0 AND line_total >= 0
          ),
          CONSTRAINT uq_draft_invoice_line UNIQUE (draft_invoice_id,line_id)
        );
        """
    for statement in statements.split(";\n"):
        if statement.strip():
            op.execute(statement)
    op.execute(
        """
        CREATE FUNCTION prevent_confirmation_ledger_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'Delivery Confirmation ledgers are immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table_name in (
        "delivery_confirmations",
        "delivery_confirmation_lines",
        "delivery_confirmation_evidence",
        "draft_invoices",
        "draft_invoice_lines",
        "outbox_handler_receipts",
        "outbox_events",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_immutable
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION prevent_confirmation_ledger_mutation()
            """
        )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM delivery_confirmations) THEN
            RAISE EXCEPTION 'Cannot downgrade 0012 while Delivery Confirmation data exists';
          END IF;
        END $$
        """
    )
    for table_name in (
        "outbox_handler_receipts",
        "draft_invoice_lines",
        "draft_invoices",
        "outbox_events",
        "delivery_confirmation_evidence",
        "delivery_confirmation_lines",
        "delivery_confirmations",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_immutable ON {table_name}")
    op.execute("DROP FUNCTION IF EXISTS prevent_confirmation_ledger_mutation")
    op.execute("DROP TABLE draft_invoice_lines")
    op.execute("DROP TABLE draft_invoices")
    op.execute("DROP TABLE outbox_handler_receipts")
    op.execute("DROP TABLE outbox_processing_state")
    op.execute("DROP TABLE outbox_events")
    op.execute("DROP TABLE delivery_receipts")
    op.execute("DROP TABLE document_series")
    op.execute("DROP TABLE delivery_confirmation_evidence")
    op.execute("DROP TABLE delivery_confirmation_lines")
    op.execute("DROP TABLE delivery_confirmations")
    op.execute("DROP TABLE delivery_evidence")
    op.execute("ALTER TABLE delivery_state DROP CONSTRAINT ck_delivery_state_status")
    op.execute(
        "ALTER TABLE delivery_state ADD CONSTRAINT ck_delivery_state_status "
        "CHECK (status IN ('dispatched'))"
    )
    op.execute(
        "ALTER TABLE fulfillment_order_state DROP CONSTRAINT ck_fulfillment_order_state_amounts"
    )
    op.execute(
        "ALTER TABLE fulfillment_order_state DROP CONSTRAINT ck_fulfillment_order_state_status"
    )
    op.execute("ALTER TABLE fulfillment_order_state DROP COLUMN delivered_quantity_base")
    op.execute(
        "ALTER TABLE fulfillment_order_state ADD CONSTRAINT ck_fulfillment_order_state_status "
        "CHECK (status IN ('reserved','payment_ready','pick_released','partially_picked',"
        "'picked','partially_dispatched','dispatched','payment_hold','cancelled'))"
    )
    op.execute(
        "ALTER TABLE fulfillment_order_state ADD CONSTRAINT ck_fulfillment_order_state_amounts "
        "CHECK (reserved_quantity_base >= 0 AND backorder_quantity_base >= 0 "
        "AND covered_amount >= 0 AND picked_quantity_base >= 0 "
        "AND dispatched_quantity_base >= 0 "
        "AND dispatched_quantity_base <= picked_quantity_base)"
    )
    op.execute("ALTER TABLE stock_movements DROP CONSTRAINT ck_stock_movements_leg")
    op.execute("ALTER TABLE stock_movements DROP CONSTRAINT ck_stock_movements_type")
    op.execute(
        "ALTER TABLE stock_movements ADD CONSTRAINT ck_stock_movements_type CHECK ("
        "movement_type IN ('opening_stock','pick','pick_reversal','dispatch'))"
    )
    op.execute(
        "ALTER TABLE stock_movements ADD CONSTRAINT ck_stock_movements_leg CHECK ("
        "(movement_type = 'opening_stock' AND movement_leg = 'opening_in') "
        "OR (movement_type = 'pick' AND movement_leg IN ('pick_available_out','pick_staging_in')) "
        "OR (movement_type = 'pick_reversal' AND movement_leg IN "
        "('pick_reversal_staging_out','pick_reversal_available_in')) "
        "OR (movement_type = 'dispatch' AND movement_leg IN "
        "('dispatch_staging_out','dispatch_transit_in')))"
    )
