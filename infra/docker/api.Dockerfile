# FastAPI API service.
#
# Build context: repo root (so `backend/` and `infra/` are both reachable).
# In Railway: set Dockerfile Path to `infra/docker/api.Dockerfile` and leave
# Build Context empty (defaults to repo root). See infra/railway/README.md.

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

# build-essential + libxml2/libxslt for python-docx (lxml C ext).
# ca-certificates for outbound TLS to Supabase / OpenAI / Meta.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl build-essential libxml2 libxslt1.1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /bin/uv

WORKDIR /app

# Copy lockfile + workspace members first so Docker can cache the install layer.
COPY backend/pyproject.toml backend/uv.lock ./
COPY backend/services ./services
COPY backend/libs ./libs
COPY backend/workers ./workers

# --all-packages installs every workspace member (api, ai_core, integrations,
# db, config_resolver, shared) into the venv. Without it, uv only installs the
# workspace root which has no runtime deps, so alembic / arq / fastapi never
# land in /app/.venv/bin.
RUN uv sync --frozen --no-dev --all-packages

# Migration metadata.
COPY backend/alembic.ini ./alembic.ini

# Entrypoint runs migrations then uvicorn on $PORT.
COPY infra/docker/api-entrypoint.sh /usr/local/bin/api-entrypoint.sh
RUN chmod +x /usr/local/bin/api-entrypoint.sh

ENV PATH="/app/.venv/bin:${PATH}"
EXPOSE 8000

CMD ["/usr/local/bin/api-entrypoint.sh"]
