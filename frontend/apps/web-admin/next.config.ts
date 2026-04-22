import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  reactStrictMode: true,
  transpilePackages: [
    '@reloop/ui',
    '@reloop/api-client',
    '@reloop/supabase-client',
    '@reloop/config',
  ],
  experimental: {
    typedRoutes: true,
  },
};

export default nextConfig;
