# Migration 0010: tracked-stock picking rollout and rollback

Migration `0010_tracked_stock_picking` is an additive, forward-only production
migration after the first Pick is posted. Pick evidence and its paired stock
movements are immutable business records and must not be deleted to make a
schema downgrade succeed.

## Before rollout

1. Back up PostgreSQL and record the backup identifier in the deployment log.
2. Confirm the application is at migration `0009_prepaid_payment_clearance`.
3. Run the migration rehearsal against a restored production snapshot.
4. Confirm there are no duplicate `(sales_order_id, reservation_generation)`
   Fulfillment Orders. Migration 0010 widens that key with `warehouse_id` but a
   later downgrade can restore the old key only while the old invariant holds.
5. Deploy migration 0010 before enabling the picking application release.

## Production containment and application recovery

The previous 0009 API binary is not compatible with schema 0010: it requires a
different Alembic head and does not populate the new mandatory stock-movement
custody fields. Do not deploy that binary over schema 0010.

If the picking release is unhealthy, set
`TRADEFLOW_PICKING_ENABLED=false` on the current API release and restart the API
instances. The tested kill switch removes barcode resolution, picking context,
Pick posting/history, and Pick reversal routes while the earlier ERP workflows
remain available. Stop or hide the picking web/mobile entry points at the same
time. Confirm those API routes return `route_not_found`, then ship a forward fix
on schema 0010 and re-enable the flag after verification.

Do not run `alembic downgrade` after any row exists in `pick_postings`. Preserve
the immutable Pick ledger and recover by forward application release.

## Database rollback before first use

A database downgrade is allowed only when no Pick has been posted and the old
Fulfillment Order uniqueness invariant still holds. Stop all writers, verify:

```sql
SELECT count(*) AS pick_count FROM pick_postings;

SELECT sales_order_id, reservation_generation, count(*)
FROM fulfillment_orders
GROUP BY sales_order_id, reservation_generation
HAVING count(*) > 1;
```

Both queries must return zero offending rows. Then roll back the application
and run `alembic downgrade 0009_prepaid_payment_clearance`. The migration also
enforces these checks and aborts rather than discarding operational evidence.

## Recovery verification

After an application rollback or forward fix, verify API health, worker health,
Fulfillment Order reads, inventory projection rebuild parity, and that every
posted Pick still has one available-out and one staging-in movement per Pick
line (or allocation segment). Record the verification and the decision to
resume Pick traffic in the deployment log.
