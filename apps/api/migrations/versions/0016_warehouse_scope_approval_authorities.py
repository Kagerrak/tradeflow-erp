"""Add warehouse scope to approval authorities and enforce it for corrections.

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE approval_authorities
          ADD COLUMN warehouse_id uuid REFERENCES warehouses(warehouse_id)
        """
    )
    op.execute(
        """
        ALTER TABLE approval_authorities
          DROP CONSTRAINT uq_approval_authority_assignment
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_approval_authority_branch
          ON approval_authorities(user_subject, capability_code, branch_id)
          WHERE warehouse_id IS NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_approval_authority_warehouse
          ON approval_authorities(user_subject, capability_code, branch_id, warehouse_id)
          WHERE warehouse_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION validate_delivery_correction_authorization()
          RETURNS trigger AS $$
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
             OR (authority_record.warehouse_id IS NOT NULL AND
                 authority_record.warehouse_id IS DISTINCT FROM correction_record.warehouse_id)
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


def downgrade() -> None:
    op.execute(
        """DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM approval_authorities WHERE warehouse_id IS NOT NULL) THEN
          RAISE EXCEPTION
            'Cannot downgrade 0016 while warehouse-scoped Approval Authorities exist';
        END IF;
        END $$"""
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION validate_delivery_correction_authorization()
          RETURNS trigger AS $$
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
    op.execute("DROP INDEX IF EXISTS uq_approval_authority_warehouse")
    op.execute("DROP INDEX IF EXISTS uq_approval_authority_branch")
    op.execute(
        """
        ALTER TABLE approval_authorities
          ADD CONSTRAINT uq_approval_authority_assignment
            UNIQUE(user_subject, capability_code, branch_id)
        """
    )
    op.execute(
        """
        ALTER TABLE approval_authorities DROP COLUMN warehouse_id
        """
    )
