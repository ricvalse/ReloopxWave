# Runbook — Supavisor `ECIRCUITBREAKER` recovery

## Symptom

API or worker containers refuse to connect to Postgres with either:

- `OSError: [Errno 101] Network is unreachable` (happens when the DSN points at the direct IPv6 endpoint `db.<ref>.supabase.co`, and Railway egress is IPv4-only), or
- `ECIRCUITBREAKER: Too many failed login attempts, try again later` (Supavisor has fail2ban-style breaker tripped by repeated auth failures).

Once the breaker trips it stays closed for ~15 minutes, and crash-restart loops (Railway restarts failed containers every ~1s) keep re-tripping it indefinitely.

## Immediate stop-the-bleed

1. Scale the offending Railway service to **0 replicas** so the restart loop stops hammering Supavisor:
   ```bash
   railway up --service api --detach  # confirm service is running
   # …then in the Railway dashboard, set replicas: 0 on api + worker
   ```
2. Wait 15 minutes or reset the Supabase DB password (either clears the breaker state).

## Fix the DSN

The only supported connection string is the **session pooler**, with the project-ref prefix on the username:

```
postgresql+asyncpg://postgres.<project_ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres
```

- Project ref lives in `Settings → Database → Connection string` on Supabase.
- Username **must** carry the `postgres.<ref>` dotted suffix, otherwise Supavisor responds `InvalidPasswordError`.
- Use **session mode (port 5432)**, not transaction mode (6543) — our code uses prepared statements and `LISTEN/NOTIFY`.
- `+asyncpg` driver prefix required by SQLAlchemy async.

Set it in Railway service env:

```bash
railway variables set SUPABASE_DB_URL="postgresql+asyncpg://postgres.<ref>:<pw>@aws-0-<region>.pooler.supabase.com:5432/postgres" --service api
railway variables set SUPABASE_DB_URL="postgresql+asyncpg://..." --service worker
```

Set replicas back to 1 on both services.

## Verify

```bash
railway logs --service api | head
# expect: "Uvicorn running on http://0.0.0.0:8080" and no asyncpg stacktraces
curl https://<api-host>/health
# expect: {"status":"ok","environment":"production"}
```

## Avoidance

`infra/docker/api-entrypoint.sh` already sleeps `MIGRATION_BACKOFF_SECONDS` (default 5) before the first DB touch; do not remove that — it is what slows the restart cascade enough to avoid tripping the breaker on a genuine transient DNS flap.
