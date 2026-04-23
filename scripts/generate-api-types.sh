#!/usr/bin/env bash
# Regenerate the OpenAPI-typed client in frontend/packages/api-client.
#
# Usage:
#   ./scripts/generate-api-types.sh                   # offline: import the FastAPI app, dump openapi()
#   OPENAPI_SOURCE=http://127.0.0.1:8000/openapi.json \
#     ./scripts/generate-api-types.sh                 # scrape a running backend
#   OPENAPI_SOURCE=https://api-staging.reloop.example/openapi.json \
#     ./scripts/generate-api-types.sh                 # use a deployed schema
#
# The offline mode avoids needing Postgres + Redis at build time: we only need
# the FastAPI route tree, not a live lifespan. CI and the openapi-drift check
# both default to it for that reason.
#
# CI must run this and fail if `frontend/packages/api-client/src/generated.ts` has drifted.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_FILE="${REPO_ROOT}/frontend/packages/api-client/src/generated.ts"
OPENAPI_SOURCE="${OPENAPI_SOURCE:-offline}"

TMP="$(mktemp -t openapi.XXXXXX.json)"
BACKEND_PID=""
cleanup() {
  rm -f "${TMP}"
  if [[ -n "${BACKEND_PID}" ]] && kill -0 "${BACKEND_PID}" 2>/dev/null; then
    kill "${BACKEND_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

if [[ "${OPENAPI_SOURCE}" == "offline" ]]; then
  echo "⇢ Extracting OpenAPI schema via create_app().openapi() (offline mode)"
  (
    cd "${REPO_ROOT}/backend"
    uv run python -c "import json, sys; from api.main import create_app; sys.stdout.write(json.dumps(create_app().openapi()))"
  ) > "${TMP}"
else
  echo "⇢ Fetching OpenAPI schema from ${OPENAPI_SOURCE}"
  if [[ "${OPENAPI_SOURCE}" == http://127.0.0.1:8000/* ]]; then
    if ! curl -fsS --max-time 1 "${OPENAPI_SOURCE}" >/dev/null 2>&1; then
      echo "⇢ Local backend not running — booting uvicorn for codegen"
      (
        cd "${REPO_ROOT}/backend"
        uv run uvicorn api.main:app --host 127.0.0.1 --port 8000 --log-level warning
      ) &
      BACKEND_PID=$!
      for _ in {1..30}; do
        if curl -fsS --max-time 1 "${OPENAPI_SOURCE}" >/dev/null 2>&1; then
          break
        fi
        sleep 1
      done
    fi
  fi
  curl -fsS "${OPENAPI_SOURCE}" -o "${TMP}"
fi

cd "${REPO_ROOT}/frontend"

echo "⇢ Writing ${TARGET_FILE}"
pnpm --silent --filter @reloop/api-client exec openapi-typescript "${TMP}" \
  --output "${TARGET_FILE}" \
  --make-paths-enum

echo "✓ Done"
