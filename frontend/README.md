# Reloop AI — Frontend

Turborepo + pnpm workspaces. Two Next.js 15 apps (admin agency + merchant portal) sharing packages.

## Layout

```
frontend/
├── apps/
│   ├── web-admin/        # port 3000 — dashboard agenzia
│   └── web-merchant/     # port 3001 — portal merchant
└── packages/
    ├── ui/               # shadcn-style primitives + KPICard, AppShell, PageHeader
    ├── api-client/       # typed OpenAPI client (generated.ts — do not edit by hand)
    ├── supabase-client/  # SSR-safe Supabase wrapper (swap surface, section 15)
    └── config/           # zod-validated env parsers, shared brand
```

## Develop

```bash
pnpm install                        # first time
pnpm dev                            # both apps in parallel
pnpm dev --filter web-admin         # only admin
pnpm dev --filter web-merchant      # only merchant
pnpm lint
pnpm typecheck
pnpm test
pnpm build
```

## Env

Each app reads from `.env.local`. Required public keys:

```
NEXT_PUBLIC_SUPABASE_URL=
NEXT_PUBLIC_SUPABASE_ANON_KEY=
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

Server-only (don't prefix with `NEXT_PUBLIC_`):

```
SUPABASE_SERVICE_ROLE_KEY=   # only if you add a route that needs to bypass RLS
```

## Data access rules

Two paths, chosen by operation type (section 4.4):

- **Direct to Supabase** — auth, simple RLS-protected reads, Storage uploads, Realtime subs.
- **Through `@reloop/api-client`** — business logic (onboarding, playground, report generation, integrations setup).

Do not import `@supabase/supabase-js` directly. Always go through `@reloop/supabase-client`.

## OpenAPI types

After a FastAPI endpoint signature changes:

```bash
../scripts/generate-api-types.sh
```

Commit `packages/api-client/src/generated.ts`. CI rejects the PR if it drifts.
