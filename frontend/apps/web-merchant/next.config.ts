import path from 'node:path';
import { fileURLToPath } from 'node:url';
import type { NextConfig } from 'next';
import { withSentryConfig } from '@sentry/nextjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: 'standalone',
  outputFileTracingRoot: path.join(__dirname, '../../'),
  transpilePackages: [
    '@reloop/ui',
    '@reloop/api-client',
    '@reloop/supabase-client',
    '@reloop/config',
    '@reloop/conversations',
  ],
  typedRoutes: true,
  experimental: {
    optimizePackageImports: ['lucide-react', 'framer-motion', '@radix-ui/react-icons'],
  },
};

// Wrap with Sentry: uploads source maps at build (when SENTRY_AUTH_TOKEN is set)
// and instruments the build. Silent in CI; a missing auth token only skips the
// upload, it does not fail the build.
export default withSentryConfig(nextConfig, {
  org: process.env.SENTRY_ORG,
  project: process.env.SENTRY_PROJECT,
  silent: !process.env.CI,
  // Hide source maps from the client bundle after upload.
  sourcemaps: { deleteSourcemapsAfterUpload: true },
  // Tunnel Sentry requests through the app to dodge ad-blockers.
  tunnelRoute: '/monitoring',
  disableLogger: true,
});
