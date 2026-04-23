# Runbook — Supabase PITR restore drill

Architecture doc §13.6 commits to a quarterly PITR drill. Treat this as a hard gate: if the drill has not run in the last 90 days, scheduling it is higher priority than any feature work.

## Goal

Prove that we can recover a full working Supabase project from point-in-time to a new project and have the backend (API + worker) talk to it without changing any code.

## Pre-drill

- Supabase Pro plan on the prod project (PITR requires Pro).
- `pg_dump`-equivalent access via the Supabase dashboard.
- A scratch Railway project (or a `staging` environment) where we can point the restored DB.

## Procedure

1. **Pick a target timestamp** ~1 hour ago.
2. In the Supabase dashboard, `Database → Backups → PITR → Restore` to the timestamp. Supabase provisions a **new** project (does not overwrite prod). Record the new `<ref>`.
3. Wait for migrations to settle in the new project (they're copied over as part of restore). Verify:
   ```sql
   select count(*) from tenants;
   select count(*) from merchants;
   select count(*) from kb_chunks;
   ```
4. In a scratch Railway service, set `SUPABASE_DB_URL` to the **new** project's session-pooler DSN (see `ecircuitbreaker-recovery.md` for the exact format).
5. Boot API + worker. `curl $api/health` → 200.
6. Sanity check a merchant: `GET /analytics/merchant/kpis?merchant_id=<known>` → numbers match what you'd expect for the target timestamp.

## Rollback plan

The restored project is a new Supabase project. To roll back, just stop pointing Railway at the restored DSN. Prod is untouched.

## Clean-up

- Delete the restored Supabase project (it's billed) once the drill passes.
- Record the drill date + result in `docs/runbooks/drill-log.md` (create that file if absent).

## Failure modes seen

- **KEK mismatch**: if you restore into a staging backend with a different `INTEGRATIONS_KEK_BASE64`, every `integrations` row decrypts to garbage. Either copy prod's KEK into the drill env (and destroy it afterwards) or accept that integrations liveness checks will 500.
- **Supavisor region mismatch**: the restored project may land in a different AWS region than the old one. Update the DSN's `aws-0-<region>` host segment accordingly.
