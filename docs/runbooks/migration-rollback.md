# Runbook — Migration rollback

Alembic migrations run automatically in `infra/docker/api-entrypoint.sh`. If a migration corrupts production state, this is how you get back.

## Preamble

Alembic downgrades are **not** a safety net for data-destructive migrations. If a migration dropped a column, `downgrade()` will recreate the column with defaults but cannot resurrect the data. Always evaluate whether a downgrade is safe **before** you run it.

## Decision flow

- **Migration has `downgrade()` implemented and the change is schema-only (no data rewrite)** → run downgrade.
- **Migration rewrote data** (e.g. backfill, enum rename) → do NOT downgrade. Restore from PITR (see `supabase-restore-drill.md`) to a timestamp just before the bad migration.
- **Migration failed mid-way** → Alembic only marks `alembic_version` on success, so the DB is in a partial state. Downgrade will refuse; restore from PITR.

## Downgrade steps (schema-only case)

1. Disable the API's auto-migrate so it doesn't re-run the migration on restart:
   ```bash
   railway variables set RUN_MIGRATIONS=0 --service api
   ```
2. Scale workers to 0 (they'll fail on missing schema otherwise):
   ```bash
   railway service scale worker 0
   ```
3. Run downgrade:
   ```bash
   railway run --service api uv run alembic downgrade -1
   ```
4. Verify:
   ```bash
   railway run --service api uv run alembic current
   # should show the previous revision
   ```
5. Turn migrations back on + restore worker replicas:
   ```bash
   railway variables set RUN_MIGRATIONS=1 --service api
   railway service scale worker 1
   ```

## PITR path (data-destructive case)

Follow `supabase-restore-drill.md` to restore to a timestamp **before** the migration ran. Coordinate with the team because **prod writes after that timestamp will be lost** unless you replay them from logs.

## Avoiding this in the first place

- Review every new migration for whether its `downgrade()` is honest. If it can't truly reverse the change, write a comment saying so.
- For data-destructive changes, ship the migration as `data_migrations/YYYYMMDD_*.py` run manually — keep `alembic upgrade head` schema-only so auto-deploy stays safe.
- Always test migrations against a PITR-restored copy of prod before merging (the restore drill pays for itself here).
