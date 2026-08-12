"""Create prepaid payment-clearance and fulfillment bridge records.

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    schema_sql = """
        CREATE TABLE branch_payment_deadline_policies (
          policy_id uuid PRIMARY KEY,
          branch_id uuid NOT NULL REFERENCES branches(branch_id),
          version integer NOT NULL,
          deadline_minutes integer NOT NULL,
          effective_from timestamptz NOT NULL DEFAULT now(),
          is_active boolean NOT NULL DEFAULT true,
          created_by varchar(200) NOT NULL REFERENCES users(subject),
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_branch_payment_deadline_policy_version CHECK (version > 0),
          CONSTRAINT ck_branch_payment_deadline_policy_duration CHECK (deadline_minutes > 0),
          CONSTRAINT uq_branch_payment_deadline_policy_version UNIQUE (branch_id, version)
        );
        CREATE TABLE payment_methods (
          payment_method_id uuid PRIMARY KEY,
          company_id uuid NOT NULL REFERENCES companies(company_id),
          code varchar(50) NOT NULL,
          name varchar(100) NOT NULL,
          kind varchar(30) NOT NULL,
          requires_external_reference boolean NOT NULL,
          requires_evidence boolean NOT NULL,
          provider_confirmation_enabled boolean NOT NULL DEFAULT false,
          provider_code varchar(100),
          is_active boolean NOT NULL DEFAULT true,
          version integer NOT NULL DEFAULT 1,
          CONSTRAINT ck_payment_methods_kind
            CHECK (kind IN ('cash','bank_transfer','check','electronic')),
          CONSTRAINT ck_payment_methods_version CHECK (version > 0),
          CONSTRAINT uq_payment_method_company_code UNIQUE (company_id, code)
        );
        CREATE TABLE fulfillment_orders (
          fulfillment_order_id uuid PRIMARY KEY,
          sales_order_id uuid NOT NULL,
          sales_order_revision_id uuid NOT NULL,
          commercial_approval_id uuid NOT NULL,
          customer_id uuid NOT NULL REFERENCES customer_accounts(customer_id),
          branch_id uuid NOT NULL REFERENCES branches(branch_id),
          warehouse_id uuid NOT NULL,
          reservation_generation integer NOT NULL,
          payment_timing_policy varchar(30) NOT NULL,
          currency varchar(3) NOT NULL,
          order_value numeric(24,6) NOT NULL,
          payment_required numeric(24,6) NOT NULL,
          payment_deadline_at timestamptz,
          payment_deadline_policy_id uuid REFERENCES branch_payment_deadline_policies(policy_id),
          payment_deadline_minutes integer,
          created_by varchar(200) NOT NULL REFERENCES users(subject),
          correlation_id varchar(100) NOT NULL,
          idempotency_key varchar(200) NOT NULL UNIQUE,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_fulfillment_orders_generation CHECK (reservation_generation > 0),
          CONSTRAINT ck_fulfillment_orders_payment_timing
            CHECK (payment_timing_policy IN ('prepaid','cash_on_delivery','on_account')),
          CONSTRAINT ck_fulfillment_orders_values
            CHECK (order_value >= 0 AND payment_required >= 0 AND payment_required <= order_value),
          CONSTRAINT ck_fulfillment_orders_deadline CHECK (
            (payment_timing_policy = 'prepaid' AND payment_deadline_at IS NOT NULL
             AND payment_deadline_policy_id IS NOT NULL AND payment_deadline_minutes > 0)
            OR (payment_timing_policy <> 'prepaid' AND payment_deadline_at IS NULL)
          ),
          CONSTRAINT fk_fulfillment_orders_approval_ownership FOREIGN KEY
            (commercial_approval_id,sales_order_id,sales_order_revision_id,warehouse_id)
            REFERENCES commercial_approvals
            (commercial_approval_id,sales_order_id,sales_order_revision_id,warehouse_id),
          CONSTRAINT uq_fulfillment_order_generation
            UNIQUE (sales_order_id,reservation_generation),
          CONSTRAINT uq_fulfillment_order_ownership UNIQUE
            (fulfillment_order_id,sales_order_id,sales_order_revision_id,
             commercial_approval_id,warehouse_id)
        );
        CREATE TABLE fulfillment_order_lines (
          fulfillment_order_id uuid NOT NULL REFERENCES fulfillment_orders(fulfillment_order_id),
          line_id uuid NOT NULL,
          sales_order_id uuid NOT NULL,
          sales_order_revision_id uuid NOT NULL,
          commercial_approval_id uuid NOT NULL,
          sku_id uuid NOT NULL,
          warehouse_id uuid NOT NULL,
          ordered_quantity_base numeric(18,6) NOT NULL,
          reserved_quantity_base numeric(18,6) NOT NULL,
          backorder_quantity_base numeric(18,6) NOT NULL,
          approved_line_total numeric(24,6) NOT NULL,
          reserved_value numeric(24,6) NOT NULL,
          calculation_snapshot jsonb NOT NULL,
          PRIMARY KEY (fulfillment_order_id,line_id),
          CONSTRAINT ck_fulfillment_order_lines_quantities CHECK (
            ordered_quantity_base > 0 AND reserved_quantity_base >= 0
            AND backorder_quantity_base >= 0
            AND reserved_quantity_base + backorder_quantity_base = ordered_quantity_base),
          CONSTRAINT ck_fulfillment_order_lines_values CHECK (
            approved_line_total >= 0 AND reserved_value >= 0
            AND reserved_value <= approved_line_total),
          CONSTRAINT fk_fulfillment_order_lines_order_ownership FOREIGN KEY
            (fulfillment_order_id,sales_order_id,sales_order_revision_id,
             commercial_approval_id,warehouse_id)
            REFERENCES fulfillment_orders
            (fulfillment_order_id,sales_order_id,sales_order_revision_id,
             commercial_approval_id,warehouse_id),
          CONSTRAINT fk_fulfillment_order_lines_line_ownership FOREIGN KEY
            (sales_order_revision_id,line_id,sku_id)
            REFERENCES sales_order_line_revisions(sales_order_revision_id,line_id,sku_id)
        );
        CREATE TABLE fulfillment_order_state (
          fulfillment_order_id uuid PRIMARY KEY REFERENCES fulfillment_orders(fulfillment_order_id),
          status varchar(30) NOT NULL,
          reserved_quantity_base numeric(18,6) NOT NULL,
          backorder_quantity_base numeric(18,6) NOT NULL,
          covered_amount numeric(24,6) NOT NULL DEFAULT 0,
          payment_hold boolean NOT NULL DEFAULT false,
          version integer NOT NULL DEFAULT 1,
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_fulfillment_order_state_status CHECK
            (status IN ('reserved','payment_ready','pick_released','payment_hold','cancelled')),
          CONSTRAINT ck_fulfillment_order_state_amounts CHECK
            (reserved_quantity_base >= 0 AND backorder_quantity_base >= 0 AND covered_amount >= 0),
          CONSTRAINT ck_fulfillment_order_state_version CHECK (version > 0)
        );
        CREATE TABLE payment_receipts (
          payment_receipt_id uuid PRIMARY KEY,
          company_id uuid NOT NULL REFERENCES companies(company_id),
          branch_id uuid NOT NULL REFERENCES branches(branch_id),
          customer_id uuid NOT NULL REFERENCES customer_accounts(customer_id),
          payment_method_id uuid NOT NULL REFERENCES payment_methods(payment_method_id),
          payment_method_code varchar(50) NOT NULL,
          payment_method_kind varchar(30) NOT NULL,
          amount numeric(24,6) NOT NULL,
          currency varchar(3) NOT NULL,
          received_at timestamptz NOT NULL,
          external_reference varchar(200),
          external_reference_normalized varchar(200),
          evidence jsonb,
          intended_sales_order_id uuid REFERENCES sales_orders(sales_order_id),
          intended_fulfillment_order_id uuid REFERENCES fulfillment_orders(fulfillment_order_id),
          recorded_by varchar(200) NOT NULL REFERENCES users(subject),
          correlation_id varchar(100) NOT NULL,
          idempotency_key varchar(200) NOT NULL UNIQUE,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_payment_receipts_amount CHECK (amount > 0),
          CONSTRAINT ck_payment_receipts_kind CHECK
            (payment_method_kind IN ('cash','bank_transfer','check','electronic'))
        );
        CREATE TABLE payment_receipt_events (
          payment_receipt_event_id uuid PRIMARY KEY,
          payment_receipt_id uuid NOT NULL REFERENCES payment_receipts(payment_receipt_id),
          event_type varchar(30) NOT NULL,
          actor_subject varchar(200) NOT NULL REFERENCES users(subject),
          reason varchar(500),
          evidence jsonb,
          source_id uuid NOT NULL,
          correlation_id varchar(100) NOT NULL,
          idempotency_key varchar(200) NOT NULL,
          occurred_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_payment_receipt_events_type CHECK
            (event_type IN ('recorded','verified','bank_cleared','provider_confirmed',
                            'cleared','rejected','reversed','refunded')),
          CONSTRAINT uq_payment_receipt_event_command_type
            UNIQUE (payment_receipt_id,idempotency_key,event_type)
        );
        CREATE TABLE payment_receipt_status (
          payment_receipt_id uuid PRIMARY KEY REFERENCES payment_receipts(payment_receipt_id),
          company_id uuid NOT NULL,
          payment_method_id uuid NOT NULL,
          external_reference_normalized varchar(200),
          state varchar(30) NOT NULL,
          verified_by varchar(200) REFERENCES users(subject),
          cleared_at timestamptz,
          reversal_id uuid,
          version integer NOT NULL DEFAULT 1,
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_payment_receipt_status_state CHECK
            (state IN ('pending_verification','pending_clearance','cleared','rejected','reversed')),
          CONSTRAINT ck_payment_receipt_status_version CHECK (version > 0)
        );
        CREATE UNIQUE INDEX uq_payment_receipt_active_external_reference
          ON payment_receipt_status
          (company_id,payment_method_id,external_reference_normalized)
          WHERE external_reference_normalized IS NOT NULL
            AND state IN ('pending_verification','pending_clearance','cleared');
        CREATE TABLE payment_receipt_balances (
          payment_receipt_id uuid PRIMARY KEY REFERENCES payment_receipts(payment_receipt_id),
          cleared_amount numeric(24,6) NOT NULL DEFAULT 0,
          reversed_amount numeric(24,6) NOT NULL DEFAULT 0,
          refunded_amount numeric(24,6) NOT NULL DEFAULT 0,
          allocated_amount numeric(24,6) NOT NULL DEFAULT 0,
          coverage_designated_amount numeric(24,6) NOT NULL DEFAULT 0,
          version integer NOT NULL DEFAULT 1,
          CONSTRAINT ck_payment_receipt_balances_nonnegative CHECK (
            cleared_amount >= 0 AND reversed_amount >= 0 AND refunded_amount >= 0
            AND allocated_amount >= 0 AND coverage_designated_amount >= 0
            AND reversed_amount + refunded_amount + allocated_amount <= cleared_amount
            AND coverage_designated_amount <=
              cleared_amount - reversed_amount - refunded_amount - allocated_amount),
          CONSTRAINT ck_payment_receipt_balances_version CHECK (version > 0)
        );
        CREATE TABLE cash_reconciliation_items (
          payment_receipt_id uuid PRIMARY KEY REFERENCES payment_receipts(payment_receipt_id),
          status varchar(20) NOT NULL DEFAULT 'pending',
          expected_amount numeric(24,6) NOT NULL,
          counted_amount numeric(24,6),
          variance_amount numeric(24,6),
          cash_reconciliation_id uuid UNIQUE,
          reconciled_by varchar(200) REFERENCES users(subject),
          reconciled_at timestamptz,
          reason varchar(500),
          CONSTRAINT ck_cash_reconciliation_items_status
            CHECK (status IN ('pending','reconciled'))
        );
        CREATE TABLE prepayment_coverage_events (
          coverage_event_id uuid PRIMARY KEY,
          fulfillment_order_id uuid NOT NULL REFERENCES fulfillment_orders(fulfillment_order_id),
          payment_receipt_id uuid NOT NULL REFERENCES payment_receipts(payment_receipt_id),
          event_type varchar(20) NOT NULL,
          amount numeric(24,6) NOT NULL,
          reason varchar(500) NOT NULL,
          actor_subject varchar(200) NOT NULL REFERENCES users(subject),
          source_id uuid NOT NULL,
          correlation_id varchar(100) NOT NULL,
          idempotency_key varchar(200) NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_prepayment_coverage_events_type
            CHECK (event_type IN ('designated','released','consumed')),
          CONSTRAINT ck_prepayment_coverage_events_amount CHECK (amount > 0),
          CONSTRAINT uq_prepayment_coverage_event_command UNIQUE
            (idempotency_key,payment_receipt_id,fulfillment_order_id,event_type)
        );
        CREATE TABLE sales_order_hold_events (
          hold_event_id uuid PRIMARY KEY,
          sales_order_id uuid NOT NULL REFERENCES sales_orders(sales_order_id),
          fulfillment_order_id uuid NOT NULL REFERENCES fulfillment_orders(fulfillment_order_id),
          hold_type varchar(30) NOT NULL,
          event_type varchar(20) NOT NULL,
          reason varchar(500) NOT NULL,
          actor_subject varchar(200) NOT NULL REFERENCES users(subject),
          correlation_id varchar(100) NOT NULL,
          idempotency_key varchar(200) NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_sales_order_hold_events_type CHECK (hold_type IN ('payment')),
          CONSTRAINT ck_sales_order_hold_events_event CHECK (event_type IN ('applied','released')),
          CONSTRAINT uq_sales_order_hold_event_command UNIQUE
            (idempotency_key,sales_order_id,hold_type,event_type)
        );
        CREATE TABLE active_sales_order_holds (
          sales_order_id uuid NOT NULL REFERENCES sales_orders(sales_order_id),
          hold_type varchar(30) NOT NULL,
          fulfillment_order_id uuid NOT NULL REFERENCES fulfillment_orders(fulfillment_order_id),
          hold_event_id uuid NOT NULL REFERENCES sales_order_hold_events(hold_event_id),
          applied_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (sales_order_id,hold_type)
        );
        CREATE TABLE pick_releases (
          pick_release_id uuid PRIMARY KEY,
          fulfillment_order_id uuid NOT NULL UNIQUE
            REFERENCES fulfillment_orders(fulfillment_order_id),
          quantity_base numeric(18,6) NOT NULL,
          payment_required numeric(24,6) NOT NULL,
          cleared_payment numeric(24,6) NOT NULL,
          reason varchar(500) NOT NULL,
          released_by varchar(200) NOT NULL REFERENCES users(subject),
          correlation_id varchar(100) NOT NULL,
          idempotency_key varchar(200) NOT NULL UNIQUE,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_pick_releases_quantity CHECK (quantity_base > 0),
          CONSTRAINT ck_pick_releases_payment
            CHECK (payment_required >= 0 AND cleared_payment >= payment_required)
        );
        CREATE TABLE payment_refunds (
          payment_refund_id uuid PRIMARY KEY,
          payment_receipt_id uuid NOT NULL REFERENCES payment_receipts(payment_receipt_id),
          amount numeric(24,6) NOT NULL,
          reason varchar(500) NOT NULL,
          requested_by varchar(200) NOT NULL REFERENCES users(subject),
          approved_by varchar(200) NOT NULL REFERENCES users(subject),
          correlation_id varchar(100) NOT NULL,
          idempotency_key varchar(200) NOT NULL UNIQUE,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_payment_refunds_amount CHECK (amount > 0),
          CONSTRAINT ck_payment_refunds_maker_checker CHECK (requested_by <> approved_by)
        );
        """
    for statement in schema_sql.split(";\n"):
        if statement.strip():
            op.execute(statement)
    _create_immutable_guards()


def _create_immutable_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION prevent_payment_fulfillment_ledger_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'Payment and fulfillment ledgers are immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table_name in (
        "fulfillment_orders",
        "fulfillment_order_lines",
        "payment_receipts",
        "payment_receipt_events",
        "prepayment_coverage_events",
        "sales_order_hold_events",
        "pick_releases",
        "payment_refunds",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_immutable
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION prevent_payment_fulfillment_ledger_mutation()
            """
        )


def downgrade() -> None:
    for table_name in (
        "payment_refunds",
        "pick_releases",
        "sales_order_hold_events",
        "prepayment_coverage_events",
        "payment_receipt_events",
        "payment_receipts",
        "fulfillment_order_lines",
        "fulfillment_orders",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_immutable ON {table_name}")
    op.execute("DROP FUNCTION IF EXISTS prevent_payment_fulfillment_ledger_mutation")
    for table_name in (
        "payment_refunds",
        "pick_releases",
        "active_sales_order_holds",
        "sales_order_hold_events",
        "prepayment_coverage_events",
        "cash_reconciliation_items",
        "payment_receipt_balances",
        "payment_receipt_status",
        "payment_receipt_events",
        "payment_receipts",
        "fulfillment_order_state",
        "fulfillment_order_lines",
        "fulfillment_orders",
        "payment_methods",
        "branch_payment_deadline_policies",
    ):
        op.drop_table(table_name)
