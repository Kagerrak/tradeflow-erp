"""Preserve custody through Delivery Exceptions and retry Deliveries.

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    statements = """
        ALTER TABLE warehouse_stock_locations
          DROP CONSTRAINT ck_warehouse_stock_locations_custody;
        ALTER TABLE warehouse_stock_locations
          ADD CONSTRAINT ck_warehouse_stock_locations_custody CHECK (
            custody IN ('available','quarantine','dispatch_staging','in_transit','investigation')
          );
        CREATE UNIQUE INDEX uq_warehouse_active_investigation
          ON warehouse_stock_locations (warehouse_id)
          WHERE custody = 'investigation' AND is_active;

        ALTER TABLE stock_movements DROP CONSTRAINT ck_stock_movements_type;
        ALTER TABLE stock_movements DROP CONSTRAINT ck_stock_movements_leg;
        ALTER TABLE stock_movements ADD CONSTRAINT ck_stock_movements_type CHECK (
          movement_type IN ('opening_stock','pick','pick_reversal','dispatch',
            'delivery_confirmation','delivery_exception','return_to_warehouse',
            'investigation_resolution')
        );
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
        );

        ALTER TABLE delivery_dispatches
          ADD COLUMN dispatch_kind varchar(20) NOT NULL DEFAULT 'initial',
          ADD COLUMN parent_delivery_id uuid REFERENCES delivery_dispatches(delivery_id),
          ADD CONSTRAINT ck_delivery_dispatch_kind CHECK (
            (dispatch_kind = 'initial' AND parent_delivery_id IS NULL)
            OR (dispatch_kind = 'retry' AND parent_delivery_id IS NOT NULL)
          );

        ALTER TABLE cod_on_account_conversions
          ADD COLUMN consumed_amount numeric(24,6) NOT NULL DEFAULT 0,
          ADD CONSTRAINT ck_cod_conversion_consumed_amount CHECK (
            consumed_amount >= 0 AND consumed_amount <= amount
          );
        UPDATE cod_on_account_conversions
          SET consumed_amount = amount
          WHERE status = 'consumed';

        ALTER TABLE delivery_lines DROP CONSTRAINT delivery_lines_pick_line_id_key;
        ALTER TABLE delivery_lines
          ADD COLUMN source_exception_case_id uuid;
        ALTER TABLE delivery_lines
          DROP CONSTRAINT delivery_lines_staging_movement_id_key,
          DROP CONSTRAINT delivery_lines_transit_movement_id_key,
          ALTER COLUMN staging_movement_id DROP NOT NULL,
          ALTER COLUMN transit_movement_id DROP NOT NULL;
        CREATE UNIQUE INDEX uq_initial_delivery_line_pick
          ON delivery_lines (pick_line_id) WHERE source_exception_case_id IS NULL;
        CREATE UNIQUE INDEX uq_initial_delivery_line_staging_movement
          ON delivery_lines (staging_movement_id) WHERE staging_movement_id IS NOT NULL;
        CREATE UNIQUE INDEX uq_initial_delivery_line_transit_movement
          ON delivery_lines (transit_movement_id) WHERE transit_movement_id IS NOT NULL;

        ALTER TABLE delivery_confirmation_lines
          DROP CONSTRAINT ck_delivery_confirmation_line_quantity,
          DROP CONSTRAINT ck_delivery_confirmation_line_value;
        ALTER TABLE delivery_confirmation_lines
          ADD COLUMN refused_quantity_base numeric(18,6) NOT NULL DEFAULT 0,
          ADD COLUMN damaged_quantity_base numeric(18,6) NOT NULL DEFAULT 0,
          ADD COLUMN short_missing_quantity_base numeric(18,6) NOT NULL DEFAULT 0,
          ADD COLUMN still_undelivered_quantity_base numeric(18,6) NOT NULL DEFAULT 0;
        ALTER TABLE delivery_confirmation_lines
          ALTER COLUMN outbound_movement_id DROP NOT NULL;
        ALTER TABLE delivery_confirmation_lines
          ADD CONSTRAINT ck_delivery_confirmation_line_partition CHECK (
            accepted_quantity_base >= 0 AND refused_quantity_base >= 0
            AND damaged_quantity_base >= 0 AND short_missing_quantity_base >= 0
            AND still_undelivered_quantity_base >= 0
            AND accepted_quantity_base + refused_quantity_base + damaged_quantity_base
              + short_missing_quantity_base + still_undelivered_quantity_base > 0
          ),
          ADD CONSTRAINT ck_delivery_confirmation_line_value CHECK (
            unit_cost >= 0 AND value_delta <= 0
            AND ((accepted_quantity_base = 0 AND outbound_movement_id IS NULL AND value_delta = 0)
              OR (accepted_quantity_base > 0 AND outbound_movement_id IS NOT NULL))
          );

        CREATE TABLE delivery_line_identity_allocations (
          allocation_id uuid PRIMARY KEY,
          delivery_line_id uuid NOT NULL REFERENCES delivery_lines(delivery_line_id),
          pick_identity_assignment_id uuid NOT NULL
            REFERENCES pick_identity_assignments(pick_identity_assignment_id),
          quantity_base numeric(18,6) NOT NULL CHECK (quantity_base > 0),
          CONSTRAINT uq_delivery_line_identity_allocation
            UNIQUE (delivery_line_id,pick_identity_assignment_id)
        );

        INSERT INTO delivery_line_identity_allocations
          (allocation_id,delivery_line_id,pick_identity_assignment_id,quantity_base)
        SELECT gen_random_uuid(), dl.delivery_line_id,
          pia.pick_identity_assignment_id, pia.quantity_base
        FROM delivery_lines dl
        JOIN pick_identity_assignments pia ON pia.pick_line_id = dl.pick_line_id;

        CREATE TABLE stock_movement_identity_allocations (
          allocation_id uuid PRIMARY KEY,
          movement_id uuid NOT NULL REFERENCES stock_movements(movement_id),
          delivery_line_identity_allocation_id uuid NOT NULL
            REFERENCES delivery_line_identity_allocations(allocation_id),
          quantity_base numeric(18,6) NOT NULL CHECK (quantity_base > 0),
          CONSTRAINT uq_stock_movement_identity_allocation
            UNIQUE (movement_id,delivery_line_identity_allocation_id)
        );

        INSERT INTO stock_movement_identity_allocations(
          allocation_id,movement_id,delivery_line_identity_allocation_id,quantity_base
        )
        SELECT gen_random_uuid(), confirmation_line.outbound_movement_id,
          identity_allocation.allocation_id, identity_allocation.quantity_base
        FROM delivery_confirmation_lines confirmation_line
        JOIN delivery_line_identity_allocations identity_allocation
          ON identity_allocation.delivery_line_id = confirmation_line.delivery_line_id
        WHERE confirmation_line.outbound_movement_id IS NOT NULL;

        CREATE TABLE delivery_confirmation_identity_partitions (
          partition_id uuid PRIMARY KEY,
          confirmation_line_id uuid NOT NULL
            REFERENCES delivery_confirmation_lines(confirmation_line_id),
          delivery_line_identity_allocation_id uuid NOT NULL
            REFERENCES delivery_line_identity_allocations(allocation_id),
          accepted_quantity_base numeric(18,6) NOT NULL DEFAULT 0,
          refused_quantity_base numeric(18,6) NOT NULL DEFAULT 0,
          damaged_quantity_base numeric(18,6) NOT NULL DEFAULT 0,
          short_missing_quantity_base numeric(18,6) NOT NULL DEFAULT 0,
          still_undelivered_quantity_base numeric(18,6) NOT NULL DEFAULT 0,
          CONSTRAINT ck_delivery_identity_partition_nonnegative CHECK (
            accepted_quantity_base >= 0 AND refused_quantity_base >= 0
            AND damaged_quantity_base >= 0 AND short_missing_quantity_base >= 0
            AND still_undelivered_quantity_base >= 0
          ),
          CONSTRAINT uq_delivery_confirmation_identity_partition
            UNIQUE (confirmation_line_id,delivery_line_identity_allocation_id)
        );

        INSERT INTO delivery_confirmation_identity_partitions(
          partition_id,confirmation_line_id,delivery_line_identity_allocation_id,
          accepted_quantity_base
        )
        SELECT gen_random_uuid(), confirmation_line.confirmation_line_id,
          identity_allocation.allocation_id, identity_allocation.quantity_base
        FROM delivery_confirmation_lines confirmation_line
        JOIN delivery_line_identity_allocations identity_allocation
          ON identity_allocation.delivery_line_id = confirmation_line.delivery_line_id;

        CREATE TABLE delivery_exception_cases (
          exception_case_id uuid PRIMARY KEY,
          confirmation_line_id uuid NOT NULL
            REFERENCES delivery_confirmation_lines(confirmation_line_id),
          exception_kind varchar(30) NOT NULL CHECK (
            exception_kind IN ('refused','damaged','short_missing','still_undelivered')
          ),
          original_quantity_base numeric(18,6) NOT NULL CHECK (original_quantity_base > 0),
          initial_custody varchar(30) NOT NULL CHECK (
            initial_custody IN ('in_transit','investigation')
          ),
          responsible_party_type varchar(20) NOT NULL CHECK (
            responsible_party_type IN ('staff','carrier','customer','unknown')
          ),
          responsible_subject varchar(200),
          responsible_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
          investigation_movement_group_id uuid,
          investigation_out_movement_id uuid REFERENCES stock_movements(movement_id),
          investigation_in_movement_id uuid REFERENCES stock_movements(movement_id),
          opened_by varchar(200) NOT NULL REFERENCES users(subject),
          correlation_id varchar(100) NOT NULL,
          opened_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_delivery_exception_case_kind
            UNIQUE (confirmation_line_id,exception_kind)
        );
        ALTER TABLE delivery_lines ADD CONSTRAINT fk_delivery_line_source_exception
          FOREIGN KEY (source_exception_case_id)
            REFERENCES delivery_exception_cases(exception_case_id);

        CREATE TABLE delivery_exception_case_evidence (
          exception_case_id uuid NOT NULL REFERENCES delivery_exception_cases(exception_case_id),
          evidence_id uuid NOT NULL REFERENCES delivery_evidence(evidence_id),
          PRIMARY KEY (exception_case_id,evidence_id)
        );

        CREATE TABLE delivery_exception_events (
          exception_event_id uuid PRIMARY KEY,
          exception_case_id uuid NOT NULL REFERENCES delivery_exception_cases(exception_case_id),
          event_type varchar(40) NOT NULL CHECK (event_type IN
            ('opened','return_received','retry_allocated','recovered',
             'carrier_claim_resolved','inventory_adjustment_resolved')),
          quantity_base numeric(18,6) NOT NULL CHECK (quantity_base > 0),
          source_document_type varchar(50) NOT NULL,
          source_document_id uuid NOT NULL,
          from_custody varchar(30),
          to_custody varchar(30),
          reason varchar(500),
          approved_by varchar(200) REFERENCES users(subject),
          approval_authority_id uuid REFERENCES approval_authorities(approval_authority_id),
          movement_group_id uuid,
          actor_subject varchar(200) NOT NULL REFERENCES users(subject),
          correlation_id varchar(100) NOT NULL,
          idempotency_key varchar(200) NOT NULL,
          occurred_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_delivery_exception_event_actor_key
            UNIQUE (actor_subject,idempotency_key,exception_case_id,event_type)
        );

        CREATE TABLE delivery_exception_state (
          exception_case_id uuid PRIMARY KEY REFERENCES delivery_exception_cases(exception_case_id),
          status varchar(30) NOT NULL CHECK (status IN ('open','partially_resolved','resolved')),
          custody varchar(30) NOT NULL CHECK (
            custody IN ('in_transit','investigation','quarantine','outbound')
          ),
          open_quantity_base numeric(18,6) NOT NULL CHECK (open_quantity_base >= 0),
          returned_quantity_base numeric(18,6) NOT NULL DEFAULT 0
            CHECK (returned_quantity_base >= 0),
          retry_allocated_quantity_base numeric(18,6) NOT NULL DEFAULT 0
            CHECK (retry_allocated_quantity_base >= 0),
          resolved_quantity_base numeric(18,6) NOT NULL DEFAULT 0
            CHECK (resolved_quantity_base >= 0),
          version integer NOT NULL DEFAULT 1 CHECK (version > 0),
          updated_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE delivery_exception_event_evidence (
          exception_event_id uuid NOT NULL
            REFERENCES delivery_exception_events(exception_event_id),
          evidence_id uuid NOT NULL REFERENCES delivery_evidence(evidence_id),
          PRIMARY KEY (exception_event_id,evidence_id)
        );

        CREATE TABLE return_to_warehouse_receipts (
          receipt_id uuid PRIMARY KEY,
          delivery_id uuid NOT NULL REFERENCES delivery_dispatches(delivery_id),
          warehouse_id uuid NOT NULL REFERENCES warehouses(warehouse_id),
          received_by varchar(200) NOT NULL REFERENCES users(subject),
          received_at timestamptz NOT NULL,
          notes varchar(2000),
          correlation_id varchar(100) NOT NULL,
          idempotency_key varchar(200) NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_return_receipt_actor_key UNIQUE (received_by,idempotency_key)
        );
        CREATE TABLE return_to_warehouse_receipt_lines (
          receipt_line_id uuid PRIMARY KEY,
          receipt_id uuid NOT NULL REFERENCES return_to_warehouse_receipts(receipt_id),
          exception_case_id uuid NOT NULL REFERENCES delivery_exception_cases(exception_case_id),
          quantity_base numeric(18,6) NOT NULL CHECK (quantity_base > 0),
          transit_out_movement_id uuid NOT NULL UNIQUE REFERENCES stock_movements(movement_id),
          quarantine_in_movement_id uuid NOT NULL UNIQUE REFERENCES stock_movements(movement_id),
          CONSTRAINT uq_return_receipt_case UNIQUE (receipt_id,exception_case_id)
        );

        CREATE TABLE investigation_resolutions (
          resolution_id uuid PRIMARY KEY,
          exception_case_id uuid NOT NULL REFERENCES delivery_exception_cases(exception_case_id),
          resolution_type varchar(30) NOT NULL CHECK (
            resolution_type IN ('recovery','carrier_claim','inventory_adjustment')
          ),
          quantity_base numeric(18,6) NOT NULL CHECK (quantity_base > 0),
          reason varchar(500) NOT NULL,
          external_reference varchar(200),
          approved_by varchar(200) NOT NULL REFERENCES users(subject),
          approval_authority_id uuid REFERENCES approval_authorities(approval_authority_id),
          movement_group_id uuid NOT NULL,
          investigation_out_movement_id uuid NOT NULL UNIQUE
            REFERENCES stock_movements(movement_id),
          quarantine_in_movement_id uuid UNIQUE REFERENCES stock_movements(movement_id),
          correlation_id varchar(100) NOT NULL,
          idempotency_key varchar(200) NOT NULL,
          resolved_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_investigation_resolution_actor_key
            UNIQUE (approved_by,idempotency_key)
        );

        CREATE TABLE delivery_retry_allocations (
          retry_allocation_id uuid PRIMARY KEY,
          source_exception_case_id uuid NOT NULL
            REFERENCES delivery_exception_cases(exception_case_id),
          retry_delivery_line_id uuid NOT NULL UNIQUE REFERENCES delivery_lines(delivery_line_id),
          quantity_base numeric(18,6) NOT NULL CHECK (quantity_base > 0),
          allocated_by varchar(200) NOT NULL REFERENCES users(subject),
          reason varchar(500) NOT NULL,
          correlation_id varchar(100) NOT NULL,
          idempotency_key varchar(200) NOT NULL,
          allocated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_delivery_retry_allocation_actor_key
            UNIQUE (allocated_by,idempotency_key,source_exception_case_id)
        );

        CREATE INDEX ix_delivery_exception_queue
          ON delivery_exception_state (status,custody,updated_at);
        """
    for statement in statements.split(";\n"):
        if statement.strip():
            op.execute(statement)
    op.execute("DROP TRIGGER cod_conversion_approval_immutable ON cod_on_account_conversions")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION protect_cod_conversion_approval() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'COD On Account approvals are immutable';
          END IF;
          IF TG_OP = 'INSERT' THEN
            IF NEW.status = 'approved' AND NEW.confirmation_id IS NULL
               AND NEW.consumed_amount = 0 THEN
              RETURN NEW;
            END IF;
            RAISE EXCEPTION 'A COD On Account approval must start unconsumed';
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
          IF OLD.status IN ('consumed','reversed') THEN
            IF NEW.status IS NOT DISTINCT FROM OLD.status
               AND NEW.confirmation_id IS NOT DISTINCT FROM OLD.confirmation_id
               AND NEW.consumed_amount IS NOT DISTINCT FROM OLD.consumed_amount THEN
              RETURN NEW;
            END IF;
            RAISE EXCEPTION 'Invalid COD On Account approval transition';
          END IF;
          IF OLD.status = 'approved' AND OLD.confirmation_id IS NULL
             AND OLD.consumed_amount = 0 THEN
            IF NEW.status = 'approved' AND NEW.confirmation_id IS NULL
               AND NEW.consumed_amount = 0 THEN
              RETURN NEW;
            END IF;
            IF NEW.status = 'consumed' AND NEW.confirmation_id IS NOT NULL
               AND NEW.consumed_amount > 0
               AND NEW.consumed_amount <= NEW.amount THEN
              RETURN NEW;
            END IF;
            IF NEW.status = 'reversed' AND NEW.confirmation_id IS NULL
               AND NEW.consumed_amount = 0 THEN
              RETURN NEW;
            END IF;
          END IF;
          RAISE EXCEPTION 'Invalid COD On Account approval transition';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER cod_conversion_approval_immutable
          BEFORE INSERT OR UPDATE OR DELETE ON cod_on_account_conversions
          FOR EACH ROW EXECUTE FUNCTION protect_cod_conversion_approval()
        """
    )
    op.execute(
        """
        CREATE FUNCTION validate_delivery_partition() RETURNS trigger AS $$
        DECLARE dispatched numeric(18,6);
        BEGIN
          SELECT quantity_base INTO dispatched FROM delivery_lines
            WHERE delivery_line_id = NEW.delivery_line_id;
          IF NEW.accepted_quantity_base + NEW.refused_quantity_base
             + NEW.damaged_quantity_base + NEW.short_missing_quantity_base
             + NEW.still_undelivered_quantity_base <> dispatched THEN
            RAISE EXCEPTION 'Delivery line partition must equal dispatched quantity';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """CREATE TRIGGER trg_delivery_confirmation_partition_exact
        BEFORE INSERT ON delivery_confirmation_lines
        FOR EACH ROW EXECUTE FUNCTION validate_delivery_partition()"""
    )
    op.execute(
        """
        CREATE FUNCTION validate_delivery_line_lineage() RETURNS trigger AS $$
        DECLARE kind varchar(20);
        BEGIN
          SELECT dispatch_kind INTO kind FROM delivery_dispatches
            WHERE delivery_id = NEW.delivery_id;
          IF (kind = 'initial' AND (
                NEW.source_exception_case_id IS NOT NULL
                OR NEW.staging_movement_id IS NULL OR NEW.transit_movement_id IS NULL))
             OR (kind = 'retry' AND (
                NEW.source_exception_case_id IS NULL
                OR NEW.staging_movement_id IS NOT NULL OR NEW.transit_movement_id IS NOT NULL)) THEN
            RAISE EXCEPTION 'Delivery Line lineage does not match Dispatch kind';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """CREATE TRIGGER trg_delivery_line_lineage
        BEFORE INSERT ON delivery_lines
        FOR EACH ROW EXECUTE FUNCTION validate_delivery_line_lineage()"""
    )
    op.execute(
        """
        CREATE FUNCTION validate_delivery_line_identity_integrity() RETURNS trigger AS $$
        DECLARE target_line_id uuid;
        DECLARE line_record record;
        DECLARE allocation_count integer;
        DECLARE allocation_quantity numeric(18,6);
        DECLARE invalid_count integer;
        DECLARE retry_count integer;
        BEGIN
          target_line_id := NEW.delivery_line_id;
          SELECT delivery_line.*, sku.tracking_policy, dispatch.dispatch_kind
            INTO line_record
          FROM delivery_lines delivery_line
          JOIN skus sku ON sku.sku_id = delivery_line.sku_id
          JOIN delivery_dispatches dispatch
            ON dispatch.delivery_id = delivery_line.delivery_id
          WHERE delivery_line.delivery_line_id = target_line_id;
          IF NOT FOUND THEN
            RETURN NEW;
          END IF;

          SELECT count(*), coalesce(sum(allocation.quantity_base),0),
            count(*) FILTER (WHERE assignment.pick_line_id <> line_record.pick_line_id
              OR assignment.tracking_policy <> line_record.tracking_policy)
            INTO allocation_count, allocation_quantity, invalid_count
          FROM delivery_line_identity_allocations allocation
          JOIN pick_identity_assignments assignment
            ON assignment.pick_identity_assignment_id =
              allocation.pick_identity_assignment_id
          WHERE allocation.delivery_line_id = target_line_id;

          IF (line_record.tracking_policy = 'untracked' AND allocation_count <> 0)
             OR (line_record.tracking_policy IN ('lot','serial') AND (
               allocation_count = 0 OR allocation_quantity <> line_record.quantity_base
               OR invalid_count <> 0)) THEN
            RAISE EXCEPTION
              'Delivery Line identity allocations do not exactly match tracked custody';
          END IF;

          IF line_record.dispatch_kind = 'retry' THEN
            SELECT count(*) INTO retry_count
            FROM delivery_retry_allocations retry
            WHERE retry.retry_delivery_line_id = target_line_id;
            IF retry_count <> 1 THEN
              RAISE EXCEPTION 'Retry Delivery Line requires exactly one retry allocation';
            END IF;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """CREATE CONSTRAINT TRIGGER trg_delivery_line_identity_exact
        AFTER INSERT ON delivery_lines DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION validate_delivery_line_identity_integrity()"""
    )
    op.execute(
        """CREATE CONSTRAINT TRIGGER trg_delivery_identity_allocation_exact
        AFTER INSERT ON delivery_line_identity_allocations DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION validate_delivery_line_identity_integrity()"""
    )
    op.execute(
        """
        CREATE FUNCTION validate_confirmation_identity_integrity() RETURNS trigger AS $$
        DECLARE target_confirmation_line_id uuid;
        DECLARE line_record record;
        DECLARE partition_count integer;
        DECLARE invalid_count integer;
        DECLARE accepted numeric(18,6);
        DECLARE refused numeric(18,6);
        DECLARE damaged numeric(18,6);
        DECLARE short_missing numeric(18,6);
        DECLARE still_undelivered numeric(18,6);
        BEGIN
          IF TG_TABLE_NAME = 'delivery_confirmation_lines' THEN
            target_confirmation_line_id := NEW.confirmation_line_id;
          ELSE
            target_confirmation_line_id := NEW.confirmation_line_id;
          END IF;
          SELECT confirmation_line.*, sku.tracking_policy
            INTO line_record
          FROM delivery_confirmation_lines confirmation_line
          JOIN delivery_lines delivery_line
            ON delivery_line.delivery_line_id = confirmation_line.delivery_line_id
          JOIN skus sku ON sku.sku_id = delivery_line.sku_id
          WHERE confirmation_line.confirmation_line_id = target_confirmation_line_id;
          IF NOT FOUND THEN
            RETURN NEW;
          END IF;

          SELECT count(*),
            count(*) FILTER (WHERE allocation.delivery_line_id <> line_record.delivery_line_id
              OR partition.accepted_quantity_base + partition.refused_quantity_base
                + partition.damaged_quantity_base + partition.short_missing_quantity_base
                + partition.still_undelivered_quantity_base <> allocation.quantity_base
              OR (assignment.tracking_policy = 'serial' AND
                (CASE WHEN partition.accepted_quantity_base > 0 THEN 1 ELSE 0 END
                 + CASE WHEN partition.refused_quantity_base > 0 THEN 1 ELSE 0 END
                 + CASE WHEN partition.damaged_quantity_base > 0 THEN 1 ELSE 0 END
                 + CASE WHEN partition.short_missing_quantity_base > 0 THEN 1 ELSE 0 END
                 + CASE WHEN partition.still_undelivered_quantity_base > 0
                    THEN 1 ELSE 0 END) <> 1)),
            coalesce(sum(partition.accepted_quantity_base),0),
            coalesce(sum(partition.refused_quantity_base),0),
            coalesce(sum(partition.damaged_quantity_base),0),
            coalesce(sum(partition.short_missing_quantity_base),0),
            coalesce(sum(partition.still_undelivered_quantity_base),0)
            INTO partition_count, invalid_count, accepted, refused, damaged,
              short_missing, still_undelivered
          FROM delivery_confirmation_identity_partitions partition
          JOIN delivery_line_identity_allocations allocation
            ON allocation.allocation_id =
              partition.delivery_line_identity_allocation_id
          JOIN pick_identity_assignments assignment
            ON assignment.pick_identity_assignment_id =
              allocation.pick_identity_assignment_id
          WHERE partition.confirmation_line_id = target_confirmation_line_id;

          IF (line_record.tracking_policy = 'untracked' AND partition_count <> 0)
             OR (line_record.tracking_policy IN ('lot','serial') AND (
               partition_count = 0 OR invalid_count <> 0
               OR accepted <> line_record.accepted_quantity_base
               OR refused <> line_record.refused_quantity_base
               OR damaged <> line_record.damaged_quantity_base
               OR short_missing <> line_record.short_missing_quantity_base
               OR still_undelivered <> line_record.still_undelivered_quantity_base)) THEN
            RAISE EXCEPTION
              'Confirmation identity partitions do not exactly match Delivery custody';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """CREATE CONSTRAINT TRIGGER trg_confirmation_line_identity_exact
        AFTER INSERT ON delivery_confirmation_lines DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION validate_confirmation_identity_integrity()"""
    )
    op.execute(
        """CREATE CONSTRAINT TRIGGER trg_confirmation_identity_partition_exact
        AFTER INSERT ON delivery_confirmation_identity_partitions
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION validate_confirmation_identity_integrity()"""
    )
    op.execute(
        """
        CREATE FUNCTION validate_stock_movement_identity_integrity() RETURNS trigger AS $$
        DECLARE target_movement_id uuid;
        DECLARE movement_record record;
        DECLARE allocation_count integer;
        DECLARE allocation_quantity numeric(18,6);
        DECLARE invalid_count integer;
        DECLARE source_delivery_line_id uuid;
        DECLARE source_confirmation_line_id uuid;
        DECLARE source_outcome_kind varchar(30);
        BEGIN
          target_movement_id := NEW.movement_id;
          SELECT movement.*, sku.tracking_policy INTO movement_record
          FROM stock_movements movement
          JOIN skus sku ON sku.sku_id = movement.sku_id
          WHERE movement.movement_id = target_movement_id;
          IF NOT FOUND OR movement_record.movement_type NOT IN (
            'delivery_confirmation','delivery_exception','return_to_warehouse',
            'investigation_resolution'
          ) THEN
            RETURN NEW;
          END IF;

          IF movement_record.movement_type = 'delivery_confirmation' THEN
            SELECT confirmation_line.delivery_line_id,
              confirmation_line.confirmation_line_id, 'accepted'
              INTO source_delivery_line_id, source_confirmation_line_id,
                source_outcome_kind
            FROM delivery_confirmation_lines confirmation_line
            WHERE confirmation_line.outbound_movement_id = target_movement_id;
          ELSIF movement_record.movement_type = 'delivery_exception' THEN
            SELECT confirmation_line.delivery_line_id,
              confirmation_line.confirmation_line_id, exception_case.exception_kind
              INTO source_delivery_line_id, source_confirmation_line_id,
                source_outcome_kind
            FROM delivery_exception_cases exception_case
            JOIN delivery_confirmation_lines confirmation_line
              ON confirmation_line.confirmation_line_id =
                exception_case.confirmation_line_id
            WHERE exception_case.investigation_out_movement_id = target_movement_id
               OR exception_case.investigation_in_movement_id = target_movement_id;
          ELSIF movement_record.movement_type = 'return_to_warehouse' THEN
            SELECT confirmation_line.delivery_line_id,
              confirmation_line.confirmation_line_id, exception_case.exception_kind
              INTO source_delivery_line_id, source_confirmation_line_id,
                source_outcome_kind
            FROM return_to_warehouse_receipt_lines receipt_line
            JOIN delivery_exception_cases exception_case
              ON exception_case.exception_case_id = receipt_line.exception_case_id
            JOIN delivery_confirmation_lines confirmation_line
              ON confirmation_line.confirmation_line_id =
                exception_case.confirmation_line_id
            WHERE receipt_line.transit_out_movement_id = target_movement_id
               OR receipt_line.quarantine_in_movement_id = target_movement_id;
          ELSE
            SELECT confirmation_line.delivery_line_id,
              confirmation_line.confirmation_line_id, exception_case.exception_kind
              INTO source_delivery_line_id, source_confirmation_line_id,
                source_outcome_kind
            FROM investigation_resolutions resolution
            JOIN delivery_exception_cases exception_case
              ON exception_case.exception_case_id = resolution.exception_case_id
            JOIN delivery_confirmation_lines confirmation_line
              ON confirmation_line.confirmation_line_id =
                exception_case.confirmation_line_id
            WHERE resolution.investigation_out_movement_id = target_movement_id
               OR resolution.quarantine_in_movement_id = target_movement_id;
          END IF;
          IF source_delivery_line_id IS NULL THEN
            RAISE EXCEPTION 'Stock Movement is not linked to its custody source';
          END IF;
          IF movement_record.movement_type IN (
               'delivery_exception','investigation_resolution'
             ) AND source_outcome_kind <> 'short_missing' THEN
            RAISE EXCEPTION 'Investigation Movement must use short or missing custody';
          END IF;
          IF movement_record.movement_type = 'return_to_warehouse'
             AND source_outcome_kind NOT IN ('refused','damaged') THEN
            RAISE EXCEPTION 'Return Movement must use refused or damaged custody';
          END IF;

          SELECT count(*), coalesce(sum(allocation.quantity_base),0),
            count(*) FILTER (WHERE delivery_line.sku_id <> movement_record.sku_id
              OR delivery_line.delivery_line_id <> source_delivery_line_id
              OR delivery_dispatch.warehouse_id <> movement_record.warehouse_id
              OR assignment.tracking_policy <> movement_record.tracking_policy
              OR allocation.quantity_base > line_allocation.quantity_base
              OR (assignment.tracking_policy = 'serial'
                AND allocation.quantity_base <> line_allocation.quantity_base)
              OR identity_partition.partition_id IS NULL
              OR allocation.quantity_base <> CASE source_outcome_kind
                WHEN 'accepted' THEN identity_partition.accepted_quantity_base
                WHEN 'refused' THEN identity_partition.refused_quantity_base
                WHEN 'damaged' THEN identity_partition.damaged_quantity_base
                WHEN 'short_missing' THEN
                  identity_partition.short_missing_quantity_base
                ELSE identity_partition.still_undelivered_quantity_base
              END)
            INTO allocation_count, allocation_quantity, invalid_count
          FROM stock_movement_identity_allocations allocation
          JOIN delivery_line_identity_allocations line_allocation
            ON line_allocation.allocation_id =
              allocation.delivery_line_identity_allocation_id
          JOIN delivery_lines delivery_line
            ON delivery_line.delivery_line_id = line_allocation.delivery_line_id
          JOIN delivery_dispatches delivery_dispatch
            ON delivery_dispatch.delivery_id = delivery_line.delivery_id
          JOIN pick_identity_assignments assignment
            ON assignment.pick_identity_assignment_id =
              line_allocation.pick_identity_assignment_id
          LEFT JOIN delivery_confirmation_identity_partitions identity_partition
            ON identity_partition.confirmation_line_id = source_confirmation_line_id
           AND identity_partition.delivery_line_identity_allocation_id =
             line_allocation.allocation_id
          WHERE allocation.movement_id = target_movement_id;

          IF (movement_record.tracking_policy = 'untracked' AND allocation_count <> 0)
             OR (movement_record.tracking_policy IN ('lot','serial') AND (
               allocation_count = 0 OR allocation_quantity <> movement_record.quantity_base
               OR invalid_count <> 0)) THEN
            RAISE EXCEPTION
              'Stock Movement identity allocations do not exactly match tracked quantity';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """CREATE CONSTRAINT TRIGGER trg_stock_movement_identity_exact
        AFTER INSERT ON stock_movements DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION validate_stock_movement_identity_integrity()"""
    )
    op.execute(
        """CREATE CONSTRAINT TRIGGER trg_stock_identity_allocation_exact
        AFTER INSERT ON stock_movement_identity_allocations DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION validate_stock_movement_identity_integrity()"""
    )
    op.execute(
        """
        CREATE FUNCTION validate_delivery_retry_integrity() RETURNS trigger AS $$
        DECLARE invalid_count integer;
        DECLARE allocated_quantity numeric(18,6);
        DECLARE source_quantity numeric(18,6);
        BEGIN
          SELECT count(*) INTO invalid_count
          FROM delivery_retry_allocations retry
          JOIN delivery_lines retry_line
            ON retry_line.delivery_line_id = retry.retry_delivery_line_id
          JOIN delivery_dispatches retry_delivery
            ON retry_delivery.delivery_id = retry_line.delivery_id
          JOIN delivery_exception_cases exception_case
            ON exception_case.exception_case_id = retry.source_exception_case_id
          JOIN delivery_confirmation_lines source_confirmation_line
            ON source_confirmation_line.confirmation_line_id =
              exception_case.confirmation_line_id
          JOIN delivery_lines source_line
            ON source_line.delivery_line_id = source_confirmation_line.delivery_line_id
          JOIN delivery_confirmations source_confirmation
            ON source_confirmation.confirmation_id = source_confirmation_line.confirmation_id
          WHERE retry.retry_allocation_id = NEW.retry_allocation_id
            AND (retry_line.source_exception_case_id <> retry.source_exception_case_id
              OR exception_case.exception_kind <> 'still_undelivered'
              OR exception_case.original_quantity_base <>
                source_confirmation_line.still_undelivered_quantity_base
              OR retry_delivery.dispatch_kind <> 'retry'
              OR retry_delivery.parent_delivery_id <> source_confirmation.delivery_id
              OR retry.quantity_base <> retry_line.quantity_base
              OR retry_line.pick_line_id <> source_line.pick_line_id
              OR retry_line.line_id <> source_line.line_id
              OR retry_line.sku_id <> source_line.sku_id);
          IF invalid_count <> 0 THEN
            RAISE EXCEPTION 'Retry allocation does not match source Exception custody';
          END IF;

          SELECT exception_case.original_quantity_base,
            coalesce(sum(retry.quantity_base),0)
            INTO source_quantity, allocated_quantity
          FROM delivery_exception_cases exception_case
          LEFT JOIN delivery_retry_allocations retry
            ON retry.source_exception_case_id = exception_case.exception_case_id
          WHERE exception_case.exception_case_id = NEW.source_exception_case_id
          GROUP BY exception_case.original_quantity_base;
          IF allocated_quantity > source_quantity THEN
            RAISE EXCEPTION 'Retry allocations exceed still-undelivered custody';
          END IF;

          SELECT count(*) INTO invalid_count
          FROM delivery_retry_allocations retry
          JOIN delivery_line_identity_allocations retry_identity
            ON retry_identity.delivery_line_id = retry.retry_delivery_line_id
          JOIN delivery_exception_cases exception_case
            ON exception_case.exception_case_id = retry.source_exception_case_id
          JOIN delivery_confirmation_lines source_confirmation_line
            ON source_confirmation_line.confirmation_line_id =
              exception_case.confirmation_line_id
          LEFT JOIN delivery_line_identity_allocations source_identity
            ON source_identity.delivery_line_id =
              source_confirmation_line.delivery_line_id
           AND source_identity.pick_identity_assignment_id =
             retry_identity.pick_identity_assignment_id
          LEFT JOIN delivery_confirmation_identity_partitions source_partition
            ON source_partition.confirmation_line_id = exception_case.confirmation_line_id
           AND source_partition.delivery_line_identity_allocation_id =
             source_identity.allocation_id
          WHERE retry.source_exception_case_id = NEW.source_exception_case_id
            AND (source_partition.partition_id IS NULL
              OR source_partition.still_undelivered_quantity_base = 0);
          IF invalid_count <> 0 THEN
            RAISE EXCEPTION 'Retry identity is not still-undelivered custody';
          END IF;

          SELECT count(*) INTO invalid_count
          FROM (
            SELECT source_identity.pick_identity_assignment_id
            FROM delivery_exception_cases exception_case
            JOIN delivery_confirmation_identity_partitions source_partition
              ON source_partition.confirmation_line_id =
                exception_case.confirmation_line_id
            JOIN delivery_line_identity_allocations source_identity
              ON source_identity.allocation_id =
                source_partition.delivery_line_identity_allocation_id
            LEFT JOIN delivery_retry_allocations retry
              ON retry.source_exception_case_id = exception_case.exception_case_id
            LEFT JOIN delivery_line_identity_allocations retry_identity
              ON retry_identity.delivery_line_id = retry.retry_delivery_line_id
             AND retry_identity.pick_identity_assignment_id =
               source_identity.pick_identity_assignment_id
            WHERE exception_case.exception_case_id = NEW.source_exception_case_id
            GROUP BY source_identity.pick_identity_assignment_id,
              source_partition.still_undelivered_quantity_base
            HAVING coalesce(sum(retry_identity.quantity_base),0) >
              source_partition.still_undelivered_quantity_base
          ) overallocated_identity;
          IF invalid_count <> 0 THEN
            RAISE EXCEPTION 'Retry identity allocations exceed still-undelivered custody';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """CREATE CONSTRAINT TRIGGER trg_delivery_retry_allocation_valid
        AFTER INSERT ON delivery_retry_allocations DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION validate_delivery_retry_integrity()"""
    )
    op.execute(
        """
        CREATE FUNCTION prevent_delivery_exception_ledger_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'Delivery Exception ledgers are immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table_name in (
        "delivery_line_identity_allocations",
        "stock_movement_identity_allocations",
        "delivery_confirmation_identity_partitions",
        "delivery_exception_cases",
        "delivery_exception_case_evidence",
        "delivery_exception_events",
        "delivery_exception_event_evidence",
        "return_to_warehouse_receipts",
        "return_to_warehouse_receipt_lines",
        "investigation_resolutions",
        "delivery_retry_allocations",
    ):
        op.execute(
            f"""CREATE TRIGGER trg_{table_name}_immutable
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION prevent_delivery_exception_ledger_mutation()"""
        )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM delivery_exception_cases) THEN
            RAISE EXCEPTION 'Cannot downgrade 0014 while Delivery Exception data exists';
          END IF;
        END $$
        """
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_delivery_confirmation_partition_exact "
        "ON delivery_confirmation_lines"
    )
    op.execute("DROP FUNCTION IF EXISTS validate_delivery_partition")
    op.execute("DROP TRIGGER IF EXISTS trg_delivery_line_lineage ON delivery_lines")
    op.execute("DROP FUNCTION IF EXISTS validate_delivery_line_lineage")
    op.execute("DROP TRIGGER IF EXISTS trg_delivery_line_identity_exact ON delivery_lines")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_delivery_identity_allocation_exact "
        "ON delivery_line_identity_allocations"
    )
    op.execute("DROP FUNCTION IF EXISTS validate_delivery_line_identity_integrity")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_confirmation_line_identity_exact ON delivery_confirmation_lines"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_confirmation_identity_partition_exact "
        "ON delivery_confirmation_identity_partitions"
    )
    op.execute("DROP FUNCTION IF EXISTS validate_confirmation_identity_integrity")
    op.execute("DROP TRIGGER IF EXISTS trg_stock_movement_identity_exact ON stock_movements")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_stock_identity_allocation_exact "
        "ON stock_movement_identity_allocations"
    )
    op.execute("DROP FUNCTION IF EXISTS validate_stock_movement_identity_integrity")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_delivery_retry_allocation_valid ON delivery_retry_allocations"
    )
    op.execute("DROP FUNCTION IF EXISTS validate_delivery_retry_integrity")
    for table_name in (
        "delivery_retry_allocations",
        "investigation_resolutions",
        "return_to_warehouse_receipt_lines",
        "return_to_warehouse_receipts",
        "delivery_exception_events",
        "delivery_exception_event_evidence",
        "delivery_exception_case_evidence",
        "delivery_exception_cases",
        "delivery_confirmation_identity_partitions",
        "stock_movement_identity_allocations",
        "delivery_line_identity_allocations",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_immutable ON {table_name}")
    op.execute("DROP FUNCTION IF EXISTS prevent_delivery_exception_ledger_mutation")
    op.execute("DROP TABLE delivery_retry_allocations")
    op.execute("DROP TABLE investigation_resolutions")
    op.execute("DROP TABLE return_to_warehouse_receipt_lines")
    op.execute("DROP TABLE return_to_warehouse_receipts")
    op.execute("DROP TABLE delivery_exception_state")
    op.execute("DROP TABLE delivery_exception_event_evidence")
    op.execute("DROP TABLE delivery_exception_events")
    op.execute("DROP TABLE delivery_exception_case_evidence")
    op.execute("ALTER TABLE delivery_lines DROP CONSTRAINT fk_delivery_line_source_exception")
    op.execute("DROP TABLE delivery_exception_cases")
    op.execute("DROP TABLE delivery_confirmation_identity_partitions")
    op.execute("DROP TABLE stock_movement_identity_allocations")
    op.execute("DROP TABLE delivery_line_identity_allocations")
    op.execute(
        "ALTER TABLE delivery_confirmation_lines DROP CONSTRAINT "
        "ck_delivery_confirmation_line_partition"
    )
    op.execute(
        "ALTER TABLE delivery_confirmation_lines DROP CONSTRAINT "
        "ck_delivery_confirmation_line_value"
    )
    op.execute(
        "ALTER TABLE delivery_confirmation_lines DROP COLUMN still_undelivered_quantity_base"
    )
    op.execute("ALTER TABLE delivery_confirmation_lines DROP COLUMN short_missing_quantity_base")
    op.execute("ALTER TABLE delivery_confirmation_lines DROP COLUMN damaged_quantity_base")
    op.execute("ALTER TABLE delivery_confirmation_lines DROP COLUMN refused_quantity_base")
    op.execute(
        "ALTER TABLE delivery_confirmation_lines ALTER COLUMN outbound_movement_id SET NOT NULL"
    )
    op.execute(
        "ALTER TABLE delivery_confirmation_lines ADD CONSTRAINT "
        "ck_delivery_confirmation_line_quantity CHECK (accepted_quantity_base > 0)"
    )
    op.execute(
        "ALTER TABLE delivery_confirmation_lines ADD CONSTRAINT "
        "ck_delivery_confirmation_line_value CHECK (unit_cost >= 0 AND value_delta <= 0)"
    )
    op.execute("DROP INDEX uq_initial_delivery_line_pick")
    op.execute("DROP INDEX uq_initial_delivery_line_staging_movement")
    op.execute("DROP INDEX uq_initial_delivery_line_transit_movement")
    op.execute("ALTER TABLE delivery_lines DROP COLUMN source_exception_case_id")
    op.execute("ALTER TABLE delivery_lines ALTER COLUMN staging_movement_id SET NOT NULL")
    op.execute("ALTER TABLE delivery_lines ALTER COLUMN transit_movement_id SET NOT NULL")
    op.execute(
        "ALTER TABLE delivery_lines ADD CONSTRAINT delivery_lines_pick_line_id_key "
        "UNIQUE (pick_line_id)"
    )
    op.execute(
        "ALTER TABLE delivery_lines ADD CONSTRAINT "
        "delivery_lines_staging_movement_id_key UNIQUE (staging_movement_id)"
    )
    op.execute(
        "ALTER TABLE delivery_lines ADD CONSTRAINT "
        "delivery_lines_transit_movement_id_key UNIQUE (transit_movement_id)"
    )
    op.execute("ALTER TABLE delivery_dispatches DROP CONSTRAINT ck_delivery_dispatch_kind")
    op.execute("ALTER TABLE delivery_dispatches DROP COLUMN parent_delivery_id")
    op.execute("ALTER TABLE delivery_dispatches DROP COLUMN dispatch_kind")
    op.execute("DROP TRIGGER cod_conversion_approval_immutable ON cod_on_account_conversions")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION protect_cod_conversion_approval() RETURNS trigger AS $$
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
    op.execute(
        "ALTER TABLE cod_on_account_conversions DROP CONSTRAINT ck_cod_conversion_consumed_amount"
    )
    op.execute("ALTER TABLE cod_on_account_conversions DROP COLUMN consumed_amount")
    op.execute("DROP INDEX uq_warehouse_active_investigation")
    op.execute(
        "DELETE FROM warehouse_stock_locations location WHERE custody = 'investigation' "
        "AND NOT EXISTS (SELECT 1 FROM stock_movements movement "
        "WHERE movement.location_id = location.location_id)"
    )
    op.execute(
        "ALTER TABLE warehouse_stock_locations DROP CONSTRAINT ck_warehouse_stock_locations_custody"
    )
    op.execute(
        "ALTER TABLE warehouse_stock_locations ADD CONSTRAINT "
        "ck_warehouse_stock_locations_custody CHECK (custody IN "
        "('available','quarantine','dispatch_staging','in_transit'))"
    )
    op.execute("ALTER TABLE stock_movements DROP CONSTRAINT ck_stock_movements_leg")
    op.execute("ALTER TABLE stock_movements DROP CONSTRAINT ck_stock_movements_type")
    op.execute(
        "ALTER TABLE stock_movements ADD CONSTRAINT ck_stock_movements_type CHECK ("
        "movement_type IN ('opening_stock','pick','pick_reversal','dispatch',"
        "'delivery_confirmation'))"
    )
    op.execute(
        "ALTER TABLE stock_movements ADD CONSTRAINT ck_stock_movements_leg CHECK ("
        "(movement_type = 'opening_stock' AND movement_leg = 'opening_in') OR "
        "(movement_type = 'pick' AND movement_leg IN "
        "('pick_available_out','pick_staging_in')) OR "
        "(movement_type = 'pick_reversal' AND movement_leg IN "
        "('pick_reversal_staging_out','pick_reversal_available_in')) OR "
        "(movement_type = 'dispatch' AND movement_leg IN "
        "('dispatch_staging_out','dispatch_transit_in')) OR "
        "(movement_type = 'delivery_confirmation' AND movement_leg = 'delivery_outbound'))"
    )
