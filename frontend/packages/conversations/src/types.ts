export type MessageStatus = 'pending' | 'sent' | 'delivered' | 'read' | 'failed';
export type MessageDirection = 'in' | 'out';
export type MessageRole = 'user' | 'assistant' | 'agent';

export interface Conversation {
  id: string;
  merchant_id: string;
  lead_id?: string | null;
  wa_contact_phone: string | null;
  wa_phone_number_id: string | null;
  status: string;
  last_message_at: string | null;
  /** Time of the customer's last inbound — drives the 24h-window composer banner. */
  last_inbound_at?: string | null;
  message_count: number;
  /** Per-thread bot takeover. AND-ed with merchant `bot.auto_reply_enabled`. */
  auto_reply: boolean;
  /** Soft-pause with auto-resume (ISO). In the future = bot silenced until then. */
  ai_disabled_until?: string | null;
  /** Operator who took the thread over (auto-takeover or timed pause). */
  assigned_to?: string | null;
  /** Why the thread was handed off (e.g. "manual_reply", "video_message", "angry"). */
  handoff_reason?: string | null;
  /** The AI's 1-2 sentence brief for the operator on escalation. */
  handoff_summary?: string | null;
  /** When the handoff started / was resolved (ISO). */
  handoff_at?: string | null;
  handoff_resolved_at?: string | null;
  /** Agent's free-text internal note, shown in the detail panel. NULL when empty. */
  internal_note?: string | null;
  meta: Record<string, unknown> | null;
  created_at: string;
  // Hydrated client-side from the latest message in the thread
  last_message_preview?: string | null;
  last_message_role?: MessageRole | null;
  unread_count?: number;
}

/**
 * `phone` — message originated from the merchant's WhatsApp Business App on
 * their handset (360dialog Coexistence echo). `human` — composer-typed reply
 * via the web UI. `ai` — assistant turn. Other backends may omit `meta` or
 * `sender_type` entirely, hence both are optional.
 */
export type SenderType = 'phone' | 'human' | 'ai';

export interface MessageMeta {
  sender_type?: SenderType;
  [key: string]: unknown;
}

export interface Message {
  id: string;
  conversation_id: string;
  role: MessageRole;
  direction: MessageDirection;
  content: string;
  status: MessageStatus;
  client_message_id: string | null;
  wa_message_id: string | null;
  delivered_at: string | null;
  read_at: string | null;
  failed_at: string | null;
  error: Record<string, unknown> | null;
  meta?: MessageMeta | null;
  created_at: string;
}

export interface ThreadFilters {
  status?: 'open' | 'closed' | 'all';
  merchantId?: string;
  search?: string;
}

/**
 * UI-facing status filter for the inbox thread-list tabs. Decoupled from the
 * raw DB `status` string so the tabs can fold (`status` + `auto_reply`) into
 * agent-meaningful buckets without leaking DB vocabulary into the UI.
 *
 *   all          — everything
 *   active       — bot/active threads (status === 'active', bot still answering)
 *   needs_human  — escalated, no human yet (auto_reply false, unassigned): waiting on an agent
 *   managed      — a human took over (auto_reply false, assigned_to set)
 *   resolved     — anything no longer active (closed/archived/…)
 */
export type InboxFilter = 'all' | 'active' | 'needs_human' | 'managed' | 'resolved';

/**
 * Lead linked to a conversation, surfaced in the detail panel. Mirrors the
 * `leads` table columns the panel reads directly via Supabase under RLS.
 * `tags` is not a DB column — it is read defensively from `meta.tags`.
 */
export interface Lead {
  id: string;
  name: string | null;
  email: string | null;
  phone: string;
  score: number;
  score_reasons: string[];
  sentiment: string | null;
  status: string;
  pipeline_stage_id: string | null;
  meta: Record<string, unknown> | null;
}

/** A detected sales objection tied to a conversation (objections table). */
export interface Objection {
  id: string;
  category: string;
  summary: string;
  quote: string | null;
  severity: string;
  created_at: string;
}
