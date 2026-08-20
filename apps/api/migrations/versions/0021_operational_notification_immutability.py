"""operational notification immutability and deep link guards

Revision ID: 0021
Revises: d62caac1e324
Create Date: 2026-08-20 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0021"
down_revision: str | Sequence[str] | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_operational_notifications_immutable "
        "ON operational_notifications"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION protect_operational_notifications()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Operational Notifications cannot be deleted';
          END IF;
          IF NEW.notification_id IS DISTINCT FROM OLD.notification_id
             OR NEW.source_event_id IS DISTINCT FROM OLD.source_event_id
             OR NEW.source_type IS DISTINCT FROM OLD.source_type
             OR NEW.source_id IS DISTINCT FROM OLD.source_id
             OR NEW.recipient_subject IS DISTINCT FROM OLD.recipient_subject
             OR NEW.notification_type IS DISTINCT FROM OLD.notification_type
             OR NEW.title IS DISTINCT FROM OLD.title
             OR NEW.body IS DISTINCT FROM OLD.body
             OR NEW.deep_link_path IS DISTINCT FROM OLD.deep_link_path
             OR NEW.deep_link_token IS DISTINCT FROM OLD.deep_link_token
             OR NEW.branch_id IS DISTINCT FROM OLD.branch_id
             OR NEW.warehouse_id IS DISTINCT FROM OLD.warehouse_id
             OR NEW.required_capability IS DISTINCT FROM OLD.required_capability
             OR NEW.correlation_id IS DISTINCT FROM OLD.correlation_id
             OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION 'Operational Notification identity fields are immutable';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_operational_notifications_immutable
        BEFORE UPDATE OR DELETE ON operational_notifications
        FOR EACH ROW EXECUTE FUNCTION protect_operational_notifications()
        """
    )

    op.execute(
        "DROP TRIGGER IF EXISTS trg_notification_deliveries_immutable ON notification_deliveries"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION protect_notification_deliveries()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Notification Deliveries cannot be deleted';
          END IF;
          IF NEW.delivery_id IS DISTINCT FROM OLD.delivery_id
             OR NEW.notification_id IS DISTINCT FROM OLD.notification_id
             OR NEW.device_registration_id IS DISTINCT FROM OLD.device_registration_id
             OR NEW.provider IS DISTINCT FROM OLD.provider
             OR NEW.attempted_at IS DISTINCT FROM OLD.attempted_at THEN
            RAISE EXCEPTION 'Notification Delivery identity fields are immutable';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_notification_deliveries_immutable
        BEFORE UPDATE OR DELETE ON notification_deliveries
        FOR EACH ROW EXECUTE FUNCTION protect_notification_deliveries()
        """
    )

    op.execute(
        "DROP TRIGGER IF EXISTS trg_notification_read_events_immutable ON notification_read_events"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION protect_notification_read_events()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Notification Read Events cannot be deleted';
          END IF;
          RAISE EXCEPTION 'Notification Read Events are insert-only';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_notification_read_events_immutable
        BEFORE UPDATE OR DELETE ON notification_read_events
        FOR EACH ROW EXECUTE FUNCTION protect_notification_read_events()
        """
    )

    op.execute(
        "DROP TRIGGER IF EXISTS trg_notification_effect_events_immutable "
        "ON notification_effect_events"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION protect_notification_effect_events()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Notification Effect Events cannot be deleted';
          END IF;
          RAISE EXCEPTION 'Notification Effect Events are insert-only';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_notification_effect_events_immutable
        BEFORE UPDATE OR DELETE ON notification_effect_events
        FOR EACH ROW EXECUTE FUNCTION protect_notification_effect_events()
        """
    )

    op.execute("DROP TRIGGER IF EXISTS trg_device_registrations_immutable ON device_registrations")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION protect_device_registrations()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Device Registrations cannot be deleted';
          END IF;
          IF NEW.device_registration_id IS DISTINCT FROM OLD.device_registration_id
             OR NEW.user_subject IS DISTINCT FROM OLD.user_subject
             OR NEW.device_token IS DISTINCT FROM OLD.device_token
             OR NEW.platform IS DISTINCT FROM OLD.platform
             OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION 'Device Registration identity fields are immutable';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_device_registrations_immutable
        BEFORE UPDATE OR DELETE ON device_registrations
        FOR EACH ROW EXECUTE FUNCTION protect_device_registrations()
        """
    )

    op.execute(
        "DROP TRIGGER IF EXISTS trg_notification_preferences_immutable ON notification_preferences"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION protect_notification_preferences()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Notification Preferences cannot be deleted';
          END IF;
          IF NEW.preference_id IS DISTINCT FROM OLD.preference_id
             OR NEW.user_subject IS DISTINCT FROM OLD.user_subject
             OR NEW.category IS DISTINCT FROM OLD.category
             OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION 'Notification Preference identity fields are immutable';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_notification_preferences_immutable
        BEFORE UPDATE OR DELETE ON notification_preferences
        FOR EACH ROW EXECUTE FUNCTION protect_notification_preferences()
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_notification_read_events_notification
          ON notification_read_events(notification_id)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_notification_read_events_notification")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_notification_preferences_immutable ON notification_preferences"
    )
    op.execute("DROP FUNCTION IF EXISTS protect_notification_preferences()")
    op.execute("DROP TRIGGER IF EXISTS trg_device_registrations_immutable ON device_registrations")
    op.execute("DROP FUNCTION IF EXISTS protect_device_registrations()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_notification_effect_events_immutable "
        "ON notification_effect_events"
    )
    op.execute("DROP FUNCTION IF EXISTS protect_notification_effect_events()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_notification_read_events_immutable ON notification_read_events"
    )
    op.execute("DROP FUNCTION IF EXISTS protect_notification_read_events()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_notification_deliveries_immutable ON notification_deliveries"
    )
    op.execute("DROP FUNCTION IF EXISTS protect_notification_deliveries()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_operational_notifications_immutable "
        "ON operational_notifications"
    )
    op.execute("DROP FUNCTION IF EXISTS protect_operational_notifications()")
