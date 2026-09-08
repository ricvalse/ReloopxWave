#!/usr/bin/env sh
# API container entrypoint.
#
# 1. Run Alembic migrations against the configured DB (con ritentativi, sotto).
# 2. Hand off to uvicorn bound to Railway's $PORT (defaults to 8000 locally).
#
# NB: qui c'era scritto che Alembic prende un advisory lock per evitare che due
# repliche corrano sulla stessa migrazione. Non è vero — `migrations/env.py` non
# ne prende nessuno, apre una connessione `NullPool` e via. Serializza di fatto
# `alembic_version`, ma non contarci come se fosse un lock esplicito.
#
# Migrations on every boot are intentional: Railway has no native release phase,
# and a no-op upgrade is fast (microseconds when there's nothing to apply).
# If you'd rather decouple, set RUN_MIGRATIONS=0 and run `railway run --service
# api alembic upgrade head` manually before promoting.

set -e

# Crash-on-boot backoff. Railway restarts failed containers immediately, which
# — combined with Supavisor's ECIRCUITBREAKER on repeated auth failures — can
# lock the whole project out of the DB for ~15 minutes. Sleeping before the
# first DB touch gives a bad deploy time to be noticed and rolled back before
# it hammers the pooler. Override with MIGRATION_BACKOFF_SECONDS=0 if needed.
BACKOFF="${MIGRATION_BACKOFF_SECONDS:-5}"
if [ "${BACKOFF}" -gt 0 ] 2>/dev/null; then
  sleep "${BACKOFF}"
fi

# Le migrazioni si ritentano, invece di far morire il container al primo errore.
#
# Il motivo è concreto. Il pooler Supabase in session mode concede 15 client in
# tutto, e durante un deploy sono vivi insieme il container vecchio (che sta
# ancora servendo), il worker e quello nuovo. Se in quell'istante non c'è uno
# slot libero, `alembic upgrade head` esce con EMAXCONNSESSION, `set -e` uccide
# il container, Railway lo riavvia e si ricomincia — finché il deploy finisce in
# FAILED pur essendo tutto sano nel codice. È successo tre volte di fila
# l'08/09/2026, e dai log sembrava un problema di migrazione: non lo era.
#
# La causa di fondo è il dimensionamento del pool (ora 2+3 per processo invece
# di 5+10 — vedi il commento in `shared.settings`); questo ciclo copre la
# finestra in cui i container si sovrappongono.
#
# Il ciclo è LIMITATO di proposito: una migrazione davvero rotta fallisce tutti
# i tentativi e l'entrypoint esce comunque diverso da zero. Non si maschera un
# errore, si assorbe una contesa.
run_migrations() {
  attempts="${MIGRATION_MAX_ATTEMPTS:-6}"
  delay="${MIGRATION_RETRY_DELAY:-5}"
  i=1
  while [ "${i}" -le "${attempts}" ]; do
    echo "▶ alembic upgrade head (tentativo ${i}/${attempts})"
    if alembic upgrade head; then
      return 0
    fi
    if [ "${i}" -eq "${attempts}" ]; then
      echo "✖ migrazioni fallite dopo ${attempts} tentativi" >&2
      return 1
    fi
    echo "… nuovo tentativo fra ${delay}s"
    sleep "${delay}"
    delay=$((delay * 2))
    i=$((i + 1))
  done
}

if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
  run_migrations
else
  echo "▶ skipping migrations (RUN_MIGRATIONS=0)"
fi

PORT="${PORT:-8000}"
WORKERS="${WEB_CONCURRENCY:-2}"
echo "▶ uvicorn on :${PORT} (workers=${WORKERS})"
exec uvicorn api.main:app \
  --host 0.0.0.0 \
  --port "${PORT}" \
  --workers "${WORKERS}" \
  --proxy-headers \
  --forwarded-allow-ips='*'
