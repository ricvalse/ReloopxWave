'use client';

import { ThemeProvider, Toaster } from '@reloop/ui';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';

export function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            // Data is kept fresh by Supabase Realtime + explicit invalidation on
            // mutation, so a tab refocus shouldn't trigger a full refetch storm
            // (the single biggest source of redundant /resolved fetches).
            staleTime: 60_000,
            gcTime: 5 * 60_000,
            refetchOnWindowFocus: false,
            refetchOnReconnect: true,
            retry: 1,
          },
        },
      }),
  );
  return (
    <ThemeProvider attribute="class" defaultTheme="dark" enableSystem disableTransitionOnChange>
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      <Toaster />
    </ThemeProvider>
  );
}
