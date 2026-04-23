import path from 'node:path';
import { fileURLToPath } from 'node:url';
import type { NextConfig } from 'next';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Self-contained build for Docker/Railway — ships a minimal node_modules +
  // server.js under .next/standalone/. outputFileTracingRoot must point at the
  // monorepo root so Next traces workspace packages correctly.
  output: 'standalone',
  outputFileTracingRoot: path.join(__dirname, '../../'),
  transpilePackages: [
    '@reloop/ui',
    '@reloop/api-client',
    '@reloop/supabase-client',
    '@reloop/config',
  ],
  typedRoutes: true,
};

export default nextConfig;
