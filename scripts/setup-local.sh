#!/usr/bin/env bash
# One-shot local bootstrap. Idempotent — safe to re-run.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

echo "⇢ Checking prerequisites"
command -v node >/dev/null || { echo "Node 20+ required"; exit 1; }
command -v pnpm >/dev/null || { echo "pnpm 10+ required — corepack enable"; exit 1; }
command -v uv >/dev/null || { echo "uv required — https://github.com/astral-sh/uv"; exit 1; }
command -v supabase >/dev/null || echo "⚠︎  Supabase CLI missing — skip local DB step if you don't need it"

if [[ ! -f .env ]]; then
  echo "⇢ Creating .env from .env.example"
  cp .env.example .env
  echo "   Fill in the Supabase / OpenAI / WhatsApp values before continuing."
fi

echo "⇢ Installing frontend deps"
(cd frontend && pnpm install --frozen-lockfile=false)

echo "⇢ Syncing backend workspace (all members)"
# --all-packages installs every workspace member's deps into the shared venv.
# Without it, plain `uv sync` only installs the root project — which has no
# runtime deps — and `.venv/bin` comes out empty (no alembic, arq, uvicorn).
(cd backend && uv sync --all-packages)

echo "✓ Setup complete. Next:"
echo "    (term 1) cd backend && uv run uvicorn api.main:app --reload"
echo "    (term 2) cd frontend && pnpm dev"
