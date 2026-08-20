# Migration and rollback runbook

This runbook covers Alembic migration, downgrade, re-upgrade, and rollback
procedures for TradeFlow ERP.

## Before any change

- Run the full test suite on the branch.
- Verify migration head matches deployed environments:
  `uv run alembic -c apps/api/alembic.ini current`
- Take a logical backup of the target database before applying migrations in
  staging or production.

## Apply migrations

```bash
uv run alembic -c apps/api/alembic.ini upgrade head
```

## Verify deterministic backfill

If a migration includes a backfill:

1. Record row counts and control totals before the migration.
2. Run the migration.
3. Compare counts and totals after the migration.
4. Downgrade one revision and re-upgrade to confirm the backfill is idempotent.

```bash
uv run alembic -c apps/api/alembic.ini downgrade -1
uv run alembic -c apps/api/alembic.ini upgrade head
```

## Rollback criteria

Rollback the database when:

- A deployed migration introduces an invariant violation detected by tests or
  health checks.
- Business stakeholders reject a behavior change.
- The staging cutover rehearsal fails reconciliation.

## Rollback procedure

1. Stop application containers to prevent new writes.
2. Restore the pre-migration logical backup, or run Alembic downgrade to the
   previous revision if no data-loss migration was applied.
3. Re-run health checks and a focused subset of contract tests.
4. Notify operators and update the incident log.

## Immutable data

TradeFlow does not support direct balance edits. Corrections are posted as
linked reversals and replacements. A rollback must not delete immutable ledger
rows; instead, restore from backup and replay valid events.

## Recovery after rollback

1. Identify the root cause of the failed migration.
2. Fix the migration or application code.
3. Re-run the migration in a fresh staging copy.
4. Re-run the full CI pipeline and reconciliation tests.
5. Schedule a new deployment window.
