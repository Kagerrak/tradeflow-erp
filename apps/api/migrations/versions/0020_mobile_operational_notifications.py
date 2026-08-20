"""mobile operational notifications and deep links

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-20 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0020"
down_revision: str | None = "d62caac1e324"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE device_registrations (
          device_registration_id UUID PRIMARY KEY,
          user_subject VARCHAR(200) NOT NULL REFERENCES users(subject),
          device_token VARCHAR(500) NOT NULL,
          platform VARCHAR(20) NOT NULL,
          app_version VARCHAR(50),
          locale VARCHAR(10) NOT NULL DEFAULT 'en',
          is_active BOOLEAN NOT NULL DEFAULT true,
          expires_at TIMESTAMPTZ NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT ck_device_registrations_platform
            CHECK (platform IN ('ios', 'android', 'web')),
          CONSTRAINT ck_device_registrations_expires_after_created
            CHECK (expires_at > created_at),
          CONSTRAINT uq_device_registration_user_token
            UNIQUE (user_subject, device_token, platform)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_device_registrations_user_active
          ON device_registrations(user_subject, is_active)
        """
    )

    op.execute(
        """
        CREATE TABLE notification_preferences (
          preference_id UUID PRIMARY KEY,
          user_subject VARCHAR(200) NOT NULL REFERENCES users(subject),
          category VARCHAR(50) NOT NULL,
          push_enabled BOOLEAN NOT NULL DEFAULT true,
          inbox_enabled BOOLEAN NOT NULL DEFAULT true,
          quiet_hours_start VARCHAR(5),
          quiet_hours_end VARCHAR(5),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT ck_notification_preferences_category
            CHECK (category IN (
              'delivery_assignment', 'delivery_confirmation', 'delivery_correction',
              'approval_required', 'payment_received', 'payment_rejected'
            )),
          CONSTRAINT ck_notification_preferences_quiet_start
            CHECK (
              quiet_hours_start IS NULL
              OR quiet_hours_start ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'
            ),
          CONSTRAINT ck_notification_preferences_quiet_end
            CHECK (
              quiet_hours_end IS NULL
              OR quiet_hours_end ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'
            ),
          CONSTRAINT uq_notification_preference_user_category
            UNIQUE (user_subject, category)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE operational_notifications (
          notification_id UUID PRIMARY KEY,
          source_event_id UUID REFERENCES outbox_events(outbox_event_id),
          source_type VARCHAR(50) NOT NULL,
          source_id UUID NOT NULL,
          recipient_subject VARCHAR(200) NOT NULL REFERENCES users(subject),
          notification_type VARCHAR(50) NOT NULL,
          title VARCHAR(200) NOT NULL,
          body VARCHAR(1000) NOT NULL,
          deep_link_path VARCHAR(200) NOT NULL,
          deep_link_token VARCHAR(200) NOT NULL,
          branch_id UUID NOT NULL REFERENCES branches(branch_id),
          warehouse_id UUID REFERENCES warehouses(warehouse_id),
          required_capability VARCHAR(100),
          status VARCHAR(20) NOT NULL DEFAULT 'pending',
          correlation_id VARCHAR(100) NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          read_at TIMESTAMPTZ,
          revoked_at TIMESTAMPTZ,
          CONSTRAINT ck_operational_notifications_status
            CHECK (status IN ('pending', 'delivered', 'read', 'revoked')),
          CONSTRAINT ck_operational_notifications_source_event_consistency
            CHECK (source_event_id IS NOT NULL OR source_type <> 'outbox_event'),
          CONSTRAINT uq_operational_notification_identity
            UNIQUE (source_event_id, recipient_subject, notification_type),
          CONSTRAINT uq_operational_notification_source_identity
            UNIQUE (source_type, source_id, recipient_subject, notification_type)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_operational_notifications_recipient_status
          ON operational_notifications(recipient_subject, status)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_operational_notifications_recipient_created
          ON operational_notifications(recipient_subject, created_at)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_operational_notifications_source_event
          ON operational_notifications(source_event_id)
        """
    )

    op.execute(
        """
        CREATE TABLE notification_deliveries (
          delivery_id UUID PRIMARY KEY,
          notification_id UUID NOT NULL
            REFERENCES operational_notifications(notification_id),
          device_registration_id UUID NOT NULL
            REFERENCES device_registrations(device_registration_id),
          provider VARCHAR(50) NOT NULL DEFAULT 'noop',
          provider_message_id VARCHAR(200),
          status VARCHAR(20) NOT NULL DEFAULT 'pending',
          attempted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          delivered_at TIMESTAMPTZ,
          last_error VARCHAR(2000),
          CONSTRAINT ck_notification_deliveries_status
            CHECK (status IN ('pending', 'sent', 'failed', 'delivered')),
          CONSTRAINT ck_notification_deliveries_delivered_shape
            CHECK (
              (status = 'delivered' AND delivered_at IS NOT NULL)
              OR (status <> 'delivered' AND delivered_at IS NULL)
            ),
          CONSTRAINT uq_notification_delivery_identity
            UNIQUE (notification_id, device_registration_id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_notification_deliveries_notification
          ON notification_deliveries(notification_id)
        """
    )

    op.execute(
        """
        CREATE TABLE notification_read_events (
          read_event_id UUID PRIMARY KEY,
          notification_id UUID NOT NULL REFERENCES operational_notifications(notification_id),
          device_registration_id UUID REFERENCES device_registrations(device_registration_id),
          read_by VARCHAR(200) NOT NULL REFERENCES users(subject),
          read_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT uq_notification_read_identity
            UNIQUE (notification_id, read_event_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE notification_effect_events (
          effect_event_id UUID PRIMARY KEY,
          notification_id UUID NOT NULL REFERENCES operational_notifications(notification_id),
          effect_type VARCHAR(20) NOT NULL,
          source_type VARCHAR(50) NOT NULL,
          source_id UUID NOT NULL,
          device_registration_id UUID REFERENCES device_registrations(device_registration_id),
          payload JSONB NOT NULL DEFAULT '{}',
          occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT ck_notification_effect_events_type
            CHECK (effect_type IN ('created', 'delivered', 'read', 'revoked', 'failed', 'masked')),
          CONSTRAINT uq_notification_effect_identity
            UNIQUE (notification_id, effect_type, source_type, source_id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_notification_effect_events_notification
          ON notification_effect_events(notification_id)
        """
    )

    op.execute(
        """
        INSERT INTO capabilities (code) VALUES
          ('notification:read'),
          ('notification:manage')
        ON CONFLICT (code) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS notification_effect_events")
    op.execute("DROP TABLE IF EXISTS notification_read_events")
    op.execute("DROP TABLE IF EXISTS notification_deliveries")
    op.execute("DROP TABLE IF EXISTS operational_notifications")
    op.execute("DROP TABLE IF EXISTS notification_preferences")
    op.execute("DROP TABLE IF EXISTS device_registrations")
    op.execute(
        """
        DELETE FROM role_template_capabilities
        WHERE capability_code IN ('notification:read', 'notification:manage')
        """
    )
    op.execute(
        """
        DELETE FROM capabilities
        WHERE code IN ('notification:read', 'notification:manage')
        """
    )
