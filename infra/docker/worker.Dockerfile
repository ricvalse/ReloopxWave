# Consolidated ARQ worker.
#
# Build context: repo root. In Railway: Dockerfile Path
# `infra/docker/worker.Dockerfile`. See infra/railway/README.md.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl build-essential libxml2 libxslt1.1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /bin/uv

WORKDIR /app

COPY backend/pyproject.toml backend/uv.lock ./
COPY backend/services ./services
COPY backend/libs ./libs
COPY backend/workers ./workers

# --all-packages installs every workspace member into the venv. Without it,
# uv only installs the workspace root (which has no runtime deps), so `arq`
# never lands in /app/.venv/bin.
RUN uv sync --frozen --no-dev --all-packages

COPY infra/docker/worker-entrypoint.sh /usr/local/bin/worker-entrypoint.sh
RUN chmod +x /usr/local/bin/worker-entrypoint.sh

ENV PATH="/app/.venv/bin:${PATH}"

CMD ["/usr/local/bin/worker-entrypoint.sh"]
