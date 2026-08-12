"""Correct issued Delivery Receipts through immutable linked postings.

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    statements = """
      ALTER TABLE stock_movements ALTER COLUMN movement_leg TYPE varchar(64);
      ALTER TABLE stock_movements DROP CONSTRAINT ck_stock_movements_type;
      ALTER TABLE stock_movements DROP CONSTRAINT ck_stock_movements_leg;
      ALTER TABLE stock_movements ADD CONSTRAINT ck_stock_movements_type CHECK (
        movement_type IN ('opening_stock','pick','pick_reversal','dispatch',
          'delivery_confirmation','delivery_exception','return_to_warehouse',
          'investigation_resolution','delivery_correction')
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
        OR (movement_type = 'delivery_correction' AND movement_leg IN
          ('correction_accepted_reversal_in',
           'correction_exception_reversal_transit_in',
           'correction_exception_reversal_investigation_out',
           'correction_accepted_replacement_out',
           'correction_exception_replacement_transit_out',
           'correction_exception_replacement_investigation_in'))
      );

      ALTER TABLE delivery_receipts DROP CONSTRAINT delivery_receipts_confirmation_id_key;
      ALTER TABLE delivery_receipts
        ADD COLUMN correction_id uuid,
        ADD COLUMN corrects_delivery_receipt_id uuid UNIQUE
          REFERENCES delivery_receipts(delivery_receipt_id);
      CREATE UNIQUE INDEX uq_original_delivery_receipt_confirmation
        ON delivery_receipts(confirmation_id) WHERE correction_id IS NULL;

      ALTER TABLE draft_invoices
        DROP CONSTRAINT draft_invoices_delivery_confirmation_id_key,
        DROP CONSTRAINT draft_invoices_source_event_id_key,
        DROP CONSTRAINT ck_draft_invoice_totals;
      ALTER TABLE draft_invoices
        ADD COLUMN invoice_kind varchar(20) NOT NULL DEFAULT 'original',
        ADD COLUMN correction_id uuid,
        ADD COLUMN reversal_of_draft_invoice_id uuid UNIQUE
          REFERENCES draft_invoices(draft_invoice_id),
        ADD COLUMN replaces_draft_invoice_id uuid UNIQUE
          REFERENCES draft_invoices(draft_invoice_id),
        ADD CONSTRAINT ck_draft_invoice_kind CHECK (
          invoice_kind IN ('original','reversal','replacement')),
        ADD CONSTRAINT ck_draft_invoice_signed_totals CHECK (
          ((invoice_kind IN ('original','replacement')
            AND grand_total = subtotal - discount_total + tax_total
            AND subtotal >= 0 AND discount_total >= 0
            AND tax_total >= 0 AND grand_total >= 0)
          OR (invoice_kind = 'reversal'
            AND subtotal <= 0 AND discount_total <= 0
            AND tax_total <= 0 AND grand_total <= 0))) NOT VALID,
        ADD CONSTRAINT uq_draft_invoice_event_kind UNIQUE(source_event_id,invoice_kind);
      CREATE UNIQUE INDEX uq_original_draft_invoice_confirmation
        ON draft_invoices(delivery_confirmation_id) WHERE invoice_kind = 'original';

      ALTER TABLE draft_invoice_lines DROP CONSTRAINT ck_draft_invoice_line_values;
      ALTER TABLE draft_invoice_lines
        ADD COLUMN invoice_kind varchar(20) NOT NULL DEFAULT 'original',
        ADD CONSTRAINT ck_draft_invoice_line_kind CHECK (
          invoice_kind IN ('original','reversal','replacement')),
        ADD CONSTRAINT ck_draft_invoice_line_signed_values CHECK (
          unit_price >= 0
          AND ((invoice_kind IN ('original','replacement')
            AND line_total = subtotal - discount_amount + tax_amount
            AND accepted_quantity_base > 0 AND subtotal >= 0
            AND discount_amount >= 0 AND tax_amount >= 0 AND line_total >= 0)
          OR (invoice_kind = 'reversal'
            AND accepted_quantity_base < 0 AND subtotal <= 0
            AND discount_amount <= 0 AND tax_amount <= 0 AND line_total <= 0))) NOT VALID;

      CREATE TABLE delivery_corrections (
        correction_id uuid PRIMARY KEY,
        original_delivery_receipt_id uuid NOT NULL UNIQUE
          REFERENCES delivery_receipts(delivery_receipt_id),
        delivery_id uuid NOT NULL REFERENCES delivery_dispatches(delivery_id),
        confirmation_id uuid NOT NULL REFERENCES delivery_confirmations(confirmation_id),
        branch_id uuid NOT NULL REFERENCES branches(branch_id),
        warehouse_id uuid NOT NULL REFERENCES warehouses(warehouse_id),
        reason varchar(500) NOT NULL,
        requested_by varchar(200) NOT NULL REFERENCES users(subject),
        correlation_id varchar(100) NOT NULL,
        idempotency_key varchar(200) NOT NULL,
        base_currency varchar(3) NOT NULL,
        affected_inventory_value numeric(24,6) NOT NULL,
        affected_draft_invoice_value numeric(24,6) NOT NULL,
        affected_value_base_currency numeric(24,6) NOT NULL,
        original_draft_invoice_id uuid NOT NULL REFERENCES draft_invoices(draft_invoice_id),
        reversal_draft_invoice_id uuid NOT NULL UNIQUE,
        replacement_draft_invoice_id uuid UNIQUE,
        requested_at timestamptz NOT NULL DEFAULT now(),
        sealed_at timestamptz,
        CONSTRAINT uq_delivery_correction_actor_key UNIQUE(requested_by,idempotency_key),
        CONSTRAINT ck_delivery_correction_reason CHECK (btrim(reason) <> ''),
        CONSTRAINT ck_delivery_correction_affected_value CHECK (
          affected_inventory_value >= 0 AND affected_draft_invoice_value >= 0
          AND affected_value_base_currency = greatest(
            affected_inventory_value,affected_draft_invoice_value))
      );
      ALTER TABLE delivery_receipts
        ADD CONSTRAINT delivery_receipts_correction_id_fkey
          FOREIGN KEY(correction_id) REFERENCES delivery_corrections(correction_id),
        ADD CONSTRAINT delivery_receipt_correction_shape CHECK (
          (correction_id IS NULL AND corrects_delivery_receipt_id IS NULL)
          OR (correction_id IS NOT NULL AND corrects_delivery_receipt_id IS NOT NULL));
      CREATE UNIQUE INDEX delivery_receipts_correction_id_key
        ON delivery_receipts(correction_id) WHERE correction_id IS NOT NULL;
      ALTER TABLE draft_invoices
        ADD CONSTRAINT draft_invoices_correction_id_fkey
          FOREIGN KEY(correction_id) REFERENCES delivery_corrections(correction_id),
        ADD CONSTRAINT draft_invoice_source_shape CHECK (
          (invoice_kind = 'original' AND correction_id IS NULL
            AND reversal_of_draft_invoice_id IS NULL AND replaces_draft_invoice_id IS NULL)
          OR (invoice_kind = 'reversal' AND correction_id IS NOT NULL
            AND reversal_of_draft_invoice_id IS NOT NULL
            AND replaces_draft_invoice_id IS NULL)
          OR (invoice_kind = 'replacement' AND correction_id IS NOT NULL
            AND reversal_of_draft_invoice_id IS NULL
            AND replaces_draft_invoice_id IS NOT NULL));

      CREATE TABLE delivery_correction_lines (
        correction_line_id uuid PRIMARY KEY,
        correction_id uuid NOT NULL REFERENCES delivery_corrections(correction_id),
        confirmation_line_id uuid NOT NULL
          REFERENCES delivery_confirmation_lines(confirmation_line_id),
        delivery_line_id uuid NOT NULL REFERENCES delivery_lines(delivery_line_id),
        line_id uuid NOT NULL,
        sku_id uuid NOT NULL REFERENCES skus(sku_id),
        accepted_quantity_base numeric(18,6) NOT NULL,
        refused_quantity_base numeric(18,6) NOT NULL,
        damaged_quantity_base numeric(18,6) NOT NULL,
        short_missing_quantity_base numeric(18,6) NOT NULL,
        still_undelivered_quantity_base numeric(18,6) NOT NULL,
        unit_cost numeric(18,6) NOT NULL,
        value_delta numeric(24,6) NOT NULL,
        CONSTRAINT uq_delivery_correction_line UNIQUE(correction_id,delivery_line_id),
        CONSTRAINT uq_correction_line_source UNIQUE(correction_line_id,confirmation_line_id),
        CONSTRAINT ck_delivery_correction_line_nonnegative CHECK (
          accepted_quantity_base >= 0 AND refused_quantity_base >= 0
          AND damaged_quantity_base >= 0 AND short_missing_quantity_base >= 0
          AND still_undelivered_quantity_base >= 0),
        CONSTRAINT ck_delivery_correction_line_value CHECK (
          unit_cost >= 0 AND value_delta <= 0)
      );
      CREATE TABLE delivery_correction_identity_positions (
        correction_identity_position_id uuid PRIMARY KEY,
        correction_line_id uuid NOT NULL
          REFERENCES delivery_correction_lines(correction_line_id),
        delivery_line_identity_allocation_id uuid NOT NULL
          REFERENCES delivery_line_identity_allocations(allocation_id),
        accepted_quantity_base numeric(18,6) NOT NULL DEFAULT 0,
        refused_quantity_base numeric(18,6) NOT NULL DEFAULT 0,
        damaged_quantity_base numeric(18,6) NOT NULL DEFAULT 0,
        short_missing_quantity_base numeric(18,6) NOT NULL DEFAULT 0,
        still_undelivered_quantity_base numeric(18,6) NOT NULL DEFAULT 0,
        CONSTRAINT uq_delivery_correction_identity_position UNIQUE(
          correction_line_id,delivery_line_identity_allocation_id),
        CONSTRAINT ck_delivery_correction_identity_nonnegative CHECK (
          accepted_quantity_base >= 0 AND refused_quantity_base >= 0
          AND damaged_quantity_base >= 0 AND short_missing_quantity_base >= 0
          AND still_undelivered_quantity_base >= 0)
      );
      CREATE TABLE delivery_correction_evidence (
        correction_id uuid NOT NULL REFERENCES delivery_corrections(correction_id),
        evidence_id uuid NOT NULL REFERENCES delivery_evidence(evidence_id),
        PRIMARY KEY(correction_id,evidence_id)
      );
      CREATE TABLE delivery_correction_authorizations (
        correction_id uuid PRIMARY KEY REFERENCES delivery_corrections(correction_id),
        authorized_by varchar(200) NOT NULL REFERENCES users(subject),
        approval_authority_id uuid NOT NULL
          REFERENCES approval_authorities(approval_authority_id),
        idempotency_key varchar(200) NOT NULL,
        correlation_id varchar(100) NOT NULL,
        authorized_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT uq_correction_authorization_key UNIQUE(authorized_by,idempotency_key)
      );
      CREATE TABLE delivery_correction_movement_effects (
        movement_effect_id uuid PRIMARY KEY,
        correction_id uuid NOT NULL REFERENCES delivery_corrections(correction_id),
        correction_line_id uuid NOT NULL REFERENCES delivery_correction_lines(correction_line_id),
        effect_role varchar(20) NOT NULL CHECK (
          effect_role IN ('original','reversal','replacement')),
        outcome varchar(30) NOT NULL CHECK (outcome IN ('accepted','short_missing')),
        movement_id uuid NOT NULL REFERENCES stock_movements(movement_id),
        original_movement_id uuid REFERENCES stock_movements(movement_id),
        CONSTRAINT uq_correction_movement_effect UNIQUE(
          correction_id,effect_role,movement_id)
      );

      ALTER TABLE delivery_exception_cases
        DROP CONSTRAINT uq_delivery_exception_case_kind,
        ADD COLUMN correction_line_id uuid REFERENCES delivery_correction_lines(correction_line_id);
      CREATE UNIQUE INDEX uq_delivery_exception_original_case_kind
        ON delivery_exception_cases(confirmation_line_id,exception_kind)
        WHERE correction_line_id IS NULL;
      CREATE UNIQUE INDEX uq_delivery_exception_correction_case_kind
        ON delivery_exception_cases(correction_line_id,exception_kind)
        WHERE correction_line_id IS NOT NULL;
      ALTER TABLE delivery_exception_events
        DROP CONSTRAINT delivery_exception_events_event_type_check;
      ALTER TABLE delivery_exception_events ADD CONSTRAINT ck_delivery_exception_event_type
        CHECK (event_type IN ('opened','return_received','retry_allocated','recovered',
          'carrier_claim_resolved','inventory_adjustment_resolved',
          'superseded_by_correction'));

    """
    for statement in statements.split(";\n"):
        if statement.strip():
            op.execute(statement)

    op.execute(
        """
        CREATE FUNCTION validate_delivery_correction_line() RETURNS trigger AS $$
        DECLARE source_line record;
        BEGIN
          SELECT dl.quantity_base AS dispatched, dcl.confirmation_id,
                 cl.line_id, cl.sku_id, cl.unit_cost
            INTO source_line
          FROM delivery_lines dl
          JOIN delivery_confirmation_lines cl ON cl.delivery_line_id = dl.delivery_line_id
          JOIN delivery_corrections dcl ON dcl.correction_id = NEW.correction_id
          WHERE dl.delivery_line_id = NEW.delivery_line_id
            AND cl.confirmation_line_id = NEW.confirmation_line_id;
          IF NOT FOUND OR source_line.confirmation_id <> (
              SELECT confirmation_id FROM delivery_confirmation_lines
              WHERE confirmation_line_id = NEW.confirmation_line_id)
             OR NEW.line_id <> source_line.line_id
             OR NEW.sku_id <> source_line.sku_id
             OR NEW.unit_cost <> source_line.unit_cost
             OR NEW.value_delta <> round(
                  -(NEW.accepted_quantity_base * NEW.unit_cost), 6)
             OR NEW.accepted_quantity_base + NEW.refused_quantity_base
              + NEW.damaged_quantity_base + NEW.short_missing_quantity_base
              + NEW.still_undelivered_quantity_base <> source_line.dispatched THEN
            RAISE EXCEPTION 'Correction partition must exactly match its Delivery Line';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """CREATE TRIGGER trg_delivery_correction_line_exact
        BEFORE INSERT ON delivery_correction_lines
        FOR EACH ROW EXECUTE FUNCTION validate_delivery_correction_line()"""
    )
    op.execute(
        """
        CREATE FUNCTION validate_delivery_correction_identity() RETURNS trigger AS $$
        DECLARE target_line uuid;
        DECLARE line_record record;
        DECLARE count_positions integer;
        DECLARE invalid_positions integer;
        DECLARE accepted numeric(18,6);
        DECLARE refused numeric(18,6);
        DECLARE damaged numeric(18,6);
        DECLARE short_missing numeric(18,6);
        DECLARE still_undelivered numeric(18,6);
        BEGIN
          target_line := NEW.correction_line_id;
          SELECT cl.*, sku.tracking_policy INTO line_record
          FROM delivery_correction_lines cl
          JOIN skus sku ON sku.sku_id = cl.sku_id
          WHERE cl.correction_line_id = target_line;
          IF NOT FOUND THEN RETURN NEW; END IF;
          SELECT count(*), count(*) FILTER (WHERE
              allocation.delivery_line_id <> line_record.delivery_line_id
              OR position.accepted_quantity_base + position.refused_quantity_base
                + position.damaged_quantity_base + position.short_missing_quantity_base
                + position.still_undelivered_quantity_base <> allocation.quantity_base
              OR (assignment.tracking_policy = 'serial' AND
                (CASE WHEN position.accepted_quantity_base > 0 THEN 1 ELSE 0 END
                 + CASE WHEN position.refused_quantity_base > 0 THEN 1 ELSE 0 END
                 + CASE WHEN position.damaged_quantity_base > 0 THEN 1 ELSE 0 END
                 + CASE WHEN position.short_missing_quantity_base > 0 THEN 1 ELSE 0 END
                 + CASE WHEN position.still_undelivered_quantity_base > 0 THEN 1 ELSE 0 END) <> 1)),
            coalesce(sum(position.accepted_quantity_base),0),
            coalesce(sum(position.refused_quantity_base),0),
            coalesce(sum(position.damaged_quantity_base),0),
            coalesce(sum(position.short_missing_quantity_base),0),
            coalesce(sum(position.still_undelivered_quantity_base),0)
          INTO count_positions,invalid_positions,accepted,refused,damaged,
            short_missing,still_undelivered
          FROM delivery_correction_identity_positions position
          JOIN delivery_line_identity_allocations allocation
            ON allocation.allocation_id = position.delivery_line_identity_allocation_id
          JOIN pick_identity_assignments assignment
            ON assignment.pick_identity_assignment_id = allocation.pick_identity_assignment_id
          WHERE position.correction_line_id = target_line;
          IF (line_record.tracking_policy = 'untracked' AND count_positions <> 0)
             OR (line_record.tracking_policy IN ('lot','serial') AND (
               count_positions = 0 OR invalid_positions <> 0
               OR accepted <> line_record.accepted_quantity_base
               OR refused <> line_record.refused_quantity_base
               OR damaged <> line_record.damaged_quantity_base
               OR short_missing <> line_record.short_missing_quantity_base
               OR still_undelivered <> line_record.still_undelivered_quantity_base)) THEN
            RAISE EXCEPTION 'Correction identities must exactly match corrected custody';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """CREATE CONSTRAINT TRIGGER trg_delivery_correction_line_identity_exact
        AFTER INSERT ON delivery_correction_lines DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION validate_delivery_correction_identity()"""
    )
    op.execute(
        """CREATE CONSTRAINT TRIGGER trg_delivery_correction_identity_exact
        AFTER INSERT ON delivery_correction_identity_positions DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION validate_delivery_correction_identity()"""
    )
    op.execute(
        """
        CREATE FUNCTION validate_delivery_correction_movement_identity() RETURNS trigger AS $$
        DECLARE movement_record record;
        DECLARE effect_record record;
        DECLARE allocation_count integer;
        DECLARE allocation_quantity numeric(18,6);
        DECLARE invalid_count integer;
        BEGIN
          SELECT movement.*, sku.tracking_policy INTO movement_record
          FROM stock_movements movement JOIN skus sku ON sku.sku_id = movement.sku_id
          WHERE movement.movement_id = NEW.movement_id;
          IF NOT FOUND OR movement_record.movement_type <> 'delivery_correction' THEN
            RETURN NEW;
          END IF;
          SELECT effect.*, line.delivery_line_id, line.sku_id AS source_sku_id,
                 correction.warehouse_id
            INTO effect_record
          FROM delivery_correction_movement_effects effect
          JOIN delivery_correction_lines line
            ON line.correction_line_id = effect.correction_line_id
          JOIN delivery_corrections correction
            ON correction.correction_id = effect.correction_id
          WHERE effect.movement_id = NEW.movement_id;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'Correction Movement is not linked to its correction effect';
          END IF;
          SELECT count(*), coalesce(sum(linked.quantity_base), 0),
                 count(*) FILTER (WHERE
                   linked.delivery_line_id <> effect_record.delivery_line_id
                   OR linked.sku_id <> effect_record.source_sku_id
                   OR linked.tracking_policy <> movement_record.tracking_policy
                   OR linked.expected_quantity IS NULL
                   OR linked.quantity_base <> linked.expected_quantity)
            INTO allocation_count, allocation_quantity, invalid_count
          FROM (
            SELECT allocation.quantity_base, delivery_line.delivery_line_id,
                   delivery_line.sku_id, assignment.tracking_policy,
                   CASE
                     WHEN effect_record.effect_role = 'reversal' THEN
                       CASE effect_record.outcome
                         WHEN 'accepted' THEN coalesce(
                           prior.accepted_quantity_base, original.accepted_quantity_base)
                         WHEN 'short_missing' THEN coalesce(
                           prior.short_missing_quantity_base,
                           original.short_missing_quantity_base)
                         WHEN 'refused' THEN coalesce(
                           prior.refused_quantity_base, original.refused_quantity_base)
                         WHEN 'damaged' THEN coalesce(
                           prior.damaged_quantity_base, original.damaged_quantity_base)
                         ELSE coalesce(prior.still_undelivered_quantity_base,
                           original.still_undelivered_quantity_base) END
                     ELSE
                       CASE effect_record.outcome
                         WHEN 'accepted' THEN replacement.accepted_quantity_base
                         WHEN 'short_missing' THEN replacement.short_missing_quantity_base
                         WHEN 'refused' THEN replacement.refused_quantity_base
                         WHEN 'damaged' THEN replacement.damaged_quantity_base
                         ELSE replacement.still_undelivered_quantity_base END
                   END AS expected_quantity
            FROM stock_movement_identity_allocations allocation
            JOIN delivery_line_identity_allocations line_allocation
              ON line_allocation.allocation_id =
                 allocation.delivery_line_identity_allocation_id
            JOIN delivery_lines delivery_line
              ON delivery_line.delivery_line_id = line_allocation.delivery_line_id
            JOIN pick_identity_assignments assignment
              ON assignment.pick_identity_assignment_id =
                 line_allocation.pick_identity_assignment_id
            LEFT JOIN delivery_confirmation_identity_partitions original
              ON original.confirmation_line_id =
                 (SELECT confirmation_line_id FROM delivery_correction_lines
                  WHERE correction_line_id = effect_record.correction_line_id)
             AND original.delivery_line_identity_allocation_id = line_allocation.allocation_id
            LEFT JOIN delivery_correction_identity_positions replacement
              ON replacement.correction_line_id = effect_record.correction_line_id
             AND replacement.delivery_line_identity_allocation_id = line_allocation.allocation_id
            LEFT JOIN delivery_corrections current_correction
              ON current_correction.correction_id = effect_record.correction_id
            LEFT JOIN delivery_receipts source_receipt
              ON source_receipt.delivery_receipt_id =
                 current_correction.original_delivery_receipt_id
            LEFT JOIN delivery_correction_lines prior_line
              ON prior_line.correction_id = source_receipt.correction_id
             AND prior_line.delivery_line_id = delivery_line.delivery_line_id
            LEFT JOIN delivery_correction_identity_positions prior
              ON prior.correction_line_id = prior_line.correction_line_id
             AND prior.delivery_line_identity_allocation_id = line_allocation.allocation_id
            WHERE allocation.movement_id = NEW.movement_id
          ) linked;
          IF (movement_record.tracking_policy = 'untracked' AND allocation_count <> 0)
             OR (movement_record.tracking_policy IN ('lot','serial') AND (
               allocation_count = 0 OR allocation_quantity <> movement_record.quantity_base
               OR invalid_count <> 0)) THEN
            RAISE EXCEPTION
              'Correction Movement identities do not exactly match its outcome partition';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """CREATE CONSTRAINT TRIGGER trg_correction_movement_identity_exact
        AFTER INSERT ON stock_movements DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION validate_delivery_correction_movement_identity()"""
    )
    op.execute(
        """CREATE CONSTRAINT TRIGGER trg_correction_movement_allocation_exact
        AFTER INSERT ON stock_movement_identity_allocations DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION validate_delivery_correction_movement_identity()"""
    )
    op.execute(
        """
        CREATE FUNCTION correction_movement_is_immediate_source(
          target_correction_id uuid,
          target_correction_line_id uuid,
          target_outcome varchar,
          target_movement_id uuid
        ) RETURNS boolean AS $$
          SELECT EXISTS (
            SELECT 1
            FROM delivery_correction_lines current_line
            JOIN delivery_corrections current_correction
              ON current_correction.correction_id = current_line.correction_id
            JOIN delivery_receipts source_receipt
              ON source_receipt.delivery_receipt_id =
                 current_correction.original_delivery_receipt_id
            JOIN delivery_confirmation_lines confirmation_line
              ON confirmation_line.confirmation_line_id =
                 current_line.confirmation_line_id
            LEFT JOIN delivery_correction_lines prior_line
              ON prior_line.correction_id = source_receipt.correction_id
             AND prior_line.delivery_line_id = current_line.delivery_line_id
            WHERE current_line.correction_id = target_correction_id
              AND current_line.correction_line_id = target_correction_line_id
              AND (
                (target_outcome = 'accepted' AND (
                  (source_receipt.correction_id IS NULL
                   AND confirmation_line.outbound_movement_id = target_movement_id)
                  OR (source_receipt.correction_id IS NOT NULL AND EXISTS (
                    SELECT 1 FROM delivery_correction_movement_effects prior_effect
                    WHERE prior_effect.correction_id = source_receipt.correction_id
                      AND prior_effect.correction_line_id = prior_line.correction_line_id
                      AND prior_effect.effect_role = 'replacement'
                      AND prior_effect.outcome = 'accepted'
                      AND prior_effect.movement_id = target_movement_id))))
                OR (target_outcome = 'short_missing' AND (
                  (source_receipt.correction_id IS NULL AND EXISTS (
                    SELECT 1 FROM delivery_exception_cases source_case
                    WHERE source_case.confirmation_line_id =
                          current_line.confirmation_line_id
                      AND source_case.correction_line_id IS NULL
                      AND source_case.exception_kind = 'short_missing'
                      AND target_movement_id IN (
                        source_case.investigation_out_movement_id,
                        source_case.investigation_in_movement_id)))
                  OR (source_receipt.correction_id IS NOT NULL AND EXISTS (
                    SELECT 1 FROM delivery_correction_movement_effects prior_effect
                    WHERE prior_effect.correction_id = source_receipt.correction_id
                      AND prior_effect.correction_line_id = prior_line.correction_line_id
                      AND prior_effect.effect_role = 'replacement'
                      AND prior_effect.outcome = 'short_missing'
                      AND prior_effect.movement_id = target_movement_id))))
              )
          )
        $$ LANGUAGE sql STABLE STRICT
        """
    )
    op.execute(
        """
        CREATE FUNCTION validate_delivery_correction_movement_effect() RETURNS trigger AS $$
        DECLARE effect_record record;
        DECLARE movement_record record;
        DECLARE source_movement record;
        DECLARE expected_quantity numeric(18,6);
        DECLARE expected_value numeric(24,6);
        DECLARE location_custody varchar(30);
        BEGIN
          IF TG_TABLE_NAME = 'delivery_correction_movement_effects' THEN
            SELECT effect.*, line.correction_id AS line_correction_id,
                   line.sku_id AS line_sku_id, line.unit_cost AS line_unit_cost,
                   line.accepted_quantity_base, line.short_missing_quantity_base,
                   correction.warehouse_id AS correction_warehouse_id,
                   correction.base_currency AS correction_base_currency
              INTO effect_record
            FROM delivery_correction_movement_effects effect
            JOIN delivery_correction_lines line
              ON line.correction_line_id = effect.correction_line_id
            JOIN delivery_corrections correction
              ON correction.correction_id = effect.correction_id
            WHERE effect.movement_effect_id = NEW.movement_effect_id;
          ELSE
            SELECT effect.*, line.correction_id AS line_correction_id,
                   line.sku_id AS line_sku_id, line.unit_cost AS line_unit_cost,
                   line.accepted_quantity_base, line.short_missing_quantity_base,
                   correction.warehouse_id AS correction_warehouse_id,
                   correction.base_currency AS correction_base_currency
              INTO effect_record
            FROM delivery_correction_movement_effects effect
            JOIN delivery_correction_lines line
              ON line.correction_line_id = effect.correction_line_id
            JOIN delivery_corrections correction
              ON correction.correction_id = effect.correction_id
            WHERE effect.movement_id = NEW.movement_id
              AND effect.effect_role IN ('reversal','replacement')
            LIMIT 1;
            IF NOT FOUND AND NEW.movement_type = 'delivery_correction' THEN
              RAISE EXCEPTION
                'Correction Movement is not linked to its correction effect';
            ELSIF NOT FOUND THEN
              RETURN NEW;
            END IF;
          END IF;
          SELECT movement.*, location.custody INTO movement_record
          FROM stock_movements movement
          JOIN warehouse_stock_locations location
            ON location.location_id = movement.location_id
          WHERE movement.movement_id = effect_record.movement_id;
          IF NOT FOUND OR effect_record.line_correction_id <> effect_record.correction_id
             OR movement_record.sku_id <> effect_record.line_sku_id
             OR movement_record.warehouse_id <> effect_record.correction_warehouse_id
             OR movement_record.unit_cost <> effect_record.line_unit_cost
             OR movement_record.base_currency <> effect_record.correction_base_currency THEN
            RAISE EXCEPTION
              'Correction Movement economics do not belong to its correction line';
          END IF;
          IF effect_record.effect_role = 'original' THEN
            IF effect_record.original_movement_id IS NOT NULL
               OR NOT correction_movement_is_immediate_source(
                 effect_record.correction_id,effect_record.correction_line_id,
                 effect_record.outcome,effect_record.movement_id) THEN
              RAISE EXCEPTION
                'Original correction effect must be the exact immediate source Movement';
            END IF;
            RETURN NEW;
          END IF;
          IF movement_record.movement_type <> 'delivery_correction'
             OR movement_record.source_reference <>
                'DELIVERY-CORRECTION:' || effect_record.correction_id::text THEN
            RAISE EXCEPTION
              'Posted correction Movement must identify its immutable correction source';
          END IF;
          IF effect_record.effect_role = 'reversal' THEN
            SELECT * INTO source_movement FROM stock_movements
              WHERE movement_id = effect_record.original_movement_id;
            IF NOT FOUND
               OR movement_record.reversal_of_movement_id IS DISTINCT FROM
                  effect_record.original_movement_id
               OR NOT correction_movement_is_immediate_source(
                 effect_record.correction_id,effect_record.correction_line_id,
                 effect_record.outcome,effect_record.original_movement_id)
               OR movement_record.quantity_base <> source_movement.quantity_base
               OR movement_record.unit_cost <> source_movement.unit_cost
               OR movement_record.value_delta <> -source_movement.value_delta
               OR movement_record.location_id <> source_movement.location_id
               OR NOT (
                 (effect_record.outcome = 'accepted'
                  AND source_movement.movement_leg IN (
                    'delivery_outbound','correction_accepted_replacement_out')
                  AND movement_record.movement_leg = 'correction_accepted_reversal_in')
                 OR (effect_record.outcome = 'short_missing'
                  AND source_movement.movement_leg IN (
                    'exception_transit_out','correction_exception_replacement_transit_out')
                  AND movement_record.movement_leg =
                    'correction_exception_reversal_transit_in')
                 OR (effect_record.outcome = 'short_missing'
                  AND source_movement.movement_leg IN (
                    'exception_investigation_in',
                    'correction_exception_replacement_investigation_in')
                  AND movement_record.movement_leg =
                    'correction_exception_reversal_investigation_out')) THEN
              RAISE EXCEPTION
                'Correction reversal must exactly negate its immediate source Movement';
            END IF;
            RETURN NEW;
          END IF;
          expected_quantity := CASE effect_record.outcome
            WHEN 'accepted' THEN effect_record.accepted_quantity_base
            ELSE effect_record.short_missing_quantity_base END;
          expected_value := round(expected_quantity * effect_record.line_unit_cost, 6);
          IF movement_record.reversal_of_movement_id IS NOT NULL
             OR movement_record.quantity_base <> expected_quantity
             OR NOT (
               (effect_record.outcome = 'accepted'
                AND movement_record.movement_leg = 'correction_accepted_replacement_out'
                AND movement_record.custody = 'in_transit'
                AND movement_record.value_delta = -expected_value)
               OR (effect_record.outcome = 'short_missing'
                AND movement_record.movement_leg =
                  'correction_exception_replacement_transit_out'
                AND movement_record.custody = 'in_transit'
                AND movement_record.value_delta = -expected_value)
               OR (effect_record.outcome = 'short_missing'
                AND movement_record.movement_leg =
                  'correction_exception_replacement_investigation_in'
                AND movement_record.custody = 'investigation'
                AND movement_record.value_delta = expected_value)) THEN
            RAISE EXCEPTION
              'Replacement correction Movement must exactly post its corrected outcome';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """CREATE CONSTRAINT TRIGGER trg_correction_movement_effect_exact
        AFTER INSERT ON delivery_correction_movement_effects DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION validate_delivery_correction_movement_effect()"""
    )
    op.execute(
        """CREATE CONSTRAINT TRIGGER trg_correction_movement_economics_exact
        AFTER INSERT ON stock_movements DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION validate_delivery_correction_movement_effect()"""
    )
    op.execute(
        """
        CREATE FUNCTION validate_draft_invoice_line_kind() RETURNS trigger AS $$
        DECLARE parent_kind varchar(20);
        BEGIN
          SELECT invoice_kind INTO parent_kind FROM draft_invoices
            WHERE draft_invoice_id = NEW.draft_invoice_id;
          IF parent_kind IS NULL OR parent_kind <> NEW.invoice_kind THEN
            RAISE EXCEPTION 'Draft Invoice Line kind must match its parent';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """CREATE CONSTRAINT TRIGGER trg_draft_invoice_line_kind
        AFTER INSERT ON draft_invoice_lines DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION validate_draft_invoice_line_kind()"""
    )
    op.execute(
        """
        CREATE FUNCTION validate_correction_draft_invoice() RETURNS trigger AS $$
        DECLARE target_invoice_id uuid;
        DECLARE correction_invoice record;
        DECLARE correction_record record;
        DECLARE source_invoice record;
        DECLARE source_line_count integer;
        DECLARE correction_line_count integer;
        DECLARE expected_replacement_line_count integer;
        DECLARE invalid_line_count integer;
        DECLARE aggregated_subtotal numeric(24,6);
        DECLARE aggregated_discount numeric(24,6);
        DECLARE aggregated_tax numeric(24,6);
        DECLARE aggregated_total numeric(24,6);
        DECLARE root_invoice_id uuid;
        DECLARE next_invoice_id uuid;
        BEGIN
          target_invoice_id := CASE WHEN TG_TABLE_NAME = 'draft_invoices'
            THEN NEW.draft_invoice_id ELSE NEW.draft_invoice_id END;
          SELECT * INTO correction_invoice FROM draft_invoices
            WHERE draft_invoice_id = target_invoice_id;
          IF NOT FOUND OR correction_invoice.invoice_kind = 'original' THEN
            RETURN NEW;
          END IF;
          SELECT * INTO correction_record FROM delivery_corrections
            WHERE correction_id = correction_invoice.correction_id;
          SELECT * INTO source_invoice FROM draft_invoices
            WHERE draft_invoice_id = CASE correction_invoice.invoice_kind
              WHEN 'reversal' THEN correction_invoice.reversal_of_draft_invoice_id
              ELSE correction_invoice.replaces_draft_invoice_id END;
          IF NOT FOUND OR correction_record.correction_id IS NULL
             OR source_invoice.draft_invoice_id IS DISTINCT FROM
                correction_record.original_draft_invoice_id
             OR (correction_invoice.invoice_kind = 'reversal' AND
                correction_invoice.draft_invoice_id IS DISTINCT FROM
                  correction_record.reversal_draft_invoice_id)
             OR (correction_invoice.invoice_kind = 'replacement' AND
                correction_invoice.draft_invoice_id IS DISTINCT FROM
                  correction_record.replacement_draft_invoice_id)
             OR NOT EXISTS (
                SELECT 1 FROM outbox_events source_event
                WHERE source_event.outbox_event_id = correction_invoice.source_event_id
                  AND source_event.aggregate_type = 'delivery_correction'
                  AND source_event.aggregate_id = correction_record.correction_id
                  AND source_event.event_type = 'delivery.correction.posted.v1')
             OR correction_invoice.delivery_confirmation_id IS DISTINCT FROM
                source_invoice.delivery_confirmation_id
             OR correction_invoice.sales_order_id IS DISTINCT FROM source_invoice.sales_order_id
             OR correction_invoice.sales_order_revision_id IS DISTINCT FROM
                source_invoice.sales_order_revision_id
             OR correction_invoice.customer_id IS DISTINCT FROM source_invoice.customer_id
             OR correction_invoice.branch_id IS DISTINCT FROM source_invoice.branch_id
             OR correction_invoice.currency IS DISTINCT FROM source_invoice.currency THEN
            RAISE EXCEPTION
              'Correction Draft Invoice must preserve its immediate source identity';
          END IF;
          SELECT count(*) INTO source_line_count FROM draft_invoice_lines
            WHERE draft_invoice_id = source_invoice.draft_invoice_id;
          SELECT count(*), coalesce(sum(line.subtotal),0),
                 coalesce(sum(line.discount_amount),0), coalesce(sum(line.tax_amount),0),
                 coalesce(sum(line.line_total),0)
            INTO correction_line_count,aggregated_subtotal,aggregated_discount,
                 aggregated_tax,aggregated_total
          FROM draft_invoice_lines line WHERE line.draft_invoice_id = target_invoice_id;
          IF correction_invoice.invoice_kind = 'reversal' THEN
            SELECT count(*) INTO invalid_line_count
            FROM draft_invoice_lines reversed
            LEFT JOIN draft_invoice_lines source
              ON source.draft_invoice_id = source_invoice.draft_invoice_id
             AND source.line_id = reversed.line_id
            WHERE reversed.draft_invoice_id = target_invoice_id
              AND (source.draft_invoice_line_id IS NULL
                OR reversed.sku_id IS DISTINCT FROM source.sku_id
                OR reversed.accepted_quantity_base IS DISTINCT FROM
                   -source.accepted_quantity_base
                OR reversed.unit_price IS DISTINCT FROM source.unit_price
                OR reversed.subtotal IS DISTINCT FROM -source.subtotal
                OR reversed.discount_amount IS DISTINCT FROM -source.discount_amount
                OR reversed.tax_amount IS DISTINCT FROM -source.tax_amount
                OR reversed.line_total IS DISTINCT FROM -source.line_total);
            IF correction_invoice.subtotal IS DISTINCT FROM -source_invoice.subtotal
               OR correction_invoice.discount_total IS DISTINCT FROM
                  -source_invoice.discount_total
               OR correction_invoice.tax_total IS DISTINCT FROM -source_invoice.tax_total
               OR correction_invoice.grand_total IS DISTINCT FROM -source_invoice.grand_total
               OR correction_line_count <> source_line_count OR invalid_line_count <> 0 THEN
              RAISE EXCEPTION
                'Reversal Draft Invoice must exactly negate its immediate source';
            END IF;
          ELSE
            root_invoice_id := source_invoice.draft_invoice_id;
            LOOP
              SELECT replaces_draft_invoice_id INTO next_invoice_id
              FROM draft_invoices WHERE draft_invoice_id = root_invoice_id;
              EXIT WHEN next_invoice_id IS NULL;
              root_invoice_id := next_invoice_id;
            END LOOP;
            SELECT count(*) INTO expected_replacement_line_count
            FROM (
              SELECT line_id, sum(accepted_quantity_base) AS accepted_quantity_base
              FROM delivery_correction_lines
              WHERE correction_id = correction_invoice.correction_id
              GROUP BY line_id HAVING sum(accepted_quantity_base) > 0
            ) corrected;
            SELECT count(*) INTO invalid_line_count
            FROM draft_invoice_lines replacement
            LEFT JOIN draft_invoice_lines source
              ON source.draft_invoice_id = root_invoice_id
             AND source.line_id = replacement.line_id
            LEFT JOIN (
              SELECT line_id, sum(accepted_quantity_base) AS accepted_quantity_base
              FROM delivery_correction_lines
              WHERE correction_id = correction_invoice.correction_id
              GROUP BY line_id
            ) corrected ON corrected.line_id = replacement.line_id
            WHERE replacement.draft_invoice_id = target_invoice_id
              AND (source.draft_invoice_line_id IS NULL
                OR corrected.accepted_quantity_base IS NULL
                OR corrected.accepted_quantity_base <= 0
                OR replacement.sku_id IS DISTINCT FROM source.sku_id
                OR replacement.unit_price IS DISTINCT FROM source.unit_price
                OR replacement.accepted_quantity_base IS DISTINCT FROM
                   corrected.accepted_quantity_base
                OR replacement.subtotal IS DISTINCT FROM round(
                   source.subtotal * corrected.accepted_quantity_base
                   / source.accepted_quantity_base,
                   tradeflow_currency_minor_scale(correction_invoice.currency))
                OR replacement.discount_amount IS DISTINCT FROM round(
                   source.discount_amount * corrected.accepted_quantity_base
                   / source.accepted_quantity_base,
                   tradeflow_currency_minor_scale(correction_invoice.currency))
                OR replacement.tax_amount IS DISTINCT FROM round(
                   source.tax_amount * corrected.accepted_quantity_base
                   / source.accepted_quantity_base,
                   tradeflow_currency_minor_scale(correction_invoice.currency))
                OR replacement.line_total IS DISTINCT FROM
                   round(source.subtotal * corrected.accepted_quantity_base
                     / source.accepted_quantity_base,
                     tradeflow_currency_minor_scale(correction_invoice.currency))
                   - round(source.discount_amount * corrected.accepted_quantity_base
                     / source.accepted_quantity_base,
                     tradeflow_currency_minor_scale(correction_invoice.currency))
                   + round(source.tax_amount * corrected.accepted_quantity_base
                     / source.accepted_quantity_base,
                     tradeflow_currency_minor_scale(correction_invoice.currency)));
            IF correction_invoice.subtotal IS DISTINCT FROM aggregated_subtotal
               OR correction_invoice.discount_total IS DISTINCT FROM aggregated_discount
               OR correction_invoice.tax_total IS DISTINCT FROM aggregated_tax
               OR correction_invoice.grand_total IS DISTINCT FROM aggregated_total
               OR correction_line_count <> expected_replacement_line_count
               OR correction_line_count = 0 OR invalid_line_count <> 0 THEN
              RAISE EXCEPTION
                'Replacement Draft Invoice must exactly allocate its corrected source lines';
            END IF;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """CREATE CONSTRAINT TRIGGER trg_correction_draft_invoice_exact
        AFTER INSERT ON draft_invoices DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION validate_correction_draft_invoice()"""
    )
    op.execute(
        """CREATE CONSTRAINT TRIGGER trg_correction_draft_invoice_line_exact
        AFTER INSERT ON draft_invoice_lines DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION validate_correction_draft_invoice()"""
    )
    op.execute(
        """
        CREATE FUNCTION tradeflow_currency_minor_scale(currency_code varchar)
        RETURNS integer AS $$
        BEGIN
          IF currency_code IN ('BIF','CLP','DJF','GNF','ISK','JPY','KMF','KRW','PYG',
                               'RWF','UGX','VND','VUV','XAF','XOF','XPF') THEN
            RETURN 0;
          ELSIF currency_code IN ('BHD','IQD','JOD','KWD','LYD','OMR','TND') THEN
            RETURN 3;
          ELSIF currency_code IN ('CLF','UYW') THEN
            RETURN 4;
          END IF;
          RETURN 2;
        END;
        $$ LANGUAGE plpgsql IMMUTABLE STRICT
        """
    )
    op.execute(
        """
        CREATE FUNCTION validate_delivery_correction_completeness() RETURNS trigger AS $$
        DECLARE target_correction_id uuid;
        DECLARE correction_record record;
        DECLARE line_count integer;
        DECLARE expected_line_count integer;
        DECLARE evidence_count integer;
        DECLARE invalid_evidence_count integer;
        DECLARE expected_inventory numeric(24,6);
        DECLARE replacement_invoice_total numeric(24,6);
        DECLARE expected_draft_invoice numeric(24,6);
        DECLARE root_invoice_id uuid;
        DECLARE next_invoice_id uuid;
        BEGIN
          target_correction_id := NEW.correction_id;
          SELECT * INTO correction_record
          FROM delivery_corrections WHERE correction_id = target_correction_id;
          IF NOT FOUND THEN RETURN NEW; END IF;
          root_invoice_id := correction_record.original_draft_invoice_id;
          LOOP
            SELECT replaces_draft_invoice_id INTO next_invoice_id
            FROM draft_invoices WHERE draft_invoice_id = root_invoice_id;
            EXIT WHEN next_invoice_id IS NULL;
            root_invoice_id := next_invoice_id;
          END LOOP;
          SELECT count(*) INTO line_count FROM delivery_correction_lines
            WHERE correction_id = target_correction_id;
          SELECT count(*) INTO expected_line_count FROM delivery_confirmation_lines
            WHERE confirmation_id = correction_record.confirmation_id;
          SELECT count(*), count(*) FILTER (
              WHERE evidence.status <> 'verified'
                 OR evidence.delivery_id <> correction_record.delivery_id)
            INTO evidence_count, invalid_evidence_count
          FROM delivery_correction_evidence link
          JOIN delivery_evidence evidence ON evidence.evidence_id = link.evidence_id
          WHERE link.correction_id = target_correction_id;
          SELECT abs(coalesce(sum(round((
                   coalesce(prior.accepted_quantity_base,
                            source.accepted_quantity_base)
                   - corrected.accepted_quantity_base)
                 * source.unit_cost, 6)), 0))
            INTO expected_inventory
          FROM delivery_correction_lines corrected
          JOIN delivery_confirmation_lines source
            ON source.confirmation_line_id = corrected.confirmation_line_id
          JOIN delivery_corrections current_correction
            ON current_correction.correction_id = corrected.correction_id
          JOIN delivery_receipts source_receipt
            ON source_receipt.delivery_receipt_id =
               current_correction.original_delivery_receipt_id
          LEFT JOIN delivery_correction_lines prior
            ON prior.correction_id = source_receipt.correction_id
           AND prior.delivery_line_id = corrected.delivery_line_id
          WHERE corrected.correction_id = target_correction_id;
          SELECT coalesce(sum(
                   round(invoice_line.subtotal *
                     coalesce(corrected.accepted_quantity_base, 0)
                     / invoice_line.accepted_quantity_base,
                     tradeflow_currency_minor_scale(source_invoice.currency))
                   - round(invoice_line.discount_amount *
                     coalesce(corrected.accepted_quantity_base, 0)
                     / invoice_line.accepted_quantity_base,
                     tradeflow_currency_minor_scale(source_invoice.currency))
                   + round(invoice_line.tax_amount *
                     coalesce(corrected.accepted_quantity_base, 0)
                     / invoice_line.accepted_quantity_base,
                     tradeflow_currency_minor_scale(source_invoice.currency))), 0)
            INTO replacement_invoice_total
          FROM draft_invoice_lines invoice_line
          JOIN draft_invoices source_invoice
            ON source_invoice.draft_invoice_id = invoice_line.draft_invoice_id
          LEFT JOIN (
            SELECT line_id, sum(accepted_quantity_base) AS accepted_quantity_base
            FROM delivery_correction_lines
            WHERE correction_id = target_correction_id GROUP BY line_id
          ) corrected ON corrected.line_id = invoice_line.line_id
          WHERE invoice_line.draft_invoice_id = root_invoice_id;
          SELECT round(abs(predecessor.grand_total - replacement_invoice_total),
                       tradeflow_currency_minor_scale(predecessor.currency))
            INTO expected_draft_invoice
          FROM draft_invoices predecessor
          WHERE predecessor.draft_invoice_id = correction_record.original_draft_invoice_id;
          IF correction_record.sealed_at IS NULL
             OR line_count <> expected_line_count
             OR evidence_count = 0 OR invalid_evidence_count <> 0 THEN
            RAISE EXCEPTION
              'Delivery Correction requires lines and verified source Delivery evidence';
          END IF;
          IF correction_record.affected_inventory_value <> expected_inventory
             OR correction_record.affected_draft_invoice_value <> expected_draft_invoice
             OR correction_record.affected_value_base_currency <>
                greatest(expected_inventory, expected_draft_invoice) THEN
            RAISE EXCEPTION 'Delivery Correction affected value does not match source economics';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in (
        "delivery_corrections",
        "delivery_correction_lines",
        "delivery_correction_evidence",
    ):
        op.execute(
            f"""CREATE CONSTRAINT TRIGGER trg_{table}_complete
            AFTER INSERT ON {table} DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION validate_delivery_correction_completeness()"""
        )
    op.execute(
        """
        CREATE FUNCTION validate_delivery_correction_authorization() RETURNS trigger AS $$
        DECLARE correction_record record;
        DECLARE authority_record record;
        DECLARE incomplete_effect_lines integer;
        BEGIN
          SELECT * INTO correction_record FROM delivery_corrections
            WHERE correction_id = NEW.correction_id;
          SELECT * INTO authority_record FROM approval_authorities
            WHERE approval_authority_id = NEW.approval_authority_id;
          SELECT count(*) INTO incomplete_effect_lines
          FROM delivery_correction_lines line
          JOIN delivery_confirmation_lines confirmation_line
            ON confirmation_line.confirmation_line_id = line.confirmation_line_id
          JOIN delivery_receipts source_receipt
            ON source_receipt.delivery_receipt_id =
               correction_record.original_delivery_receipt_id
          LEFT JOIN delivery_correction_lines prior_line
            ON prior_line.correction_id = source_receipt.correction_id
           AND prior_line.delivery_line_id = line.delivery_line_id
          WHERE line.correction_id = NEW.correction_id
            AND (
              (SELECT count(*) FROM delivery_correction_movement_effects effect
                WHERE effect.correction_id = NEW.correction_id
                  AND effect.correction_line_id = line.correction_line_id
                  AND effect.effect_role = 'original'
                  AND effect.outcome = 'accepted') <>
                CASE WHEN coalesce(prior_line.accepted_quantity_base,
                     confirmation_line.accepted_quantity_base) > 0 THEN 1 ELSE 0 END
              OR (SELECT count(*) FROM delivery_correction_movement_effects effect
                WHERE effect.correction_id = NEW.correction_id
                  AND effect.correction_line_id = line.correction_line_id
                  AND effect.effect_role = 'reversal'
                  AND effect.outcome = 'accepted') <>
                CASE WHEN coalesce(prior_line.accepted_quantity_base,
                     confirmation_line.accepted_quantity_base) > 0 THEN 1 ELSE 0 END
              OR (SELECT count(*) FROM delivery_correction_movement_effects effect
                WHERE effect.correction_id = NEW.correction_id
                  AND effect.correction_line_id = line.correction_line_id
                  AND effect.effect_role = 'replacement'
                  AND effect.outcome = 'accepted') <>
                CASE WHEN line.accepted_quantity_base > 0 THEN 1 ELSE 0 END
              OR (SELECT count(*) FROM delivery_correction_movement_effects effect
                WHERE effect.correction_id = NEW.correction_id
                  AND effect.correction_line_id = line.correction_line_id
                  AND effect.effect_role = 'original'
                  AND effect.outcome = 'short_missing') <>
                CASE WHEN coalesce(prior_line.short_missing_quantity_base,
                     confirmation_line.short_missing_quantity_base) > 0 THEN 2 ELSE 0 END
              OR (SELECT count(*) FROM delivery_correction_movement_effects effect
                WHERE effect.correction_id = NEW.correction_id
                  AND effect.correction_line_id = line.correction_line_id
                  AND effect.effect_role = 'reversal'
                  AND effect.outcome = 'short_missing') <>
                CASE WHEN coalesce(prior_line.short_missing_quantity_base,
                     confirmation_line.short_missing_quantity_base) > 0 THEN 2 ELSE 0 END
              OR (SELECT count(*) FROM delivery_correction_movement_effects effect
                WHERE effect.correction_id = NEW.correction_id
                  AND effect.correction_line_id = line.correction_line_id
                  AND effect.effect_role = 'replacement'
                  AND effect.outcome = 'short_missing') <>
                CASE WHEN line.short_missing_quantity_base > 0 THEN 2 ELSE 0 END
            );
          IF NOT FOUND
             OR correction_record.sealed_at IS NULL
             OR NEW.authorized_by = correction_record.requested_by
             OR authority_record.user_subject IS DISTINCT FROM NEW.authorized_by
             OR authority_record.capability_code IS DISTINCT FROM
                'fulfillment:delivery-correction-authorize'
             OR authority_record.branch_id IS DISTINCT FROM correction_record.branch_id
             OR (authority_record.maximum_amount IS NOT NULL AND
                 authority_record.maximum_amount <
                   correction_record.affected_value_base_currency)
             OR incomplete_effect_lines <> 0 THEN
            RAISE EXCEPTION 'Delivery Correction authorization violates maker-checker authority';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """CREATE CONSTRAINT TRIGGER trg_delivery_correction_authorization_valid
        AFTER INSERT ON delivery_correction_authorizations DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION validate_delivery_correction_authorization()"""
    )
    op.execute(
        """
        CREATE FUNCTION validate_document_series_audit() RETURNS trigger AS $$
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
        """CREATE CONSTRAINT TRIGGER trg_document_series_audit_complete
        AFTER INSERT OR UPDATE OF next_number ON document_series
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION validate_document_series_audit()"""
    )
    op.execute(
        """
        CREATE FUNCTION reject_sealed_delivery_correction_append() RETURNS trigger AS $$
        DECLARE target_correction_id uuid;
        DECLARE sealed timestamptz;
        BEGIN
          IF TG_TABLE_NAME = 'delivery_correction_identity_positions' THEN
            SELECT correction_id INTO target_correction_id FROM delivery_correction_lines
              WHERE correction_line_id = NEW.correction_line_id;
          ELSE
            target_correction_id := NEW.correction_id;
          END IF;
          IF TG_TABLE_NAME = 'delivery_correction_movement_effects' THEN
            IF EXISTS (SELECT 1 FROM delivery_correction_authorizations
                       WHERE correction_id = target_correction_id) THEN
              RAISE EXCEPTION 'Posted Delivery Correction effects are sealed';
            END IF;
          ELSE
            SELECT sealed_at INTO sealed FROM delivery_corrections
              WHERE correction_id = target_correction_id;
            IF sealed IS NOT NULL THEN
              RAISE EXCEPTION 'Delivery Correction proposal is sealed';
            END IF;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in (
        "delivery_correction_lines",
        "delivery_correction_identity_positions",
        "delivery_correction_evidence",
        "delivery_correction_movement_effects",
    ):
        op.execute(
            f"""CREATE TRIGGER trg_{table}_append_guard BEFORE INSERT ON {table}
            FOR EACH ROW EXECUTE FUNCTION reject_sealed_delivery_correction_append()"""
        )
    op.execute(
        """
        CREATE FUNCTION reject_delivery_correction_mutation() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'UPDATE' AND OLD.sealed_at IS NULL AND NEW.sealed_at IS NOT NULL
             AND (to_jsonb(OLD) - 'sealed_at') = (to_jsonb(NEW) - 'sealed_at') THEN
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'Delivery Correction history is immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """CREATE TRIGGER delivery_corrections_immutable
        BEFORE UPDATE OR DELETE ON delivery_corrections
        FOR EACH ROW EXECUTE FUNCTION reject_delivery_correction_mutation()"""
    )
    op.execute(
        """
        CREATE FUNCTION reject_immutable_delivery_correction_history() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'Delivery Correction history is immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    immutable_tables = {
        "delivery_correction_lines": "delivery_correction_lines_immutable",
        "delivery_correction_identity_positions": "delivery_correction_identities_immutable",
        "delivery_correction_evidence": "delivery_correction_evidence_immutable",
        "delivery_correction_authorizations": "delivery_correction_authorizations_immutable",
        "delivery_correction_movement_effects": "delivery_correction_effects_immutable",
    }
    for table, trigger in immutable_tables.items():
        op.execute(
            f"""CREATE TRIGGER {trigger} BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION reject_immutable_delivery_correction_history()"""
        )


def downgrade() -> None:
    op.execute(
        """DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM delivery_corrections) THEN
          RAISE EXCEPTION
            'Cannot downgrade 0015 while immutable Delivery Correction history exists';
        END IF;
        END $$"""
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_correction_draft_invoice_line_exact ON draft_invoice_lines"
    )
    op.execute("DROP TRIGGER IF EXISTS trg_correction_draft_invoice_exact ON draft_invoices")
    op.execute("DROP FUNCTION IF EXISTS validate_correction_draft_invoice()")
    op.execute("DROP TRIGGER IF EXISTS trg_draft_invoice_line_kind ON draft_invoice_lines")
    op.execute("DROP FUNCTION IF EXISTS validate_draft_invoice_line_kind()")
    op.execute(
        """DROP TRIGGER IF EXISTS delivery_correction_authorizations_immutable
        ON delivery_correction_authorizations"""
    )
    op.execute(
        """DROP TRIGGER IF EXISTS delivery_correction_effects_immutable
        ON delivery_correction_movement_effects"""
    )
    op.execute(
        """DROP TRIGGER IF EXISTS delivery_correction_evidence_immutable
        ON delivery_correction_evidence"""
    )
    op.execute(
        """DROP TRIGGER IF EXISTS delivery_correction_identities_immutable
        ON delivery_correction_identity_positions"""
    )
    op.execute(
        "DROP TRIGGER IF EXISTS delivery_correction_lines_immutable ON delivery_correction_lines"
    )
    op.execute("DROP TRIGGER IF EXISTS delivery_corrections_immutable ON delivery_corrections")
    op.execute("DROP FUNCTION IF EXISTS reject_delivery_correction_mutation()")
    op.execute("DROP FUNCTION IF EXISTS reject_immutable_delivery_correction_history()")
    for table in (
        "delivery_correction_movement_effects",
        "delivery_correction_evidence",
        "delivery_correction_identity_positions",
        "delivery_correction_lines",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_append_guard ON {table}")
    op.execute("DROP FUNCTION IF EXISTS reject_sealed_delivery_correction_append()")
    op.execute("DROP TRIGGER IF EXISTS trg_document_series_audit_complete ON document_series")
    op.execute("DROP FUNCTION IF EXISTS validate_document_series_audit()")
    op.execute(
        """DROP TRIGGER IF EXISTS trg_delivery_correction_identity_exact
        ON delivery_correction_identity_positions"""
    )
    op.execute(
        """DROP TRIGGER IF EXISTS trg_delivery_correction_line_identity_exact
        ON delivery_correction_lines"""
    )
    op.execute("DROP FUNCTION IF EXISTS validate_delivery_correction_identity()")
    op.execute(
        """DROP TRIGGER IF EXISTS trg_correction_movement_allocation_exact
        ON stock_movement_identity_allocations"""
    )
    op.execute(
        """DROP TRIGGER IF EXISTS trg_correction_movement_identity_exact
        ON stock_movements"""
    )
    op.execute("DROP FUNCTION IF EXISTS validate_delivery_correction_movement_identity()")
    op.execute(
        """DROP TRIGGER IF EXISTS trg_correction_movement_economics_exact
        ON stock_movements"""
    )
    op.execute(
        """DROP TRIGGER IF EXISTS trg_correction_movement_effect_exact
        ON delivery_correction_movement_effects"""
    )
    op.execute("DROP FUNCTION IF EXISTS validate_delivery_correction_movement_effect()")
    op.execute(
        """DROP FUNCTION IF EXISTS correction_movement_is_immediate_source(
        uuid,uuid,varchar,uuid)"""
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_delivery_correction_line_exact ON delivery_correction_lines"
    )
    op.execute("DROP FUNCTION IF EXISTS validate_delivery_correction_line()")
    op.execute(
        """DROP TRIGGER IF EXISTS trg_delivery_correction_authorization_valid
        ON delivery_correction_authorizations"""
    )
    op.execute("DROP FUNCTION IF EXISTS validate_delivery_correction_authorization()")
    for table in (
        "delivery_correction_evidence",
        "delivery_correction_lines",
        "delivery_corrections",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_complete ON {table}")
    op.execute("DROP FUNCTION IF EXISTS validate_delivery_correction_completeness()")
    op.execute("DROP FUNCTION IF EXISTS tradeflow_currency_minor_scale(varchar)")
    op.execute(
        "ALTER TABLE delivery_exception_events DROP CONSTRAINT ck_delivery_exception_event_type"
    )
    op.execute(
        """ALTER TABLE delivery_exception_events
        ADD CONSTRAINT delivery_exception_events_event_type_check CHECK (event_type IN
        ('opened','return_received','retry_allocated','recovered',
        'carrier_claim_resolved','inventory_adjustment_resolved'))"""
    )
    op.execute("DROP INDEX uq_delivery_exception_correction_case_kind")
    op.execute("DROP INDEX uq_delivery_exception_original_case_kind")
    op.execute("ALTER TABLE delivery_exception_cases DROP COLUMN correction_line_id")
    op.execute(
        """ALTER TABLE delivery_exception_cases
        ADD CONSTRAINT uq_delivery_exception_case_kind
        UNIQUE(confirmation_line_id,exception_kind)"""
    )
    op.execute("DROP TABLE delivery_correction_movement_effects")
    op.execute("DROP TABLE delivery_correction_authorizations")
    op.execute("DROP TABLE delivery_correction_evidence")
    op.execute("DROP TABLE delivery_correction_identity_positions")
    op.execute("DROP TABLE delivery_correction_lines")
    op.execute(
        """ALTER TABLE draft_invoice_lines
        DROP CONSTRAINT IF EXISTS ck_draft_invoice_line_signed_values,
        DROP CONSTRAINT IF EXISTS ck_draft_invoice_line_kind,
        DROP COLUMN IF EXISTS invoice_kind"""
    )
    op.execute(
        "ALTER TABLE draft_invoice_lines DROP CONSTRAINT IF EXISTS ck_draft_invoice_line_values"
    )
    op.execute(
        """ALTER TABLE draft_invoice_lines
        ADD CONSTRAINT ck_draft_invoice_line_values CHECK (
        accepted_quantity_base > 0 AND unit_price >= 0 AND subtotal >= 0
        AND discount_amount >= 0 AND tax_amount >= 0 AND line_total >= 0)"""
    )
    op.execute(
        """ALTER TABLE draft_invoices
        DROP CONSTRAINT draft_invoices_correction_id_fkey,
        DROP CONSTRAINT draft_invoice_source_shape"""
    )
    op.execute(
        """ALTER TABLE delivery_receipts
        DROP CONSTRAINT delivery_receipts_correction_id_fkey,
        DROP CONSTRAINT delivery_receipt_correction_shape"""
    )
    op.execute("DROP TABLE delivery_corrections")
    op.execute("DROP INDEX uq_original_draft_invoice_confirmation")
    op.execute(
        """ALTER TABLE draft_invoices
        DROP CONSTRAINT uq_draft_invoice_event_kind,
        DROP CONSTRAINT ck_draft_invoice_kind,
        DROP CONSTRAINT IF EXISTS ck_draft_invoice_signed_totals"""
    )
    op.execute(
        """ALTER TABLE draft_invoices
        DROP COLUMN replaces_draft_invoice_id,
        DROP COLUMN reversal_of_draft_invoice_id,
        DROP COLUMN correction_id,
        DROP COLUMN invoice_kind"""
    )
    op.execute("ALTER TABLE draft_invoices DROP CONSTRAINT IF EXISTS ck_draft_invoice_totals")
    op.execute(
        """ALTER TABLE draft_invoices
        ADD CONSTRAINT draft_invoices_delivery_confirmation_id_key
          UNIQUE(delivery_confirmation_id),
        ADD CONSTRAINT draft_invoices_source_event_id_key UNIQUE(source_event_id),
        ADD CONSTRAINT ck_draft_invoice_totals CHECK (
          subtotal >= 0 AND discount_total >= 0
          AND tax_total >= 0 AND grand_total >= 0)"""
    )
    op.execute("DROP INDEX delivery_receipts_correction_id_key")
    op.execute("DROP INDEX uq_original_delivery_receipt_confirmation")
    op.execute(
        """ALTER TABLE delivery_receipts
        DROP COLUMN corrects_delivery_receipt_id,
        DROP COLUMN correction_id"""
    )
    op.execute(
        """ALTER TABLE delivery_receipts
        ADD CONSTRAINT delivery_receipts_confirmation_id_key UNIQUE(confirmation_id)"""
    )
    op.execute(
        """ALTER TABLE stock_movements
        DROP CONSTRAINT ck_stock_movements_type,
        DROP CONSTRAINT ck_stock_movements_leg"""
    )
    op.execute("ALTER TABLE stock_movements ALTER COLUMN movement_leg TYPE varchar(40)")
    op.execute(
        """ALTER TABLE stock_movements ADD CONSTRAINT ck_stock_movements_type CHECK (
        movement_type IN ('opening_stock','pick','pick_reversal','dispatch',
        'delivery_confirmation','delivery_exception','return_to_warehouse',
        'investigation_resolution'))"""
    )
    op.execute(
        """ALTER TABLE stock_movements ADD CONSTRAINT ck_stock_movements_leg CHECK (
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
           'carrier_claim_investigation_out','inventory_adjustment_investigation_out')))"""
    )
