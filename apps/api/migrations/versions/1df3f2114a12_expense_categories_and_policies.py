"""expense_categories_and_policies

Revision ID: 1df3f2114a12
Revises: 0019
Create Date: 2026-08-19 23:38:23.543562
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1df3f2114a12"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE expense_categories (
          expense_category_version_id UUID PRIMARY KEY,
          company_id UUID NOT NULL REFERENCES companies(company_id),
          category_code VARCHAR(50) NOT NULL,
          version INTEGER NOT NULL,
          name VARCHAR(200) NOT NULL,
          description VARCHAR,
          allowed_evidence_types JSONB NOT NULL DEFAULT '[]',
          attribution_rules JSONB NOT NULL DEFAULT '{}',
          effective_from DATE NOT NULL,
          effective_to DATE,
          status VARCHAR(20) NOT NULL DEFAULT 'draft',
          created_by VARCHAR(200) NOT NULL REFERENCES users(subject),
          published_by VARCHAR(200) REFERENCES users(subject),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          published_at TIMESTAMPTZ,
          CONSTRAINT ck_expense_categories_version_positive CHECK (version > 0),
          CONSTRAINT ck_expense_categories_effective_range CHECK (
            effective_to IS NULL OR effective_to >= effective_from
          ),
          CONSTRAINT ck_expense_categories_status CHECK (status IN ('draft','published')),
          CONSTRAINT ck_expense_categories_published_shape CHECK (
            (status = 'published') OR (published_by IS NULL AND published_at IS NULL)
          ),
          CONSTRAINT uq_expense_categories_company_code_version
            UNIQUE (company_id, category_code, version)
        )
        """
    )

    op.execute(
        """
        CREATE UNIQUE INDEX uq_expense_categories_active_code
          ON expense_categories(company_id, category_code)
          WHERE status = 'published'
        """
    )

    op.execute(
        """
        CREATE TABLE expense_policies (
          expense_policy_version_id UUID PRIMARY KEY,
          company_id UUID NOT NULL REFERENCES companies(company_id),
          branch_id UUID REFERENCES branches(branch_id),
          policy_code VARCHAR(50) NOT NULL,
          version INTEGER NOT NULL,
          name VARCHAR(200) NOT NULL,
          description VARCHAR,
          category_version_id UUID NOT NULL
            REFERENCES expense_categories(expense_category_version_id),
          category_code VARCHAR(50) NOT NULL,
          max_amount NUMERIC(18,2),
          currencies JSONB NOT NULL DEFAULT '[]',
          requires_receipt BOOLEAN NOT NULL DEFAULT true,
          allowed_evidence_types JSONB NOT NULL DEFAULT '[]',
          attribution_rules JSONB NOT NULL DEFAULT '{}',
          effective_from DATE NOT NULL,
          effective_to DATE,
          status VARCHAR(20) NOT NULL DEFAULT 'draft',
          created_by VARCHAR(200) NOT NULL REFERENCES users(subject),
          published_by VARCHAR(200) REFERENCES users(subject),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          published_at TIMESTAMPTZ,
          CONSTRAINT ck_expense_policies_version_positive CHECK (version > 0),
          CONSTRAINT ck_expense_policies_effective_range CHECK (
            effective_to IS NULL OR effective_to >= effective_from
          ),
          CONSTRAINT ck_expense_policies_status CHECK (status IN ('draft','published')),
          CONSTRAINT ck_expense_policies_published_shape CHECK (
            (status = 'published') OR (published_by IS NULL AND published_at IS NULL)
          ),
          CONSTRAINT ck_expense_policies_max_amount_nonnegative CHECK (
            max_amount IS NULL OR max_amount >= 0
          )
        )
        """
    )

    op.execute(
        """
        CREATE UNIQUE INDEX uq_expense_policies_version
          ON expense_policies(
            company_id,
            COALESCE(branch_id, '00000000-0000-0000-0000-000000000000'),
            policy_code,
            version
          )
        """
    )

    op.execute(
        """
        CREATE UNIQUE INDEX uq_expense_policies_active_code
          ON expense_policies(
            company_id,
            COALESCE(branch_id, '00000000-0000-0000-0000-000000000000'),
            policy_code
          )
          WHERE status = 'published'
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION protect_published_expense_categories()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Published Expense Category versions cannot be deleted'
              USING ERRCODE = 'check_violation';
          END IF;
          IF OLD.status = 'published' THEN
            RAISE EXCEPTION 'Published Expense Category versions are immutable'
              USING ERRCODE = 'check_violation';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_protect_published_expense_categories
          BEFORE UPDATE OR DELETE ON expense_categories
          FOR EACH ROW
          EXECUTE FUNCTION protect_published_expense_categories()
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_overlapping_expense_categories()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF NEW.status = 'published' THEN
            IF EXISTS (
              SELECT 1 FROM expense_categories
              WHERE company_id = NEW.company_id
                AND category_code = NEW.category_code
                AND status = 'published'
                AND expense_category_version_id <> NEW.expense_category_version_id
                AND effective_from <= COALESCE(NEW.effective_to, '9999-12-31'::date)
                AND COALESCE(effective_to, '9999-12-31'::date) >= NEW.effective_from
            ) THEN
              RAISE EXCEPTION 'Published Expense Category effective ranges must not overlap'
                USING ERRCODE = 'check_violation';
            END IF;
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_prevent_overlapping_expense_categories
          BEFORE INSERT OR UPDATE ON expense_categories
          FOR EACH ROW
          EXECUTE FUNCTION prevent_overlapping_expense_categories()
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION protect_published_expense_policies()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Published Expense Policy versions cannot be deleted'
              USING ERRCODE = 'check_violation';
          END IF;
          IF OLD.status = 'published' THEN
            RAISE EXCEPTION 'Published Expense Policy versions are immutable'
              USING ERRCODE = 'check_violation';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_protect_published_expense_policies
          BEFORE UPDATE OR DELETE ON expense_policies
          FOR EACH ROW
          EXECUTE FUNCTION protect_published_expense_policies()
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_overlapping_expense_policies()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF NEW.status = 'published' THEN
            IF EXISTS (
              SELECT 1 FROM expense_policies
              WHERE company_id = NEW.company_id
                AND policy_code = NEW.policy_code
                AND COALESCE(branch_id, '00000000-0000-0000-0000-000000000000')
                    = COALESCE(NEW.branch_id, '00000000-0000-0000-0000-000000000000')
                AND status = 'published'
                AND expense_policy_version_id <> NEW.expense_policy_version_id
                AND effective_from <= COALESCE(NEW.effective_to, '9999-12-31'::date)
                AND COALESCE(effective_to, '9999-12-31'::date) >= NEW.effective_from
            ) THEN
              RAISE EXCEPTION 'Published Expense Policy effective ranges must not overlap'
                USING ERRCODE = 'check_violation';
            END IF;
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_prevent_overlapping_expense_policies
          BEFORE INSERT OR UPDATE ON expense_policies
          FOR EACH ROW
          EXECUTE FUNCTION prevent_overlapping_expense_policies()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_prevent_overlapping_expense_policies ON expense_policies"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_overlapping_expense_policies()")
    op.execute("DROP TRIGGER IF EXISTS trg_protect_published_expense_policies ON expense_policies")
    op.execute("DROP FUNCTION IF EXISTS protect_published_expense_policies()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_prevent_overlapping_expense_categories ON expense_categories"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_overlapping_expense_categories()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_protect_published_expense_categories ON expense_categories"
    )
    op.execute("DROP FUNCTION IF EXISTS protect_published_expense_categories()")
    op.execute("DROP INDEX IF EXISTS uq_expense_policies_active_code")
    op.execute("DROP INDEX IF EXISTS uq_expense_policies_version")
    op.execute("DROP INDEX IF EXISTS uq_expense_categories_active_code")
    op.execute("DROP TABLE IF EXISTS expense_policies")
    op.execute("DROP TABLE IF EXISTS expense_categories")
