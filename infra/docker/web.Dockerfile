# Shared Dockerfile for both Next.js apps (web-admin, web-merchant).
#
# Build context: repo root. On Railway set:
#   Dockerfile Path: infra/docker/web.Dockerfile
#   Build Args:      APP_NAME=web-admin   (or web-merchant)
#
# Why one Dockerfile parameterised by APP_NAME: both apps share every toolchain
# choice (pnpm workspace, Next 15 standalone, workspace packages) — duplicating
# two Dockerfiles means two places to drift.
#
# NEXT_PUBLIC_* vars are inlined into the client JS bundle at build time, so
# they must be available as build ARGs (Railway forwards matching service
# variables during `docker build`). Runtime-only vars don't need ARG lines.

# ---- base ----
FROM node:20-alpine AS base
RUN corepack enable && corepack prepare pnpm@10 --activate
WORKDIR /app

# ---- deps ----
# Install node_modules against just the package manifests so the install layer
# caches across source changes.
FROM base AS deps

COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml frontend/turbo.json frontend/tsconfig.base.json ./
COPY frontend/apps/web-admin/package.json ./apps/web-admin/
COPY frontend/apps/web-merchant/package.json ./apps/web-merchant/
COPY frontend/packages/api-client/package.json ./packages/api-client/
COPY frontend/packages/config/package.json ./packages/config/
COPY frontend/packages/conversations/package.json ./packages/conversations/
COPY frontend/packages/supabase-client/package.json ./packages/supabase-client/
COPY frontend/packages/ui/package.json ./packages/ui/

RUN pnpm install --frozen-lockfile

# ---- builder ----
FROM base AS builder
ARG APP_NAME
ARG NEXT_PUBLIC_SUPABASE_URL
ARG NEXT_PUBLIC_SUPABASE_ANON_KEY
ARG NEXT_PUBLIC_API_BASE_URL
ARG NEXT_PUBLIC_SENTRY_DSN
ARG NEXT_PUBLIC_POSTHOG_KEY

ENV NEXT_PUBLIC_SUPABASE_URL=${NEXT_PUBLIC_SUPABASE_URL} \
    NEXT_PUBLIC_SUPABASE_ANON_KEY=${NEXT_PUBLIC_SUPABASE_ANON_KEY} \
    NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL} \
    NEXT_PUBLIC_SENTRY_DSN=${NEXT_PUBLIC_SENTRY_DSN} \
    NEXT_PUBLIC_POSTHOG_KEY=${NEXT_PUBLIC_POSTHOG_KEY} \
    NEXT_TELEMETRY_DISABLED=1

COPY --from=deps /app/node_modules ./node_modules
COPY --from=deps /app/apps ./apps
COPY --from=deps /app/packages ./packages
COPY frontend/ ./

RUN test -n "${APP_NAME}" || (echo "APP_NAME build arg is required" && exit 1)
RUN pnpm --filter "${APP_NAME}" build

# ---- runner ----
# Standalone output at apps/<name>/.next/standalone/ is a self-contained tree
# rooted at the tracing root (our monorepo root), so it already includes the
# needed hoisted node_modules and workspace packages. We only need to layer in
# the static/ and public/ assets that Next keeps outside standalone.
FROM base AS runner
ARG APP_NAME
ENV APP_NAME=${APP_NAME} \
    NODE_ENV=production \
    HOSTNAME=0.0.0.0 \
    NEXT_TELEMETRY_DISABLED=1

RUN addgroup -S nextjs && adduser -S -G nextjs nextjs

COPY --from=builder --chown=nextjs:nextjs /app/apps/${APP_NAME}/.next/standalone ./
COPY --from=builder --chown=nextjs:nextjs /app/apps/${APP_NAME}/.next/static ./apps/${APP_NAME}/.next/static
COPY --from=builder --chown=nextjs:nextjs /app/apps/${APP_NAME}/public ./apps/${APP_NAME}/public

USER nextjs

# Railway sets $PORT; fall back to 3000 for local docker runs.
CMD node apps/${APP_NAME}/server.js
