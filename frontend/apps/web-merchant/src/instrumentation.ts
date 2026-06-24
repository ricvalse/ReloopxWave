// Next.js instrumentation hook — loads the right Sentry config per runtime.
// `@sentry/nextjs` also re-exports `captureRequestError` for `onRequestError`,
// which forwards nested RSC/render errors that the boundary would otherwise eat.
export async function register() {
  if (process.env.NEXT_RUNTIME === 'nodejs') {
    await import('../sentry.server.config');
  }
  if (process.env.NEXT_RUNTIME === 'edge') {
    await import('../sentry.edge.config');
  }
}

export { captureRequestError as onRequestError } from '@sentry/nextjs';
