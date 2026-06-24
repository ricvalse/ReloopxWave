// Sentry browser SDK. Runs in the client bundle. No-op when the DSN is unset so
// local `pnpm dev` never ships events upstream. The release SHA is injected by
// Railway/Vercel at build time (NEXT_PUBLIC_SENTRY_RELEASE) and mirrors the
// backend release tagging in shared.observability.
import * as Sentry from '@sentry/nextjs';

const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;

if (dsn) {
  Sentry.init({
    dsn,
    environment: process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT ?? process.env.NODE_ENV,
    release: process.env.NEXT_PUBLIC_SENTRY_RELEASE,
    // Keep trace sampling low; turn it up via env when debugging.
    tracesSampleRate: process.env.NODE_ENV === 'production' ? 0.05 : 1.0,
    sendDefaultPii: false,
  });
}
