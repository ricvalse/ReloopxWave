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
  /**
   * Optional override to retrieve the Bearer token for backend API calls.
   * Required during agency→merchant impersonation, where `supabase.auth.getSession()`
   * returns null (no Supabase auth session) but the HS256 impersonation token lives
   * in a cookie. When omitted, hooks fall back to `supabase.auth.getSession()`.
   */
  getAccessToken?: () => Promise<string | null>;
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
  /**
   * Show the lead-centric detail panel (right rail / mobile sheet). Defaults to
   * true. Set false to render the classic two-pane inbox without the panel.
   */
  customerDetailEnabled?: boolean;
  /**
   * Show the GDPR/DSAR actions (export + erase lead data) inside the detail
   * panel. Off by default — only the merchant portal turns it on, since DSAR
   * fulfilment is a per-merchant responsibility (the admin inbox is read-only).
   */
  dsarEnabled?: boolean;
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
