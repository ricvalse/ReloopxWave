'use client';

// PostHog product-analytics provider. Initialises the browser SDK once and
// exposes it via context. No-op when NEXT_PUBLIC_POSTHOG_KEY is unset, so local
// dev and preview builds without a key never emit events. Host defaults to the
// EU cloud to match the backend (`shared.observability` → eu.posthog.com).
import posthog from 'posthog-js';
import { PostHogProvider as PHProvider } from 'posthog-js/react';
import { useEffect, type ReactNode } from 'react';

const KEY = process.env.NEXT_PUBLIC_POSTHOG_KEY;
const HOST = process.env.NEXT_PUBLIC_POSTHOG_HOST ?? 'https://eu.posthog.com';

export function PostHogProvider({ children }: { children: ReactNode }) {
  useEffect(() => {
    if (!KEY || typeof window === 'undefined') return;
    if (posthog.__loaded) return;
    posthog.init(KEY, {
      api_host: HOST,
      // Manual pageviews: App Router has no router event we can hook here without
      // extra wiring; capture the initial load and let callers send the rest.
      capture_pageview: false,
      capture_pageleave: true,
      persistence: 'localStorage+cookie',
    });
  }, []);

  if (!KEY) return <>{children}</>;
  return <PHProvider client={posthog}>{children}</PHProvider>;
}
