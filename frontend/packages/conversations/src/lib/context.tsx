'use client';

import type { SupabaseClient } from '@supabase/supabase-js';
import { createContext, useContext, type ReactNode } from 'react';

// Loose-typed alias so callers passing a `SupabaseClient<Database>` from
// `@reloop/supabase-client` are assignable. The package only uses the runtime
// query/realtime/auth methods; Database typing happens at the app boundary.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type AnySupabaseClient = SupabaseClient<any, any, any>;

export interface ConversationsContextValue {
  /** Browser-side Supabase client with the user's session. */
  supabase: AnySupabaseClient;
  /** API base URL for POSTing messages — typically NEXT_PUBLIC_API_BASE_URL. */
  apiBaseUrl: string;
  /** Optional merchant filter for the admin inbox (when set, the list rail is filtered). */
  merchantFilter?: string | null;
  /**
   * Pass-through whether the user can compose. False on the admin inbox or when
   * the per-merchant `composer_enabled` flag is off.
   */
  composerEnabled?: boolean;
  /** Cosmetic: shows merchant name on each thread row in the admin inbox. */
  adminMode?: boolean;
  /**
   * Merchant-wide bot auto-reply master switch (mirror of
   * `bot_configs.overrides.bot.auto_reply_enabled`). Read in the thread header
   * so the per-thread switch can disable itself + show why when the master is
   * off. The actual gate lives server-side in ConversationService.
   */
  merchantAutoReplyEnabled?: boolean;
  /** Called when the user flips the merchant master from inside the workspace. */
  onMerchantAutoReplyChange?: (enabled: boolean) => void;
}

const ConversationsContext = createContext<ConversationsContextValue | null>(null);

export function ConversationsProvider({
  children,
  ...value
}: ConversationsContextValue & { children: ReactNode }) {
  return <ConversationsContext.Provider value={value}>{children}</ConversationsContext.Provider>;
}

export function useConversationsContext() {
  const ctx = useContext(ConversationsContext);
  if (!ctx) {
    throw new Error('useConversationsContext must be used inside <ConversationsProvider>');
  }
  return ctx;
}
