"""return request evidence and offline sync

Revision ID: 5db106ff1092
Revises: e93736a741bd
Create Date: 2026-08-28 23:44:09.016519
"""

from collections.abc import Sequence

from alembic import op

revision: str = "5db106ff1092"
down_revision: str | None = "e93736a741bd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO capabilities(code)
        VALUES ('returns:evidence-capture'), ('returns:evidence-read')
        ON CONFLICT (code) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO role_template_capabilities(role_template_id, capability_code)
        SELECT template.role_template_id, capability.code
        FROM role_templates template
        CROSS JOIN (VALUES ('returns:evidence-capture'), ('returns:evidence-read')) capability(code)
        WHERE template.code = 'WAREHOUSE_SUPERVISOR'
        ON CONFLICT DO NOTHING
        """
    )
    op.execute(
        """
        CREATE TABLE return_request_evidence (
          evidence_id uuid PRIMARY KEY,
          return_request_id uuid NOT NULL REFERENCES return_requests(return_request_id),
          kind varchar(30) NOT NULL CHECK (kind IN ('photo', 'note')),
          object_key varchar(500) UNIQUE,
          content_type varchar(100),
          size_bytes integer,
          sha256 varchar(64),
          upload_id varchar(500),
          note_text varchar(2000),
          captured_by varchar(200) NOT NULL REFERENCES users(subject),
          device_captured_at timestamptz NOT NULL,
          status varchar(30) NOT NULL CHECK (status IN ('uploading', 'verified', 'rejected')),
          created_at timestamptz NOT NULL DEFAULT now(),
          verified_at timestamptz,
          sync_correlation_id varchar(100),
          CONSTRAINT uq_return_request_evidence_request UNIQUE(return_request_id, evidence_id),
          CONSTRAINT ck_return_request_evidence_fields_by_kind CHECK (
            (
              kind = 'photo'
              AND object_key IS NOT NULL
              AND content_type IS NOT NULL
              AND size_bytes IS NOT NULL
              AND sha256 IS NOT NULL
              AND note_text IS NULL
            )
            OR (
              kind = 'note'
              AND object_key IS NULL
              AND content_type IS NULL
              AND size_bytes IS NULL
              AND sha256 IS NULL
              AND note_text IS NOT NULL
            )
          )
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_return_request_evidence_request "
        "ON return_request_evidence(return_request_id)"
    )
    op.execute(
        "CREATE INDEX ix_return_request_evidence_captured_by "
        "ON return_request_evidence(captured_by)"
    )
    op.execute(
        """
        CREATE TABLE return_request_evidence_sync_state (
          return_request_id uuid PRIMARY KEY REFERENCES return_requests(return_request_id),
          expected_version integer NOT NULL CHECK (expected_version > 0),
          acknowledged_at timestamptz,
          conflict_detected_at timestamptz,
          conflict_reason varchar(200),
          correlation_id varchar(100) NOT NULL,
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_return_evidence_sync_state_mutual_exclusion CHECK (
            (
              acknowledged_at IS NULL
              AND conflict_detected_at IS NOT NULL
              AND conflict_reason IS NOT NULL
            )
            OR (
              acknowledged_at IS NOT NULL
              AND conflict_detected_at IS NULL
              AND conflict_reason IS NULL
            )
            OR (
              acknowledged_at IS NULL
              AND conflict_detected_at IS NULL
              AND conflict_reason IS NULL
            )
          )
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS return_request_evidence_sync_state")
    op.execute("DROP TABLE IF EXISTS return_request_evidence")
    op.execute(
        """
        DELETE FROM role_template_capabilities
        WHERE capability_code IN ('returns:evidence-capture', 'returns:evidence-read')
        """
    )
    op.execute(
        """
        DELETE FROM capabilities
        WHERE code IN ('returns:evidence-capture', 'returns:evidence-read')
        """
    )
