#!/usr/bin/env bash
# Regenerate the OpenAPI-typed client in frontend/packages/api-client.
#
# Usage:
#   ./scripts/generate-api-types.sh              # boot backend locally and scrape openapi.json
#   OPENAPI_SOURCE=https://api-staging.reloop.example/openapi.json \
#     ./scripts/generate-api-types.sh            # use a deployed schema instead
#
# CI must run this and fail if `frontend/packages/api-client/src/generated.ts` has drifted.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_FILE="${REPO_ROOT}/frontend/packages/api-client/src/generated.ts"
OPENAPI_SOURCE="${OPENAPI_SOURCE:-http://127.0.0.1:8000/openapi.json}"

cd "${REPO_ROOT}/frontend"

echo "⇢ Fetching OpenAPI schema from ${OPENAPI_SOURCE}"
BACKEND_PID=""
cleanup() {
  if [[ -n "${BACKEND_PID}" ]] && kill -0 "${BACKEND_PID}" 2>/dev/null; then
    kill "${BACKEND_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

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

TMP="$(mktemp -t openapi.XXXXXX.json)"
curl -fsS "${OPENAPI_SOURCE}" -o "${TMP}"

echo "⇢ Writing ${TARGET_FILE}"
pnpm --silent --filter @reloop/api-client exec openapi-typescript "${TMP}" \
  --output "${TARGET_FILE}" \
  --make-paths-enum

rm -f "${TMP}"
echo "✓ Done"
