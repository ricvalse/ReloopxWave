#!/usr/bin/env sh
# API container entrypoint.
#
# 1. Run Alembic migrations against the configured DB. Alembic acquires an
#    advisory lock so concurrent api replicas don't race on first deploy.
# 2. Hand off to uvicorn bound to Railway's $PORT (defaults to 8000 locally).
#
# Migrations on every boot are intentional: Railway has no native release phase,
# and a no-op upgrade is fast (microseconds when there's nothing to apply).
# If you'd rather decouple, set RUN_MIGRATIONS=0 and run `railway run --service
# api alembic upgrade head` manually before promoting.

set -e

if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
  echo "▶ alembic upgrade head"
  alembic upgrade head
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
